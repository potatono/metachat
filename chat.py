from datetime import datetime
import time

from restream import RestreamApp
from oauth import OAuthApp

from logs import Logger
from config import CONFIG
from eventbus import eventbus, Events

class ChatApp():
    token = None

    def __init__(self):
        self.log = Logger("chat")
        self.streamer_name = CONFIG.get("streamer", "name")
        self.restream = RestreamApp()
        self.oauth = OAuthApp("restream.io")
        self.events = eventbus.create_subscriber(name="chat", event_types=[Events.CHAT_MESSAGE], callback=self.on_chat_message)
        self.open_chat_log()

    def ensure_connected(self):
        if not self.restream.running:
            if self.token is None:
                self.log.info("Waiting for Restream token...")
                self.token = self.oauth.get_token()               
            else:
                self.log.info("Token received.  Starting Restream Server..")
                self.restream.start(self.token)

    def shutdown(self):
        self.restream.shutdown()
        self.oauth.shutdown()

        if self.chat_log_file:
            self.chat_log({'author': 'METACHAT', 'text': f"*** Chat session ended ***"})
            self.chat_log_file.close()

    def open_chat_log(self):
        now = datetime.now()
        data = {
            "month": now.strftime("%m"),
            "day": now.strftime("%d"),
            "year": now.strftime("%Y")
        }
        path = CONFIG.get("chat", "log_path", vars=data, fallback=None)

        if path:
            self.log.info(f"Saving log to {path}..")
            self.start_time = time.time()
            self.chat_log_file = open(path, "a")
            self.chat_log({'author': 'METACHAT', 'text': f"*** Chat session started on {now.strftime('%c')} ***"})
        else:
            self.log_file = None

    def chat_log(self, message):
        if self.chat_log_file:
            t = time.time() - self.start_time
            print(f"[{t:10.2}] {message['author']}: {message['text']}", file=self.chat_log_file)
            self.chat_log_file.flush()
            
    def on_chat_message(self, event):
        self.log.debug(f"Received event: {event}")
        message = event.data
        self.chat_log(message)
