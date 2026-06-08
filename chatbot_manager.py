import re
import random
import traceback

from chatbot import ChatbotApp
from twitch import TwitchApp
from oauth import OAuthApp
from tts import TTSApp
from avatar import AvatarApp
from webserver import WebserverApp

from config import CONFIG
from logs import Logger
from eventbus import eventbus, Events


''' Coordinates one ChatbotApp per character.

The avatar subsystem already multiplexes multiple characters through a single
window; this manager does the equivalent for the chatbots.  It owns the shared
output resources (the one avatar/TTS, the copilot webserver, and the Twitch
connections), subscribes to the eventbus once, dispatches each message to every
character, and arbitrates which single character actually replies. '''
class ChatbotManager():
    def __init__(self):
        self.log = Logger("chatbot")
        self.streamer_name = CONFIG.get("streamer", "name")

        self.names = re.split(r"\s*,\s*", CONFIG.get("chatbot", "characters", fallback="bobby"))

        # The bot display name for every character.  Each ChatbotApp uses this
        # set to ignore messages authored by any bot (no inter-bot loops).
        self.bot_names = {
            CONFIG.get(f"chatbot.{n}", "name", fallback=CONFIG.get("chatbot", "name"))
            for n in self.names
        }

        # --- Shared output resources, owned here and injected per character ---
        self.tts = None
        if CONFIG.getboolean("chatbot", "send_to_tts", fallback=False):
            self.tts = TTSApp()
        elif CONFIG.getboolean("chatbot", "send_to_avatar", fallback=False):
            self.tts = AvatarApp()

        self.webserver = None
        if CONFIG.getboolean("chatbot", "enable_copilot_server", fallback=False):
            self.webserver = WebserverApp(on_copilot_message=self.on_copilot_message)

        # One Twitch token per oauth section (the handshake happens lazily in
        # ensure_connected; multiple characters never share an oauth section,
        # since each OAuthApp binds its own redirect port).
        self.tokens = {}

        # --- Build the per-character chatbots ---
        self.chatbots = {}
        for n in self.names:
            twitch = None
            oauth = None

            # Twitch is opt-in per character: a character without a provisioned
            # bot account (oauth secrets) stays avatar-only.
            send_to_twitch = CONFIG.getboolean(
                f"chatbot.{n}", "send_to_twitch",
                fallback=CONFIG.getboolean("chatbot", "send_to_twitch", fallback=True))

            if send_to_twitch:
                section = CONFIG.get(f"chatbot.{n}", "twitch_oauth_section",
                                     fallback=CONFIG.get("chatbot", "twitch_oauth_section"))
                user_id = CONFIG.getint(f"chatbot.{n}", "user_id",
                                        fallback=CONFIG.getint("chatbot", "user_id", fallback=None))
                name = CONFIG.get(f"chatbot.{n}", "name", fallback=CONFIG.get("chatbot", "name"))

                oauth = OAuthApp(section)
                twitch = TwitchApp(name, self.streamer_name, user_id=user_id)

            self.chatbots[n] = ChatbotApp(
                n,
                tts=self.tts,
                webserver=self.webserver,
                twitch=twitch,
                oauth=oauth,
                all_names=self.bot_names,
            )

        # Characters in config order, for deterministic arbitration.
        self.ordered = [self.chatbots[n] for n in self.names]

        # The character that fields copilot/code requests (first by config order).
        self.code_character = self.ordered[0]

        eventbus.create_subscriber(
            name="chatbot",
            event_types=[Events.CHAT_MESSAGE, Events.STREAMER_PHRASE],
            callback=self.on_event,
        )

    # --- Lifecycle (called by metachat's Application) ---

    def ensure_connected(self):
        for ch in self.ordered:
            if ch.twitch and not ch.twitch.running:
                section = ch.oauth.cs
                token = self.tokens.get(section)

                if token is None:
                    self.log.info(f"Waiting for {ch.name} Twitch token...")
                    token = ch.oauth.get_token()
                    self.tokens[section] = token

                if token:
                    self.log.info(f"Token received.  Starting Twitch for {ch.name}..")
                    ch.twitch.start(token)

        if self.tts and not self.tts.running:
            self.log.info("Starting TTS Server..")
            self.tts.start()

        if self.webserver:
            self.webserver.ensure_connected()

    def shutdown(self):
        for ch in self.ordered:
            if ch.twitch:
                ch.twitch.shutdown()
            if ch.oauth:
                ch.oauth.shutdown()

        if self.webserver:
            self.webserver.shutdown()

    def tick(self):
        if self.tts:
            self.tts.tick()

    # --- Event handling ---

    def on_event(self, event):
        if event.type == Events.CHAT_MESSAGE:
            self.on_message(event.data)
        elif event.type == Events.STREAMER_PHRASE:
            self.on_voice(event.data)

    def on_voice(self, text):
        self.log.debug(f"Got voice: {text}")
        message = { "author": self.streamer_name, "text": text }

        # Ack with whichever character was addressed (only one).
        for ch in self.ordered:
            if ch.is_activated(message):
                if self.tts:
                    self.tts.ack(character=ch.character)
                break

    def on_copilot_message(self, text):
        self.code_character.on_copilot_message(text)

    def on_message(self, message):
        try:
            self.log.info(f"Got message {message['author']}: {message['text']}")

            if message.get('text') is None:
                self.log.warning("Got unexpected empty message.  Returning early.")
                return

            from_bot = message['author'] in self.bot_names

            if not from_bot:
                # Bang commands use a global pattern; one character handles them.
                first = self.ordered[0]
                if first.is_bang_command(message):
                    first.process_bang_command(message)
                    return

                # Voice commands ("Hey <nick> please ...") are addressed by
                # nickname; the matching character handles them.
                for ch in self.ordered:
                    if ch.is_voice_command(message):
                        ch.process_voice_command(message)
                        return

            # Bookkeeping for every character so each keeps full conversational
            # context, including what the other characters said.
            for ch in self.ordered:
                ch.note_times(message)
                ch.append_to_history(message)

            # Never reply to a bot (defense in depth with the per-character guard).
            if from_bot:
                return

            # Gather reply candidates, then let at most one character speak.
            addressed = []
            ambient = []
            for ch in self.ordered:
                context = ch.should_reply(message)
                if context:
                    if ch.is_addressed(message):
                        addressed.append((ch, context))
                    else:
                        ambient.append((ch, context))

            if addressed:
                # Directly-addressed character wins (config order breaks ties).
                winner, context = addressed[0]
            elif ambient:
                # Ambient/boredom/spam: pick one so both get airtime over time.
                winner, context = random.choice(ambient)
            else:
                return

            winner.reply(context)

        except Exception:
            self.log.error("Caught exception in ChatbotManager.on_message")
            self.log.error(traceback.format_exc())
