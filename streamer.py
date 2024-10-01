import time

from rev import TranscriptApp
from twitch import TwitchApp
from oauth import OAuthApp

from config import *
from logs import *

class StreamerApp():
    token = None

    def __init__(self, on_say, on_voice):
        self.log = Logger("streamer")
        self.rev = TranscriptApp(
            on_text=lambda t : self.on_text(t),
            on_voice=on_voice
        )
        self.name = CONFIG.get("streamer", "name")

        if CONFIG.getboolean("streamer", "send_to_twitch", fallback=True):
            self.oauth = OAuthApp(CONFIG.get("streamer", "twitch_oauth_section"))
            self.twitch = TwitchApp(self.name, self.name)
        else:
            self.oauth = None
            self.twitch = None

        self.on_say = on_say

    def ensure_connected(self):
        if not self.rev.running:
            self.rev.start()

        if self.twitch: 
            if not self.twitch.running:
                if self.token is None:
                    self.log.info("Waiting for Streamer Twitch token...")
                    self.token = self.oauth.get_token()
                else:
                    self.log.info("Token received.  Starting Twitch Server..")
                    self.twitch.start(self.token)        

    def shutdown(self):
        self.rev.shutdown()
        
        if self.twitch:
            self.twitch.shutdown()
            self.oauth.shutdown()


    def on_text(self, text):
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
            self.on_say(msg)
    
    
