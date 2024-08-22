
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import sleep
from queue import Queue

from chat import ChatApp
from streamer import StreamerApp
from chatbot import ChatbotApp

from config import *
from logs import *

application = None

''' Main applicaion '''
class Application():
    token = None
    running = False
    history = []

    def __init__(self):
        self.log = Logger("metachat")
        self.chat = ChatApp(on_message=lambda m: self.on_message(m))
        self.streamer = StreamerApp(on_say=lambda m: self.on_message(m))
        self.chatbot = ChatbotApp(on_say=lambda m: self.on_message(m))

        self.queue = Queue()

    def start(self):
        self.log.info(f"Application started on {datetime.now().strftime('%c')}..")
        self.running = True
        self.loop()

    def loop(self):
        try:
            while self.running:
                self.streamer.ensure_connected()
                self.chat.ensure_connected()
                self.chatbot.ensure_connected()

                self.log.debug("tick")
                sleep(3)

        except KeyboardInterrupt:
            pass

        self.log.info("Application loop finished..")
        self.chat.shutdown()
        self.streamer.shutdown()
        self.chatbot.shutdown()

    def on_message(self, message):
        self.log.info("Passing message through to chatbot..")
        self.chatbot.on_message(message)

if __name__ == "__main__":
    application = Application()
    application.start()
