from datetime import datetime
from time import time

from restream import RestreamApp
from oauth import OAuthApp

from logs import *
from config import *

class ChatApp():
    token = None

    def __init__(self, on_message):
        self.log = Logger("chat")
        self.on_message_cb = on_message
        self.restream = RestreamApp(on_message=lambda m: self.on_message(m))
        self.oauth = OAuthApp("restream.io")
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
            self.start_time = time()
            self.chat_log_file = open(path, "a")
            self.chat_log({'author': 'METACHAT', 'text': f"*** Chat session started on {now.strftime('%c')} ***"})
        else:
            self.log_file = None

    def chat_log(self, message):
        if self.chat_log_file:
            t = time() - self.start_time
            print(f"[{t:10.2}] {message['author']}: {message['text']}", file=self.chat_log_file)
            self.chat_log_file.flush()

    def on_message(self, message):
        self.on_message_cb(message)
        self.chat_log(message)


