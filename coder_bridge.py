import json
import re
import time
import queue
from collections import deque
from threading import Thread, Lock, Timer

import websockets
from websockets.sync.server import serve as wbs_serve

from config import CONFIG, SECRETS
from logs import Logger, conversation
from eventbus import eventbus, Events
from obs import ObsApp
from debate import Debate, parse_stance

# Spoken review-navigation commands, handled without a model round trip.
# "chuck" is a common STT mishearing of "chunk".
_UNIT = r"(?:chunk|hunk|chuck|change|one)"
NAV_PATTERNS = [
    ("next", rf"(?:next(?: {_UNIT})?|go on|keep going|continue|moving on|what's next"
             rf"|(?:on|onto|on to) the next(?: {_UNIT})?"
             rf"|move (?:on|onto|on to)(?: the)?(?: next)?(?: {_UNIT})?)"),
    ("back", rf"(?:(?:go )?back|previous(?: {_UNIT})?|last {_UNIT})"),
    ("more_context", r"(?:(?:show (?:me )?)?more context|zoom out|show (?:me )?more)"),
    ("whole_file", r"(?:(?:show )?(?:me )?(?:the )?whole file|full file)"),
    ("approve", rf"(?:(?:i )?approved?(?: (?:this|the|that))?(?: {_UNIT})?"
                rf"|(?:(?:this|the|that|it) )?(?:{_UNIT} )?(?:looks|seems|sounds) (?:good|great|fine|right)(?: to me)?"
                rf"|(?:i )?(?:like|love) (?:it|this|that)(?: {_UNIT})?"
                rf"|(?:that |it )?works for me|(?:that's |that is |it's )?good to go"
                # Lone "nice"/"great"/"fine" are too easy to say to chat.
                rf"|(?:that's|that is|it's) (?:perfect|great|fine)|nice (?:work|job)"
                rf"|ship it|l\.?g\.?t\.?m\.?)"),
    # Handled locally by the review page, not sent to the channel.
    ("scroll_down", r"(?:scroll down|down a bit)"),
    ("scroll_up", r"(?:scroll up|up a bit)"),
]

VIEWER_NAV = {"scroll_down", "scroll_up"}

# Chat lines kept for pages that connect (or reconnect) mid-conversation.
CHAT_HISTORY = 200

# Dictated words reach the chat characters via the Twitch round trip, after
# the bridge has them; stay muted this long after a dictation ends.
DICTATION_UNMUTE_DELAY = 4.0

# Leading interjections that voice input tends to carry ("All right, next"),
# and trailing ones ("looks good to me too", "next please").
FILLER = re.compile(r"^(?:okay|ok|all right|alright|yeah|yes|so|um|uh|dyson)[,\s]+")
TRAILER = re.compile(r"[,\s]+(?:too|as well|then|please|thanks|dyson)$")

# Approving a proposed plan out loud ("go", "let's do it"); review-style
# approvals ("looks good to me", "I like it") count too.
PLAN_GO = (r"(?:go|go ahead|go for it|let's (?:do it|go|do this)|do it|make it so"
           r"|sounds good(?: to me)?|(?:that|the) plan (?:works|looks good)|ship it)")


def clean_utterance(text):
    """Lowercased, without trailing punctuation or leading/trailing filler,
    for anchored phrase matching."""
    cleaned = re.sub(r"[.!?,]+$", "", text.strip().lower()).strip()
    while True:
        stripped = TRAILER.sub("", FILLER.sub("", cleaned))
        if stripped == cleaned:
            return cleaned
        cleaned = stripped


def match_plan_go(text):
    """True when the utterance approves a proposed plan."""
    return bool(re.fullmatch(PLAN_GO, clean_utterance(text))) or match_nav(text) == "approve"


def match_nav(text):
    """Return the nav action for a navigation utterance, else None.

    Matching is anchored (fullmatch after stripping filler/punctuation), so
    sentences that merely contain a nav word stay review comments."""
    cleaned = clean_utterance(text)
    for action, pattern in NAV_PATTERNS:
        if re.fullmatch(pattern, cleaned):
            return action
    return None

# Instructions appended to the second reviewer's prompt (chatgpt.py 'review'
# context).  The STANCE line is parsed off before the text is spoken.
REVIEW_PROMPT = (
    "Give your own review of this hunk in one or two short spoken sentences: "
    "no code, no identifiers, no line numbers. Only raise a concern if you "
    "would actually block the change on it. Then on a new line write "
    "STANCE: approve or STANCE: concern.")
REBUTTAL_PROMPT = (
    "Reply to {coder}'s last point in one or two short spoken sentences, no "
    "code. Concede if you are convinced; otherwise make your single strongest "
    "point. Then on a new line write STANCE: agree (you accept {coder}'s "
    "position) or STANCE: disagree.")
QUESTION_PROMPT = (
    "Answer {streamer}'s question about this hunk in one or two short spoken "
    "sentences, no code.")
DEADLOCK_LINE = "we're not going to agree on this one. {streamer}, you call it."
PLAN_PROMPT = (
    "If {coder} just proposed a plan for a change, give your take in one short "
    "spoken sentence, no code: approve it, or name the single biggest concern. "
    "Then on a new line write STANCE: approve or STANCE: concern. If it isn't a "
    "plan (a question, a status update, small talk), reply with just SKIP.")
# The coder speaks a sentence at a time; a plan is done once it pauses this long.
PLAN_SETTLE = 3.0

''' WebSocket server connecting a remote Claude Code channel to this machine.

Routes the streamer's mic/keyboard to the remote session, speaks replies
through the code character's avatar, drives the review page, and publishes
CODER_STATE so other characters can react.  In review mode a second
character reviews each hunk too, and the two argue it out (debate.py).
Protocol: docs/PAIR_PROGRAMMING_PLAN.md §6. '''
class CoderBridgeApp():
    def __init__(self, chatbot_manager=None, streamer=None):
        self.log = Logger("coder")

        self.enable = CONFIG.getboolean("coder", "enable", fallback=False)
        self.address = CONFIG.get("coder", "bind_address", fallback="0.0.0.0")
        self.port = CONFIG.getint("coder", "port", fallback=9010)
        self.review_send_timeout = CONFIG.getfloat("coder", "review_send_timeout", fallback=1.0)
        self.review_scene = CONFIG.get("coder", "review_scene", fallback=None)
        self.mirror_to_twitch = CONFIG.getboolean("coder", "mirror_to_twitch", fallback=False)
        self.reviewer_name = CONFIG.get("coder", "reviewer", fallback="") or None
        self.debate_max_turns = CONFIG.getint("coder", "debate_max_turns", fallback=2)
        self.token = SECRETS.get("coder", "token", fallback=None)

        self.manager = chatbot_manager
        self.streamer = streamer
        self.avatar = chatbot_manager.tts if chatbot_manager else None

        if self.reviewer_name and self.manager:
            if (self.reviewer_name not in self.manager.chatbots
                    or self.reviewer_name == self.code_character_name()):
                self.log.warning(f"Invalid [coder] reviewer '{self.reviewer_name}'; no second reviewer")
                self.reviewer_name = None

        self.running = False
        self.server = None
        self.server_thread = None
        self.router_thread = None

        # The most recent authenticated channel session wins; older ones are
        # closed.  Viewers (review and console pages) are separate and additive.
        self.session_lock = Lock()
        self.active = None
        self.session_count = 0
        self.viewers = set()
        # Recent chat frames, replayed to each page as it connects.
        self.chat_history = deque(maxlen=CHAT_HISTORY)
        # Lines of a spoken change request being dictated, or None.
        self.dictation = None
        # The coder's lines while planning, for the second reviewer to weigh
        # in on once they settle.
        self.plan_lines = []
        self.plan_timer = None
        # The coder's latest edit diff, for pages that connect after it (the
        # console switches to the review page on the first edit).
        self.last_edit = None

        self.mode = "idle"
        self.chunk = None
        self.chunks = None
        self.activity = None
        # Readable "what the coder is doing" line from the tool hooks.
        self.activity_label = None
        self.current_hunk = None
        self.barged = False
        self.saved_scene = None
        self.obs = ObsApp() if self.review_scene else None

        # Hunks already summarized/reviewed in the current review pass, so
        # "back" doesn't re-run them.  Keyed by the channel's review id.
        self.review_id = None
        self.reviewed = set()

        # Debate state is touched from the channel reader, the router, and
        # LLM worker threads.
        self.debate_lock = Lock()
        self.debate = Debate(max_turns=self.debate_max_turns,
                             reviewer=self.reviewer_name or "reviewer",
                             coder=self.code_character_name())

        # Eventbus callbacks run under the bus lock on the publisher's
        # thread, so they only enqueue; the router thread does the work.
        self.events = queue.Queue()

        if self.enable:
            eventbus.create_subscriber(
                name="coder_bridge",
                event_types=[Events.STREAMER_LINE, Events.STREAMER_PARTIAL],
                callback=lambda event: self.events.put(event),
            )

    # --- Lifecycle ---

    def ensure_connected(self):
        if not self.enable or self.running:
            return

        if not self.token:
            self.log.error("No [coder] token in secrets.ini; not starting bridge.")
            self.enable = False
            return

        self.running = True
        self.server_thread = Thread(daemon=True, target=self.run_server)
        self.server_thread.start()
        self.router_thread = Thread(daemon=True, target=self.route_events)
        self.router_thread.start()

    def shutdown(self):
        if not self.running:
            return
        self.log.info("Shutting down coder bridge...")
        self.running = False
        self.events.put(None)
        with self.session_lock:
            sessions = ([self.active] if self.active else []) + list(self.viewers)
        for ws in sessions:
            try:
                ws.close()
            except Exception:
                pass
        if self.server:
            self.server.shutdown()

    # --- WebSocket server ---

    def run_server(self):
        self.log.info(f"Starting coder bridge on {self.address}:{self.port}...")
        try:
            with wbs_serve(self.handle_connect, self.address, self.port) as server:
                self.server = server
                server.serve_forever()
        except Exception as ex:
            self.log.error("Coder bridge server died", exc_info=ex)
            self.running = False

    def handle_connect(self, ws):
        # First frame must be a valid hello within 5s.
        try:
            raw = ws.recv(timeout=5)
            hello = json.loads(raw)
        except Exception:
            ws.close(1002, "expected hello")
            return

        if hello.get("type") != "hello" or hello.get("token") != self.token:
            self.log.warning("Rejected connection with bad hello/token")
            ws.close(1008, "bad token")
            return

        if hello.get("role") == "viewer":
            self.handle_viewer(ws)
        else:
            self.handle_channel(ws, hello)

    def handle_viewer(self, ws):
        self.log.info("Review page viewer connected")
        with self.session_lock:
            self.viewers.add(ws)
            # Recent conversation first, so a page that reloads (or the
            # console coming back from a review) picks up where it was.
            replay = list(self.chat_history)
            replay.append(self.state_message())
            if self.current_hunk:
                replay.append(self.current_hunk)
            if self.dictation is not None:
                replay.append(self.dictation_message("active"))
            if self.last_edit:
                replay.append(self.last_edit)
        with self.debate_lock:
            replay.extend({"type": "thread", **entry} for entry in self.debate.thread)
        try:
            for msg in replay:
                ws.send(json.dumps(msg))
            # Viewers listen; the request console also sends typed requests.
            # They're authenticated by the same token as the channel.
            for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "request":
                    self.handle_request(ws, str(msg.get("text", "")))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            with self.session_lock:
                self.viewers.discard(ws)
            self.log.info("Review page viewer disconnected")

    def handle_channel(self, ws, hello):
        host = hello.get("host", "?")
        self.log.info(f"Coder session connected from {host} ({hello.get('cwd', '?')})")

        with self.session_lock:
            previous = self.active
            self.active = ws
            self.session_count += 1
        if previous:
            self.log.info("Newer session takes over; closing previous")
            try:
                previous.close(1000, "superseded")
            except Exception:
                pass
        else:
            self.on_session_started()

        try:
            for raw in ws:
                try:
                    self.handle_channel_message(json.loads(raw))
                except Exception as ex:
                    self.log.error("Error handling channel message", exc_info=ex)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            with self.session_lock:
                was_active = self.active is ws
                if was_active:
                    self.active = None
            if was_active:
                self.on_session_ended()
            self.log.info(f"Coder session from {host} disconnected")

    def on_session_started(self):
        # The code character speaks for the remote session now; take it out
        # of normal LLM arbitration.
        if self.manager:
            self.manager.suppress(self.manager.code_character.character, "session")

    def on_session_ended(self):
        if self.manager:
            self.manager.unsuppress(self.manager.code_character.character, "session")
            # Nobody left to send a dictation to; don't leave everyone muted.
            if self.dictation is not None:
                self.dictation = None
                self.mute_for_dictation(False)
                self.broadcast_to_viewers({"type": "dictation", "state": "cancelled", "text": ""})
        self.set_mode("idle")

    # --- Channel -> bridge messages (§6) ---

    def handle_channel_message(self, msg):
        mtype = msg.get("type")

        if mtype == "say":
            self.handle_say(msg)
        elif mtype == "state":
            self.handle_state(msg)
        elif mtype == "hunk":
            self.handle_hunk(msg)
        elif mtype == "turn":
            self.handle_turn(msg)
        elif mtype == "vocab":
            self.handle_vocab(msg)
        elif mtype in ("activity", "review_result"):
            # An edit diff or test result from the coder's tool hooks, or a
            # finished review's verdict; only the pages show these.
            with self.session_lock:
                if msg.get("kind") == "edit":
                    self.last_edit = msg
                viewers = list(self.viewers)
            self.send_to_viewers(viewers, msg)
        else:
            self.log.warning(f"Unhandled channel message type: {mtype}")

    def code_character_name(self):
        return self.manager.code_character.character if self.manager else "dyson"

    def reviewer(self):
        """The second reviewer's ChatbotApp, or None when not configured."""
        if not self.manager or not self.reviewer_name:
            return None
        return self.manager.chatbots.get(self.reviewer_name)

    def handle_say(self, msg):
        text = msg.get("text", "")
        self.barged = False
        self.log.info(f"say: {text}")
        self.chat(self.code_character_name(), text)
        if self.mode == "plan" and self.reviewer():
            self.collect_plan_line(text)

        if self.avatar:
            self.avatar.say(text, character=self.code_character_name())

        if self.mirror_to_twitch and self.manager:
            twitch = self.manager.code_character.twitch
            if twitch and twitch.running:
                twitch.say(text)

    def handle_state(self, msg):
        previous_mode = self.mode
        self.mode = msg.get("mode", self.mode)
        self.chunk = msg.get("chunk", self.chunk)
        self.chunks = msg.get("chunks", self.chunks)
        self.activity = msg.get("avatar", self.activity)
        self.activity_label = msg.get("label", self.activity_label)

        # Hold the coder's avatar on screen (quiet ack) while its tools run;
        # release it when the turn ends (empty label) or the session is idle.
        if self.avatar and "label" in msg:
            if msg["label"] and self.mode != "idle":
                self.avatar.work(character=self.code_character_name())
            else:
                self.avatar.noack()

        eventbus.publish(Events.CODER_STATE, data=dict(msg), source="coder")
        self.broadcast_to_viewers(self.state_message())

        if previous_mode != self.mode:
            self.on_mode_change(previous_mode, self.mode)

    def set_mode(self, mode):
        if mode != self.mode:
            self.handle_state({"type": "state", "mode": mode})

    def on_mode_change(self, previous, current):
        self.log.info(f"Mode: {previous} -> {current}")

        # Faster voice turnaround during review.
        rev = self.streamer.rev if self.streamer else None
        if rev:
            if current == "review":
                rev.set_send_timeout(self.review_send_timeout)
            elif previous == "review":
                rev.set_send_timeout(None)

        # In review the bridge owns the second reviewer's voice too: no
        # ambient chat replies in the middle of a debate.
        reviewer = self.reviewer()
        if reviewer and self.manager:
            if current == "review":
                self.manager.suppress(reviewer.character, "review")
            elif previous == "review":
                self.manager.unsuppress(reviewer.character, "review")
        if previous == "review":
            self.cancel_debate()
            self.review_id = None
            self.reviewed.clear()

        # Swap OBS scenes in and out of review.  On its own thread: the OBS
        # connect/switch must not stall the channel reader (review_begin
        # sends the first hunk right behind the state change).
        if self.obs:
            Thread(daemon=True, target=self.switch_obs_scene, args=(previous, current)).start()

    def switch_obs_scene(self, previous, current):
        try:
            self.obs.ensure_connected()
            if current == "review":
                self.saved_scene = self.obs.get_current_scene_name()
                self.obs.set_current_scene_name(self.review_scene)
            elif previous == "review" and self.saved_scene:
                self.obs.set_current_scene_name(self.saved_scene)
                self.saved_scene = None
        except Exception as ex:
            self.log.error("OBS scene switch failed", exc_info=ex)

    def handle_hunk(self, msg):
        index = msg.get("index")
        self.log.info(f"hunk {index}/{msg.get('total')}: {msg.get('file')}")

        # A new review pass (re-review after revisions) starts the bookkeeping
        # over.  Older channels don't send a review id; fall back to index 1.
        review_id = msg.get("review", 1 if index == 1 else self.review_id)
        if review_id != self.review_id:
            self.review_id = review_id
            self.reviewed.clear()

        self.current_hunk = msg
        self.broadcast_to_viewers(msg)

        # Re-renders of the same hunk (more context, whole file, "back") keep
        # quiet; only the first look gets the summary and the second opinion.
        if index in self.reviewed:
            return
        self.reviewed.add(index)

        if self.avatar and msg.get("summary"):
            self.avatar.say(msg["summary"], character=self.code_character_name())
        self.begin_debate(msg)

    def handle_turn(self, msg):
        """Dyson's debate reply (spoken separately via say frames)."""
        text, stance = msg.get("text", ""), msg.get("stance")
        with self.debate_lock:
            action = self.debate.coder_said(text, stance)
        if action[0] == "ignore":
            self.log.warning("turn received with no debate awaiting the coder")
            return
        self.broadcast_to_viewers({"type": "thread", "speaker": self.code_character_name(),
                                   "text": text, "stance": stance})
        self.apply_debate_action(action)

    def handle_vocab(self, msg):
        corrections = msg.get("corrections", {})
        rev = self.streamer.rev if self.streamer else None
        if rev and corrections:
            rev.add_session_corrections(corrections)

    def state_message(self):
        return {
            "type": "state",
            "mode": self.mode,
            "chunk": self.chunk,
            "chunks": self.chunks,
            "activity": self.activity,
            "label": self.activity_label,
            "connected": self.active is not None,
            "coder": self.code_character_name(),
            "reviewer": self.reviewer_name,
        }

    # --- Second reviewer and debate (§4.3) ---

    def begin_debate(self, hunk):
        if not self.reviewer():
            return
        with self.debate_lock:
            self.debate.start(hunk)
        self.ask_reviewer_async()

    def cancel_debate(self):
        with self.debate_lock:
            was_active = self.debate.active
            self.debate.cancel()
        if was_active:
            self.log.info("Debate cancelled")
            self.broadcast_to_viewers({"type": "debate", "status": "cancelled"})

    def ask_reviewer_async(self, question=None):
        """Run the reviewer's LLM call off-thread; results tagged with the
        debate generation so a stale answer is dropped."""
        with self.debate_lock:
            generation = self.debate.generation
            hunk = self.debate.hunk or self.current_hunk
            thread = list(self.debate.thread)
        if not hunk:
            return
        Thread(daemon=True, target=self.ask_reviewer,
               args=(generation, hunk, thread, question)).start()

    def ask_reviewer(self, generation, hunk, thread, question=None):
        reviewer = self.reviewer()
        if not reviewer:
            return
        names = {"coder": self.code_character_name(),
                 "streamer": self.manager.streamer_name}
        if question:
            prompt = QUESTION_PROMPT
        elif thread:
            prompt = REBUTTAL_PROMPT
        else:
            prompt = REVIEW_PROMPT
        context = {
            "type": "review",
            "hunk": hunk,
            "thread": thread,
            "question": question,
            "coder": names["coder"],
            "reviewer": reviewer.character,
            "prompt": prompt.format(**names),
        }

        # The review page shows the reviewer's reasoning summary while she
        # works, then clears it once she has an answer (or fails).
        def on_thinking(summary):
            self.broadcast_to_viewers({"type": "thinking", "speaker": reviewer.character,
                                       "text": summary})

        on_thinking("")
        try:
            response = reviewer.chatgpt.get_response([], context, on_thinking=on_thinking)
        except Exception as ex:
            self.log.error("Reviewer LLM call failed", exc_info=ex)
            response = None
        finally:
            self.broadcast_to_viewers({"type": "thinking", "speaker": reviewer.character,
                                       "done": True})
        text, stance = parse_stance(response)

        if question:
            if text:
                self.speak_review_line(reviewer, text, None)
            return

        with self.debate_lock:
            if generation != self.debate.generation:
                self.log.info("Dropping stale reviewer result")
                return
            if not text:
                self.log.error("Reviewer gave no usable text; skipping debate")
                self.debate.cancel()
                return
            action = self.debate.reviewer_said(text, stance)
        self.speak_review_line(reviewer, text, stance)
        self.apply_debate_action(action)

    # --- Second reviewer on plans ---
    # While planning, the coder's lines are collected; once it pauses, the
    # second reviewer gets one line on the plan (or SKIPs if it wasn't one).
    # No debate: it's spoken, shown, and passed to the coder as data.

    def collect_plan_line(self, text):
        self.plan_lines.append(text)
        if self.plan_timer:
            self.plan_timer.cancel()
        self.plan_timer = Timer(PLAN_SETTLE, self.plan_settled)
        self.plan_timer.daemon = True
        self.plan_timer.start()

    def plan_settled(self):
        plan = " ".join(self.plan_lines).strip()
        self.plan_lines = []
        if plan and self.mode == "plan":
            self.review_plan(plan)

    def review_plan(self, plan):
        reviewer = self.reviewer()
        if not reviewer or not self.manager:
            return
        context = {
            "type": "review",
            "plan": plan,
            "coder": self.code_character_name(),
            "reviewer": reviewer.character,
            "prompt": PLAN_PROMPT.format(coder=self.code_character_name()),
        }

        def on_thinking(summary):
            self.broadcast_to_viewers({"type": "thinking", "speaker": reviewer.character,
                                       "text": summary})

        on_thinking("")
        try:
            response = reviewer.chatgpt.get_response([], context, on_thinking=on_thinking)
        except Exception as ex:
            self.log.error("Plan reviewer LLM call failed", exc_info=ex)
            response = None
        finally:
            self.broadcast_to_viewers({"type": "thinking", "speaker": reviewer.character,
                                       "done": True})

        if not response or response.strip().upper().startswith("SKIP"):
            return
        text, stance = parse_stance(response)
        # Moved on (e.g. "go") while she was thinking: the plan's settled.
        if not text or self.mode != "plan":
            return
        self.speak_review_line(reviewer, text, stance)
        # Quoted data for the coder, never instructions.
        self.send_to_channel({"type": "plan_comment", "from": self.reviewer_name,
                              "text": text, "stance": stance})

    def speak_review_line(self, reviewer, text, stance):
        self.log.info(f"{reviewer.character} ({stance}): {text}")
        self.chat(reviewer.character, text)
        if self.avatar:
            self.avatar.say(text, character=reviewer.character)
        if self.mirror_to_twitch and reviewer.twitch and reviewer.twitch.running:
            reviewer.twitch.say(text)
        self.broadcast_to_viewers({"type": "thread", "speaker": reviewer.character,
                                   "text": text, "stance": stance})

    def apply_debate_action(self, action):
        kind = action[0]
        payload = action[1] if len(action) > 1 else {}

        if kind == "ask_coder":
            # Bobby's words reach Claude as quoted data, never instructions.
            self.send_to_channel({"type": "peer_comment", "from": self.reviewer_name, **payload})
        elif kind == "ask_reviewer":
            self.ask_reviewer_async()
        elif kind == "done":
            self.broadcast_to_viewers({"type": "debate", **payload})
            if payload.get("comment"):
                self.send_to_channel({"type": "debate", "status": "agreed",
                                      "comment": payload["comment"]})
        elif kind == "deadlock":
            self.broadcast_to_viewers({"type": "debate", "status": "deadlock", **payload})
            reviewer = self.reviewer()
            if reviewer and self.avatar:
                line = DEADLOCK_LINE.format(streamer=self.manager.streamer_name)
                self.avatar.say(line, character=reviewer.character)

    def handle_ruling(self, ruling):
        self.log.info(f"ruling: {ruling}")
        self.broadcast_to_viewers({"type": "debate", "status": "ruled", **ruling})
        self.send_to_channel({"type": "ruling", **ruling})
        if self.avatar:
            self.avatar.ack(character=self.code_character_name())

    # --- Bridge -> channel / viewers ---

    def send_to_channel(self, msg):
        with self.session_lock:
            ws = self.active
        if not ws:
            return False
        try:
            ws.send(json.dumps(msg))
            return True
        except Exception as ex:
            self.log.error("Send to channel failed", exc_info=ex)
            return False

    def chat(self, speaker, text, **extra):
        """A line of the pairing conversation: to the pages' chat logs and
        the metachat terminal."""
        conversation(speaker, text)
        msg = {"type": "chat", "speaker": speaker, "text": text, "at": time.time(), **extra}
        # Record and pick recipients together: a page connecting in between
        # would otherwise get the line in its replay and again live.
        with self.session_lock:
            self.chat_history.append(msg)
            viewers = list(self.viewers)
        self.send_to_viewers(viewers, msg)

    def broadcast_to_viewers(self, msg):
        with self.session_lock:
            viewers = list(self.viewers)
        self.send_to_viewers(viewers, msg)

    def send_to_viewers(self, viewers, msg):
        frame = json.dumps(msg)
        for ws in viewers:
            try:
                ws.send(frame)
            except Exception:
                pass

    # --- Streamer input routing ---

    def route_events(self):
        while self.running:
            event = self.events.get()
            if event is None:
                return
            try:
                if event.type == Events.STREAMER_LINE:
                    self.route_utterance(event.data, event.source)
                elif event.type == Events.STREAMER_PARTIAL:
                    # Partials carry only the first words, but catching
                    # "Dyson stop" here cuts speech off a beat sooner.
                    if self.is_stop(event.data):
                        self.handle_stop()
            except Exception as ex:
                self.log.error("Error routing event", exc_info=ex)

    def route_utterance(self, text, source):
        # Coder input comes only from the streamer's own mic/keyboard; chat
        # relays are never forwarded (see plan §8 security).
        if source not in ("microphone", "keyboard") or not text:
            return
        with self.session_lock:
            connected = self.active is not None
        if not connected or not self.manager:
            return

        if self.is_stop(text):
            self.handle_stop()
            self.barged = False
            return
        self.barged = False
        if self.handle_dictation(text):
            return
        message = {"author": self.manager.streamer_name, "text": text, "source": source}
        code_ch = self.manager.code_character
        reviewer = self.reviewer()

        if self.mode == "review":
            nav = match_nav(text)
            if nav:
                self.log.info(f"nav: {nav}")
                if nav in VIEWER_NAV:
                    self.broadcast_to_viewers({"type": "scroll", "direction": nav.split("_")[1]})
                else:
                    # Moving on ends any argument about this hunk.
                    self.cancel_debate()
                    self.send_to_channel({"type": "nav", "action": nav})
                return

            # While the reviewers are arguing, the streamer's word is final.
            with self.debate_lock:
                ruling = self.debate.rule(text, reviewer.nicknames if reviewer else "",
                                          code_ch.nicknames)
            if ruling:
                self.handle_ruling(ruling)
                return

            # A question aimed at the second reviewer by name.
            if reviewer and reviewer.is_activated(message):
                self.log.info(f"question -> {reviewer.character}: {text}")
                if self.avatar:
                    self.avatar.ack(character=reviewer.character)
                self.broadcast_to_viewers({"type": "thread", "speaker": self.manager.streamer_name,
                                           "text": text, "stance": None})
                self.ask_reviewer_async(question=text)
                return

            # Otherwise only lines that name the coder are review comments;
            # the streamer is free to talk to chat (or themselves) mid-review.
            if code_ch.is_activated(message):
                self.send_utterance(text, source)
            else:
                self.log.debug(f"review: not addressed to the coder, ignored: {text}")
        else:
            # Plan/implement: normal addressing rules decide.  While planning,
            # a bare "go" approves the plan without naming the coder.
            if (code_ch.is_addressed(message) or code_ch.is_discussion_continued(message)
                    or (self.mode == "plan" and match_plan_go(text))):
                self.send_utterance(text, source)

    def handle_request(self, ws, text):
        """A request typed into the console page goes straight to the coder,
        like keyboard input but without the addressing rules."""
        text = text.strip()
        if not text:
            return
        with self.session_lock:
            connected = self.active is not None
        if not connected:
            ws.send(json.dumps({"type": "chat", "speaker": "system",
                                "text": "No coder session is connected."}))
            return
        self.send_utterance(text, "console")

    def send_utterance(self, text, source):
        self.log.info(f"utterance -> coder: {text}")
        if self.manager:
            self.chat(self.manager.streamer_name, text, source=source)
        sent = self.send_to_channel({
            "type": "utterance",
            "source": source,
            "text": text,
            "ts": time.time(),
        })
        # Dyson is suppressed while a session is connected, so the normal
        # addressed-ack never fires; ack here so the streamer knows the
        # utterance was captured (the reply may be a while if Claude is
        # mid-turn, since channel events queue).
        if sent and self.avatar:
            self.avatar.ack(character=self.code_character_name())
        if sent and self.mode == "review":
            self.broadcast_to_viewers({"type": "comment", "text": text})

    # --- Dictated change requests ---
    # "Dyson, here's my change request" (or "here's what I'm thinking") ...
    # "Okay Dyson, begin" (or "Dyson, cancel").  Everything in between is
    # captured, not routed, and shows live on the console; on "begin" it goes
    # to the coder as one request.

    def dictation_patterns(self):
        names = rf"(?:{self.manager.code_character.nicknames})"
        start = (rf"\b{names}\W+(?:(?:(?:here'?s|here is|i have|i've got|got) "
                 rf"(?:my |a |another )?)?(?:new )?change request"
                 rf"|(?:here'?s|here is|i have|i've got|got) (?:my |a |another )?(?:new )?change"
                 rf"|(?:here'?s|here is) what i'?m thinking|here is what i am thinking)\b\W*(.*)$")
        end = rf"^(?:ok(?:ay)?\W+)?{names}\W+(?:begin|go ahead|start|send it)\W*$"
        cancel = rf"^(?:{names}\W+(?:cancel|never ?mind|scratch that)|cancel(?: that)?\W+{names})\W*$"
        return start, end, cancel

    def mute_for_dictation(self, muted):
        """The dictated words still go out to chat, where the characters
        would answer them; keep them all quiet until the request is done."""
        for ch in self.manager.ordered:
            if muted:
                self.manager.suppress(ch.character, "dictation")
            else:
                self.manager.unsuppress(ch.character, "dictation")

    def unmute_after_dictation(self):
        """Unmute once the last dictated lines have come back through chat,
        unless another dictation has started by then."""
        def unmute():
            if self.dictation is None:
                self.mute_for_dictation(False)
        timer = Timer(DICTATION_UNMUTE_DELAY, unmute)
        timer.daemon = True
        timer.start()

    def dictation_message(self, state):
        return {"type": "dictation", "state": state,
                "text": " ".join(self.dictation or [])}

    def handle_dictation(self, text):
        """Returns True when the line was part of a dictation (so it's
        neither routed nor answered)."""
        start, end, cancel = self.dictation_patterns()
        if self.dictation is None:
            m = re.search(start, text, re.IGNORECASE)
            if not m:
                return False
            self.log.info("Dictation started")
            self.dictation = [m.group(1).strip()] if m.group(1).strip() else []
            self.mute_for_dictation(True)
            if self.avatar:
                self.avatar.ack(character=self.code_character_name())
            self.broadcast_to_viewers(self.dictation_message("active"))
            return True

        if re.search(end, text.strip(), re.IGNORECASE):
            request = " ".join(self.dictation).strip()
            self.dictation = None
            self.unmute_after_dictation()
            self.broadcast_to_viewers({"type": "dictation", "state": "sent", "text": request})
            if request:
                self.log.info(f"Dictation sent: {request}")
                self.send_utterance(request, "dictation")
            return True
        if re.search(cancel, text.strip(), re.IGNORECASE):
            self.log.info("Dictation cancelled")
            self.dictation = None
            self.unmute_after_dictation()
            if self.avatar:
                self.avatar.noack()
            self.broadcast_to_viewers({"type": "dictation", "state": "cancelled", "text": ""})
            return True

        self.dictation.append(text.strip())
        self.broadcast_to_viewers(self.dictation_message("active"))
        return True

    def is_stop(self, text):
        """The one spoken interrupt: "Dyson, stop" (or "stop, Dyson"), with
        any of the coder's nicknames.  Talking otherwise never cuts in."""
        if not text or not self.manager:
            return False
        names = self.manager.code_character.nicknames
        return re.search(rf"\b(?:{names})\W+stop\b|\bstop\W+(?:{names})\b",
                         text, re.IGNORECASE) is not None

    def handle_stop(self):
        # Once per utterance: the partial and the final line both match.
        if self.barged:
            return
        with self.session_lock:
            connected = self.active is not None
        if not connected:
            return
        self.barged = True
        self.log.info("Stop: silencing the coder and reviewer")

        # Only the characters the bridge speaks for: the coder, plus the
        # second reviewer while reviewing.  Bobby's ordinary chat replies are
        # none of our business.
        if self.avatar:
            ours = {self.code_character_name()}
            if self.mode == "review" and self.reviewer_name:
                ours.add(self.reviewer_name)
            for character in ours:
                self.avatar.stop(character)
        self.cancel_debate()
        self.send_to_channel({"type": "stop"})
