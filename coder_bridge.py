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

# Modes where the bridge owns the second reviewer's voice and the stream
# shows the review page.
REVIEW_MODES = ("review", "label")

# While labeling: skipping an image, and going with someone's pick
# ("go with Bobby", "Dyson's right", "agree with Dyson").
LABEL_SKIP = r"(?:skip(?: (?:it|this|that|this one|that one))?|pass|no label)"
# Asking the coder and reviewer to look (blind mode keeps them out until then).
LABEL_HELP = (r"(?:i (?:can't|cannot|can not) tell|i (?:don't|do not) know|(?:i'm )?not sure"
              r"|what do (?:you|you guys|you two|y'all) think)")
LABEL_GO_WITH = (r"(?:(?:i'll |let's |i'd )?(?:go|going) with|agree with|same as|i'm with)"
                 r" (\w+)(?:'s(?: (?:pick|label|call|answer))?)?"
                 r"|(\w+)(?:'s| is) (?:right|correct)")
# Ending a labeling pass early.  Review's "looks good" is too easy to say
# about a pick to mean "stop here".
LABEL_DONE = (r"(?:(?:i'm |we're )?done(?: labell?ing)?|(?:that's|that is) (?:it|all)"
              r"|stop labell?ing|finish(?: labell?ing)?)")

# Largest frame the bridge accepts (label mode's inline images).
MAX_FRAME_BYTES = 16 * 1024 * 1024

# Chat lines kept for pages that connect (or reconnect) mid-conversation.
CHAT_HISTORY = 200

# Dictated words reach the chat characters via the Twitch round trip, after
# the bridge has them; stay muted this long after a dictation ends.
DICTATION_UNMUTE_DELAY = 4.0

# Leading interjections that voice input tends to carry ("All right, next"),
# and trailing ones ("looks good to me too", "next please").
FILLER = re.compile(r"^(?:okay|ok|all right|alright|yeah|yes|so|um|uh|dyson)[,\s]+")
# The same leading interjections, without the coder's name.
LEAD_FILLER = re.compile(r"^(?:okay|ok|all right|alright|yeah|yes|so|um|uh)[,\s]+")
TRAILER = re.compile(r"[,\s]+(?:too|as well|then|please|thanks|dyson)$")

# Approving a proposed plan out loud ("go", "let's do it"); review-style
# approvals ("looks good to me", "I like it") count too.
PLAN_GO = (r"(?:go|go ahead|go for it|let's (?:do it|go|do this)|do it|make it so"
           r"|sounds good(?: to me)?|(?:that|the) plan (?:works|looks good)|ship it)")
# Dropping a plan that a dictation started ("cancel", "forget about it").
PLAN_CANCEL = (r"(?:cancel(?: that| it| the plan)?|forget (?:about )?it|never ?mind"
               r"|scratch that)")
# A line that starts like this is for chat, not for the open plan.
OFF_RECORD = re.compile(r"^\W*off the record\b", re.IGNORECASE)


def option_names(options):
    """Lowercased name -> name as the task spells it."""
    return {str(o.get("name", "")).lower(): o["name"] for o in options if o.get("name")}


def parse_call(cleaned, labels, flags):
    """A spoken call ("unclear, blurry", "neutral") as (label, [flags]) in the
    task's spelling, or None unless every word is the label or a flag."""
    words = [w for w in re.split(r"[,\s]+", cleaned) if w and w not in ("and", "with", "plus")]
    label_names, flag_names = option_names(labels), option_names(flags)

    def longest(names, at):
        for end in range(len(words), at, -1):
            name = names.get(" ".join(words[at:end]))
            if name:
                return name, end
        return None, at

    label, at = longest(label_names, 0)
    if not label:
        return None
    found = []
    while at < len(words):
        flag, at_next = longest(flag_names, at)
        if not flag:
            return None
        if flag not in found:
            found.append(flag)
        at = at_next
    return label, found


def parse_label(text, labels, flags=()):
    """Split a label reply into (spoken_text, "label:flag").  The label is
    None when the reply didn't name one of the labels, or named an unknown
    flag."""
    if not text:
        return "", None
    match = LABEL_RE.search(text)
    if not match:
        return text.strip(), None
    parts = [p.strip().lower() for p in re.split(r"[:,]", match.group(1)) if p.strip()]
    label_names, flag_names = option_names(labels), option_names(flags)
    if not parts or parts[0] not in label_names or any(p not in flag_names for p in parts[1:]):
        return text[:match.start()].strip(), None
    value = [label_names[parts[0]]]
    for p in parts[1:]:
        if flag_names[p] not in value:
            value.append(flag_names[p])
    return text[:match.start()].strip(), ":".join(value)


def spoken_label(value):
    """"unclear:blurry" as it's said: "unclear, blurry"."""
    return ", ".join(value.split(":"))


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


def match_plan_cancel(text):
    """True when the utterance drops the plan being discussed."""
    return bool(re.fullmatch(PLAN_CANCEL, clean_utterance(text)))


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
# The second reviewer's own label for an image.  She doesn't hear the coder's
# pick first, so the two are independent.  The LABEL line is parsed off.
LABEL_PROMPT = (
    "Give your own label for this image in one short spoken sentence: name "
    "the label and say why, no file names. Then on a new line write LABEL: "
    "followed by exactly one of the label names, plus any flags that apply "
    "after colons (LABEL: name:flag). Usually no flags.")
LABEL_RE = re.compile(r"\s*\**\blabel\b\**\s*[:=]\s*\**([^\n*]+?)\**\W*$", re.IGNORECASE)
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
        # True once a dictation is sent: the streamer's replies keep going to
        # the coder, unaddressed, until they say go or cancel.
        self.plan_talk = False
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
        # The image being labeled; whose pick is whose for "go with ..." by
        # (task, index); and the images already looked at (spoken, or asked
        # about when blind).
        self.current_label_item = None
        self.label_picks = {}
        self.labeled = set()
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
            # Label mode sends images inline; the default 1 MiB frame limit
            # is too small for a photo.
            with wbs_serve(self.handle_connect, self.address, self.port,
                           max_size=MAX_FRAME_BYTES) as server:
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
            if self.mode == "label" and self.current_label_item:
                replay.append(self.current_label_item)
            if self.dictation is not None:
                replay.append(self.dictation_message("active"))
            elif self.plan_talk:
                replay.append(self.dictation_message("plan"))
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
            if self.dictation is not None or self.plan_talk:
                self.dictation = None
                self.plan_talk = False
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
        elif mtype == "label_item":
            self.handle_label_item(msg)
        elif mtype == "label_suggestion":
            self.handle_label_suggestion(msg)
        elif mtype == "turn":
            self.handle_turn(msg)
        elif mtype == "vocab":
            self.handle_vocab(msg)
        elif mtype in ("activity", "review_result", "label_result"):
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
            if current in REVIEW_MODES:
                rev.set_send_timeout(self.review_send_timeout)
            elif previous in REVIEW_MODES:
                rev.set_send_timeout(None)

        # In review the bridge owns the second reviewer's voice too: no
        # ambient chat replies in the middle of a debate.
        reviewer = self.reviewer()
        if reviewer and self.manager:
            if current in REVIEW_MODES:
                self.manager.suppress(reviewer.character, "review")
            elif previous in REVIEW_MODES:
                self.manager.unsuppress(reviewer.character, "review")
        if previous == "label":
            self.current_label_item = None
            self.label_picks = {}
            self.labeled.clear()
        if previous == "review":
            self.cancel_debate()
            self.review_id = None
            self.reviewed.clear()

        # Building or reviewing means the plan talk is over, however it ended.
        if self.plan_talk and current in ("implement", *REVIEW_MODES):
            self.end_plan_talk()

        # Swap OBS scenes in and out of review.  On its own thread: the OBS
        # connect/switch must not stall the channel reader (review_begin
        # sends the first hunk right behind the state change).
        if self.obs:
            Thread(daemon=True, target=self.switch_obs_scene, args=(previous, current)).start()

    def switch_obs_scene(self, previous, current):
        try:
            self.obs.ensure_connected()
            if current in REVIEW_MODES and previous not in REVIEW_MODES:
                self.saved_scene = self.obs.get_current_scene_name()
                self.obs.set_current_scene_name(self.review_scene)
            elif previous in REVIEW_MODES and current not in REVIEW_MODES and self.saved_scene:
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

    def label_key(self, item):
        return (item.get("task"), item.get("index"))

    def current_picks(self):
        """Whose pick is whose for the image on screen, as "label:flag" values."""
        item = self.current_label_item
        return self.label_picks.setdefault(self.label_key(item), {}) if item else {}

    def handle_label_item(self, msg):
        index = msg.get("index")
        self.log.info(f"label {index}/{msg.get('total')}: {msg.get('file')}")
        self.current_label_item = msg
        coder = self.code_character_name()
        if msg.get("suggestion"):
            self.current_picks()[coder] = msg["suggestion"]
        self.broadcast_to_viewers(msg)

        # Blind, nobody looks until the streamer asks; otherwise the coder's
        # pick is spoken and the reviewer looks, once per image.
        key = self.label_key(msg)
        if msg.get("blind") or key in self.labeled:
            return
        self.labeled.add(key)
        if msg.get("suggestion"):
            self.say_coder_pick(msg["suggestion"], msg.get("note"))
        self.ask_label_reviewer_async(msg)

    def handle_label_suggestion(self, msg):
        """The coder's pick after the streamer asked for help (blind)."""
        item = self.current_label_item
        if not item or (msg.get("task"), msg.get("index")) != self.label_key(item):
            self.log.info("Dropping stale coder label pick")
            return
        coder = self.code_character_name()
        self.current_picks()[coder] = msg.get("label", "")
        self.log.info(f"{coder} labels {msg.get('label')}")
        self.say_coder_pick(msg.get("label", ""), msg.get("note"))
        self.broadcast_to_viewers({"type": "label_pick", "speaker": coder, "label": msg.get("label"),
                                   "text": msg.get("note", ""), "index": msg.get("index")})

    def say_coder_pick(self, value, note=None):
        if not self.avatar:
            return
        line = f"I'd say {spoken_label(value)}."
        if note:
            line += f" {note}"
        self.avatar.say(line, character=self.code_character_name())

    def ask_for_label_help(self):
        """"I can't tell": blind, the coder and the reviewer look now, once."""
        item = self.current_label_item
        if not item or not item.get("blind"):
            return
        key = self.label_key(item)
        if key in self.labeled:
            return
        self.labeled.add(key)
        self.log.info(f"label: help asked on {item.get('index')}")
        if self.avatar:
            self.avatar.ack(character=self.code_character_name())
        self.send_to_channel({"type": "label_help"})
        self.ask_label_reviewer_async(item)

    def ask_label_reviewer_async(self, item):
        if self.reviewer():
            Thread(daemon=True, target=self.ask_label_reviewer, args=(item,)).start()

    def ask_label_reviewer(self, item):
        """The second reviewer looks at the image and gives her own pick."""
        reviewer = self.reviewer()
        context = {
            "type": "review",
            "label_item": item,
            "coder": self.code_character_name(),
            "reviewer": reviewer.character,
            "prompt": LABEL_PROMPT,
        }

        def on_thinking(summary):
            self.broadcast_to_viewers({"type": "thinking", "speaker": reviewer.character,
                                       "text": summary})

        on_thinking("")
        try:
            response = reviewer.chatgpt.get_response([], context, on_thinking=on_thinking)
        except Exception as ex:
            self.log.error("Label reviewer LLM call failed", exc_info=ex)
            response = None
        finally:
            self.broadcast_to_viewers({"type": "thinking", "speaker": reviewer.character,
                                       "done": True})

        text, label = parse_label(response, item.get("labels", []), item.get("flags", []))
        if not label:
            self.log.error(f"Label reviewer gave no usable label: {response!r}")
            return
        # Moved on while she was looking: her pick is for another image.
        current = self.current_label_item
        if self.mode != "label" or not current or self.label_key(current) != self.label_key(item):
            self.log.info("Dropping stale label pick")
            return
        self.current_picks()[reviewer.character] = label
        self.log.info(f"{reviewer.character} labels {label}: {text}")
        self.speak_as(reviewer, text)
        self.broadcast_to_viewers({"type": "label_pick", "speaker": reviewer.character,
                                   "label": label, "text": text, "index": item.get("index")})
        # The second opinion goes in the coder's results too.
        self.send_to_channel({"type": "label_pick", "from": reviewer.character, "label": label})

    def match_label(self, text):
        """The streamer's call on the current image: (label, flags, by),
        "skip", "help", "done", or None when the line isn't one."""
        item = self.current_label_item
        if not item:
            return None
        cleaned = clean_utterance(text)
        for word, pattern in (("skip", LABEL_SKIP), ("help", LABEL_HELP), ("done", LABEL_DONE)):
            if re.fullmatch(pattern, cleaned):
                return word
        call = parse_call(cleaned, item.get("labels", []), item.get("flags", []))
        if call:
            return (*call, self.manager.streamer_name)
        # Not clean_utterance: it would strip the coder's name off "go with Dyson".
        plain = re.sub(r"[.!?,]+$", "", text.strip().lower())
        m = re.fullmatch(LABEL_GO_WITH, LEAD_FILLER.sub("", plain))
        if m:
            who = m.group(1) or m.group(2)
            picks = self.current_picks()
            for character, nicknames in self.pickers():
                if re.fullmatch(nicknames, who, re.IGNORECASE) and character in picks:
                    label, *flags = picks[character].split(":")
                    return (label, flags, character)
        return None

    def pickers(self):
        """(character, nickname pattern) for each character that picks labels."""
        pickers = [(self.code_character_name(), self.manager.code_character.nicknames)]
        reviewer = self.reviewer()
        if reviewer:
            pickers.append((reviewer.character, reviewer.nicknames))
        return pickers

    def route_label_utterance(self, text, source, message):
        nav = match_nav(text)
        if nav in ("next", "back"):
            self.log.info(f"label nav: {nav}")
            self.send_to_channel({"type": "nav", "action": nav})
            return
        call = self.match_label(text)
        if call == "help":
            self.ask_for_label_help()
            return
        if call in ("skip", "done"):
            self.log.info(f"label: {call}")
            self.send_to_channel({"type": "nav", "action": "next" if call == "skip" else "approve"})
            return
        if call:
            label, flags, by = call
            value = ":".join([label, *flags])
            self.log.info(f"label: {value} (by {by})")
            self.broadcast_to_viewers({"type": "label_call", "label": value, "by": by})
            self.send_to_channel({"type": "label", "label": label, "flags": flags, "by": by})
            return
        # Anything else is talk: it goes to the coder only when it's named.
        if self.manager.code_character.is_activated(message):
            self.send_utterance(text, source)

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
        self.speak_as(reviewer, text)
        self.broadcast_to_viewers({"type": "thread", "speaker": reviewer.character,
                                   "text": text, "stance": stance})

    def speak_as(self, reviewer, text):
        """The second reviewer says a line: chat logs, avatar, and Twitch."""
        self.chat(reviewer.character, text)
        if self.avatar:
            self.avatar.say(text, character=reviewer.character)
        if self.mirror_to_twitch and reviewer.twitch and reviewer.twitch.running:
            reviewer.twitch.say(text)

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
        if self.handle_plan_talk(text, source):
            return
        message = {"author": self.manager.streamer_name, "text": text, "source": source}
        code_ch = self.manager.code_character
        reviewer = self.reviewer()

        if self.mode == "label":
            self.route_label_utterance(text, source, message)
        elif self.mode == "review":
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
            if self.dictation is None and not self.plan_talk:
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
            self.broadcast_to_viewers({"type": "dictation", "state": "sent", "text": request})
            if request:
                self.log.info(f"Dictation sent: {request}")
                self.send_utterance(request, "dictation")
                # Stay muted: the plan talk that follows is dictated too.
                self.plan_talk = True
                self.broadcast_to_viewers(self.dictation_message("plan"))
            else:
                self.unmute_after_dictation()
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

    # --- Plan talk after a dictation ---
    # Once a dictated request is sent, the back-and-forth on its plan is
    # dictated too: each line goes to the coder without naming it, until
    # "go" or "cancel" (both forwarded, so the coder acts on them).  "Off the
    # record ..." lines are for chat and go nowhere.

    def handle_plan_talk(self, text, source):
        """Returns True when the line belonged to an open plan talk."""
        if not self.plan_talk or self.mode not in ("plan", "idle"):
            return False
        if OFF_RECORD.match(text):
            self.log.debug(f"plan talk: off the record: {text}")
            return True
        if match_plan_go(text) or match_plan_cancel(text):
            self.end_plan_talk()
        self.send_utterance(text, source)
        return True

    def end_plan_talk(self):
        self.log.info("Plan talk ended")
        self.plan_talk = False
        self.unmute_after_dictation()
        self.broadcast_to_viewers(self.dictation_message("ended"))

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
            if self.mode in REVIEW_MODES and self.reviewer_name:
                ours.add(self.reviewer_name)
            for character in ours:
                self.avatar.stop(character)
        self.cancel_debate()
        self.send_to_channel({"type": "stop"})
