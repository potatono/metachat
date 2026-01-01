import time

from rev import TranscriptApp
from twitch import TwitchApp
from oauth import OAuthApp

from config import CONFIG
from logs import Logger
from eventbus import eventbus, Events

class StreamerApp():
    token = None

    def __init__(self):
        self.log = Logger("streamer")
        
        if CONFIG.getboolean("streamer", "use_voice_input", fallback=True):
            self.rev = TranscriptApp()
        else:
            self.rev = None

        self.name = CONFIG.get("streamer", "name")

        if CONFIG.getboolean("streamer", "send_to_twitch", fallback=True):
            self.oauth = OAuthApp(CONFIG.get("streamer", "twitch_oauth_section"))
            self.twitch = TwitchApp(self.name, self.name)
        else:
            self.oauth = None
            self.twitch = None

        eventbus.create_subscriber(
            name="streamer",
            event_types=[Events.STREAMER_LINE],
            callback=self.on_streamer_line
        )

    def ensure_connected(self):
        if self.rev and not self.rev.running:
            self.rev.start()
    
        if self.twitch and self.oauth: 
            if not self.twitch.running:
                if self.token is None:
                    self.log.info("Waiting for Streamer Twitch token...")
                    self.token = self.oauth.get_token()
                else:
                    self.log.info("Token received.  Starting Twitch Server..")
                    self.twitch.start(self.token)        

    def shutdown(self):
        if self.rev:
            self.rev.shutdown()

        if self.twitch and self.oauth:
            self.twitch.shutdown()
            self.oauth.shutdown()

    def on_streamer_line(self, event):
        text = event.data

        # If we're sending to twitch, we don't need to call on_say since the
        # message will come back through restream
        if self.twitch:
            self.twitch.say(text)
        else:
            msg = {
                "author": self.name,
                "text": text,
                "sent": time.time()
            }
            eventbus.publish(Events.CHAT_MESSAGE, data=msg, source=event.source)
