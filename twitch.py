from threading import Thread
import json
import re
from time import sleep

import websocket
import requests

from config import *
from logs import *

from queue import Queue

''' WebSocket App for communicating with Twitch '''
class TwitchApp():
    app = None
    wsa = None
    thread = None
    running = False

    def __init__(self, name, channel, user_id=None):
        self.log = Logger(f"twitch {name}")
        self.name = name
        self.channel = channel

        self.send_method = CONFIG.get("twitch.tv", "send_method", fallback="ws")
        self.thread = Thread(daemon=True, target=self.loop)


        if self.send_method == "ws":
            self.ws_url = CONFIG.get("twitch.tv", "chat_ws_url", fallback=None)
        else:
            self.client_id = CONFIG.get("twitch.tv", "client_id", fallback=None)
            self.api_url = CONFIG.get("twitch.tv", "chat_api_url", fallback=None)
            self.broadcaster_id = CONFIG.getint("twitch.tv", "broadcaster_id", fallback=None)
            # The sending bot's user id; per-character so each character can post
            # to chat under its own Twitch account. Falls back to the shared one.
            if name == channel:
                self.user_id = self.broadcaster_id
            else:
                # The sending bot's user id; per-character so each character can post
                # to chat under its own Twitch account. Falls back to the shared one.
                self.user_id = user_id if user_id is not None else CONFIG.getint("twitch.tv", "user_id", fallback=None)

            self.last_api_send = None
            self.api_send_queue = []
            self.api_send_rate = CONFIG.getfloat("twitch.tv", "api_send_rate", fallback=1.0)
            self.send_queue = Queue()

    def on_ws_message(self, ws, message):
        self.log.info(message)

        if message.startswith("PING"):
            self.ws_send(message.replace("PING", "PONG"))        

    def on_ws_open(self, ws):
        self.ws_send(f"CAP REQ :twitch.tv/membership twitch.tv/tags twitch.tv/commands")
        self.ws_send(f"PASS oauth:{self.token}")
        self.ws_send(f"NICK {self.name}")
        self.ws_send(f"JOIN #{self.channel}")

    def on_ws_close(self, ws):
        self.log.error("***** WEBSOCKET TO TWITCH CLOSED *****")
        self.log.error("***** WEBSOCKET TO TWITCH CLOSED *****")
        self.log.error("***** WEBSOCKET TO TWITCH CLOSED *****")
        self.log.error("SHUTTING DOWN")
        self.shutdown()

    def ws_send(self, message):
        self.log.info(f"Sending '{message}'")
        self.wsa.send(message)

    def exeeds_api_send_rate(self):
        if self.last_api_send is None:
            return False

        elapsed = time.time() - self.last_api_send
        return elapsed < self.api_send_rate

    def process_send_queue(self):
        if not self.send_queue.empty():
            if not self.exeeds_api_send_rate():
                message = self.send_queue.get()
                self.log.debug(f"Processing send queue message")
                self.api_send(message)

    def api_send(self, message, attempt=1):
        if self.exeeds_api_send_rate():
            self.log.debug(f"API send rate exceeded, adding to send queue..")
            self.send_queue.put(message)
            return

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Client-Id": self.client_id
        }
        data = {
            "broadcaster_id": self.broadcaster_id,
            "sender_id": self.user_id,
            "message": message
        }

        self.log.debug(f"Sending via API call to {self.api_url}: {data}")

        self.last_api_send = time.time()
        resp = requests.post(self.api_url, headers=headers, json=data)

        if (resp.status_code == 200):
            self.log.debug("Message sent successfully")
        else:
            self.log.error(f"Got status code: {resp.status_code}")
            self.log.error(resp.json())

            if attempt < 3:
                attempt += 1
                self.log.warning(f"Resending failed message attempt {attempt}..")
                self.api_send(self.clean_message(message),attempt)

    def say(self, message):
        lines = re.split("[\r\n]+", message)

        for line in lines:
            if self.send_method == "api":
                self.api_send(line)
            else:
                self.ws_send(f"PRIVMSG #{self.channel} :{line}")

    def clean_message(self, message):
        return re.sub("[^\w\.\!\?\:\, ]","",message)

    def loop(self):
        self.log.info(f"Starting WS run_forever")

        try:
            if self.send_method == "ws":
                self.wsa.run_forever()
            else:
                while self.running:
                    sleep(1)
                    self.process_send_queue()
            
        except Exception as ex:
            self.log.error("Exception in Twitch", exc_info=ex)

    def start(self, token):
        self.token = token
        self.running = True

        if self.send_method == "ws":
            self.log.info(f"Starting WS thread") 
            websocket.enableTrace = True        
            self.wsa = websocket.WebSocketApp(
                self.ws_url, 
                on_message=lambda ws, message: self.on_ws_message(ws, message),
                on_open=lambda ws: self.on_ws_open(ws),
                on_close=lambda ws: self.on_ws_close(ws)
            )
        
            self.thread.start()
        else:
            self.log.info("Using API sends, no websocket needed.")
            self.thread.start()


    def shutdown(self):
        if not self.running:
            self.log.info(f"Refusing to shutdown, not started.")
            return

        self.running = False
        self.log.info(f"Shutting down..")

        if self.send_method == "ws":
            self.wsa.close()

        self.thread.join()


