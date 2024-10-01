from threading import Thread
import json
from time import sleep

import websocket

from config import *
from logs import *

''' WebSocket App for communicating with restream.io '''
class TwitchApp():
    app = None
    wsa = None
    thread = None
    running = False

    def __init__(self, name, channel):
        self.log = Logger(f"twitch {name}")
        self.name = name
        self.channel = channel
        self.thread = Thread(daemon=True, target=self.loop)

    def on_message(self, ws, message):
        self.log.info(message)

        if message.startswith("PING"):
            self.send(message.replace("PING", "PONG"))        

    def on_open(self, ws):
        self.send(f"CAP REQ :twitch.tv/membership twitch.tv/tags twitch.tv/commands")
        self.send(f"PASS oauth:{self.token}")
        self.send(f"NICK {self.name}")
        self.send(f"JOIN #{self.channel}")

    def on_close(self, ws):
        self.log.error("***** WEBSOCKET TO TWITCH CLOSED *****")
        self.log.error("***** WEBSOCKET TO TWITCH CLOSED *****")
        self.log.error("***** WEBSOCKET TO TWITCH CLOSED *****")
        self.log.error("SHUTTING DOWN")
        self.shutdown()

    def send(self, message):
        self.log.info(f"Sending '{message}'")
        self.wsa.send(message)

    def say(self, message):
        self.send(f"PRIVMSG #{self.channel} :{message}")

    def loop(self):
        self.log.info(f"Starting WS run_forever")

        try:
            self.wsa.run_forever()
        except Exception as ex:
            self.log.error("Exception in Twitch", exc_info=ex)

    def start(self, token):
        self.log.info(f"Starting WS thread")
        self.token = token        
        self.running = True
        
        websocket.enableTrace = True
        
        url = CONFIG.get("twitch.tv", "chat_ws_url")
        self.wsa = websocket.WebSocketApp(
            url, 
            on_message=lambda ws, message: self.on_message(ws, message),
            on_open=lambda ws: self.on_open(ws),
            on_close=lambda ws: self.on_close(ws)
        )
        
        self.thread.start()

    def shutdown(self):
        if not self.running:
            self.log.info(f"Refusing to shutdown, not started.")
            return

        self.running = False
        self.log.info(f"Shutting down..")
        self.wsa.close()
        self.thread.join()

