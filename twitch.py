from threading import Thread
import json
import re
from time import sleep

import websocket
import requests

from config import *
from logs import *

''' WebSocket App for communicating with Twitch '''
class TwitchApp():
    app = None
    wsa = None
    thread = None
    running = False

    def __init__(self, name, channel):
        self.log = Logger(f"twitch {name}")
        self.name = name
        self.channel = channel


        self.send_method = CONFIG.get("twitch.tv", "send_method", fallback="ws")

        if self.send_method == "ws":
            self.ws_url = CONFIG.get("twitch.tv", "chat_ws_url", fallback=None)
            self.thread = Thread(daemon=True, target=self.loop)        
        else:
            self.client_id = CONFIG.get("twitch.tv", "client_id", fallback=None)        
            self.api_url = CONFIG.get("twitch.tv", "chat_api_url", fallback=None)
            self.broadcaster_id = CONFIG.getint("twitch.tv", "broadcaster_id", fallback=None)
            self.user_id = CONFIG.getint("twitch.tv", "user_id", fallback=None)

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

    def api_send(self, message, attempt=1):
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
            self.wsa.run_forever()
        except Exception as ex:
            self.log.error("Exception in Twitch", exc_info=ex)

    def start(self, token):
        self.token = token
        self.running = True

        ## If we're using the websockets irc method to send then we need to 
        ## connect and run in a thread
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
            self.log.info("Using API sends, no thread needed.")

    def shutdown(self):
        if not self.running:
            self.log.info(f"Refusing to shutdown, not started.")
            return

        self.running = False
        self.log.info(f"Shutting down..")

        if self.send_method == "ws":
            self.wsa.close()
            self.thread.join()

