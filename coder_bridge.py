import json
import re
import time
import queue
from threading import Thread, Lock

import websockets
from websockets.sync.server import serve as wbs_serve

from config import CONFIG, SECRETS
from logs import Logger
from eventbus import eventbus, Events
from obs import ObsApp

# Spoken review-navigation commands, handled without a model round trip.
NAV_PATTERNS = [
    ("next", r"(?:next|go on|keep going|continue)"),
    ("back", r"(?:back|go back|previous)"),
    ("more_context", r"(?:more context|zoom out|show (?:me )?more)"),
    ("whole_file", r"(?:(?:show )?(?:the )?whole file|full file)"),
    ("approve", r"(?:approve[d]?|looks good|ship it|l\.?g\.?t\.?m\.?)"),
    # Handled locally by the review page, not sent to the channel.
    ("scroll_down", r"(?:scroll down|down a bit)"),
    ("scroll_up", r"(?:scroll up|up a bit)"),
]

VIEWER_NAV = {"scroll_down", "scroll_up"}

def match_nav(text):
    """Return the nav action for a bare navigation utterance, else None."""
    cleaned = text.strip().strip(".!,").strip().lower()
    for action, pattern in NAV_PATTERNS:
        if re.fullmatch(pattern, cleaned):
            return action
    return None

''' WebSocket server connecting a remote Claude Code channel to this machine.

Routes the streamer's mic/keyboard to the remote session, speaks replies
through the code character's avatar, drives the review page, and publishes
CODER_STATE so other characters can react.  Protocol: docs/PAIR_PROGRAMMING_PLAN.md §6. '''
class CoderBridgeApp():
    def __init__(self, chatbot_manager=None, streamer=None):
        self.log = Logger("coder")

        self.enable = CONFIG.getboolean("coder", "enable", fallback=False)
        self.address = CONFIG.get("coder", "bind_address", fallback="0.0.0.0")
        self.port = CONFIG.getint("coder", "port", fallback=9010)
        self.review_send_timeout = CONFIG.getfloat("coder", "review_send_timeout", fallback=1.0)
        self.review_scene = CONFIG.get("coder", "review_scene", fallback=None)
        self.mirror_to_twitch = CONFIG.getboolean("coder", "mirror_to_twitch", fallback=False)
        self.token = SECRETS.get("coder", "token", fallback=None)

        self.manager = chatbot_manager
        self.streamer = streamer
        self.avatar = chatbot_manager.tts if chatbot_manager else None

        self.running = False
        self.server = None
        self.server_thread = None
        self.router_thread = None

        # The most recent authenticated channel session wins; older ones are
        # closed.  Viewers (review pages) are separate and additive.
        self.session_lock = Lock()
        self.active = None
        self.session_count = 0
        self.viewers = set()

        self.mode = "idle"
        self.chunk = None
        self.chunks = None
        self.activity = None
        self.current_hunk = None
        self.last_say_interruptible = True
        self.barged = False
        self.saved_scene = None
        self.obs = ObsApp() if self.review_scene else None

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
            replay = [self.state_message()]
            if self.current_hunk:
                replay.append(self.current_hunk)
        try:
            for msg in replay:
                ws.send(json.dumps(msg))
            for _ in ws:
                pass  # viewers only listen
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
            self.manager.suppress(self.manager.code_character.character)

    def on_session_ended(self):
        if self.manager:
            self.manager.unsuppress(self.manager.code_character.character)
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
        elif mtype == "vocab":
            self.handle_vocab(msg)
        else:
            self.log.warning(f"Unhandled channel message type: {mtype}")

    def code_character_name(self):
        return self.manager.code_character.character if self.manager else "dyson"

    def handle_say(self, msg):
        text = msg.get("text", "")
        self.last_say_interruptible = msg.get("interruptible", True)
        self.barged = False
        self.log.info(f"say: {text}")

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

        # Swap OBS scenes in and out of review.
        if self.obs:
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
        self.current_hunk = msg
        self.broadcast_to_viewers(msg)

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
            "connected": self.active is not None,
        }

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

    def broadcast_to_viewers(self, msg):
        with self.session_lock:
            viewers = list(self.viewers)
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
                    self.handle_barge_in()
            except Exception as ex:
                self.log.error("Error routing event", exc_info=ex)

    def route_utterance(self, text, source):
        # Coder input comes only from the streamer's own mic/keyboard; chat
        # relays are never forwarded (see plan §9 security).
        if source not in ("microphone", "keyboard") or not text:
            return
        with self.session_lock:
            connected = self.active is not None
        if not connected or not self.manager:
            return

        self.barged = False
        message = {"author": self.manager.streamer_name, "text": text, "source": source}
        code_ch = self.manager.code_character

        if self.mode == "review":
            nav = match_nav(text)
            if nav:
                self.log.info(f"nav: {nav}")
                if nav in VIEWER_NAV:
                    self.broadcast_to_viewers({"type": "scroll", "direction": nav.split("_")[1]})
                else:
                    self.send_to_channel({"type": "nav", "action": nav})
                return

            # In review, anything not aimed at another character is a review
            # comment for the coder (plan §4.4).
            for ch in self.manager.ordered:
                if ch is not code_ch and ch.is_activated(message):
                    return
            self.send_utterance(text, source)
        else:
            # Plan/implement: normal addressing rules decide.
            if code_ch.is_addressed(message) or code_ch.is_discussion_continued(message):
                self.send_utterance(text, source)

    def send_utterance(self, text, source):
        self.log.info(f"utterance -> coder: {text}")
        sent = self.send_to_channel({
            "type": "utterance",
            "source": source,
            "text": text,
            "ts": time.time(),
        })
        if sent and self.mode == "review":
            self.broadcast_to_viewers({"type": "comment", "text": text})

    def handle_barge_in(self):
        # The streamer started talking; cut the coder's speech off once per
        # utterance, if the current line allows it.
        if self.barged or not self.last_say_interruptible:
            return
        with self.session_lock:
            connected = self.active is not None
        if not connected or not self.avatar:
            return
        if getattr(self.avatar, "is_talking", False) or getattr(self.avatar, "queue", None):
            self.barged = True
            self.log.info("Barge-in: stopping coder speech")
            self.avatar.stop(self.code_character_name())
            self.send_to_channel({"type": "barge_in"})
