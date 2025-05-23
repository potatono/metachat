
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import sleep
from queue import Queue

from chat import ChatApp
from streamer import StreamerApp
from chatbot import ChatbotApp
from reactions import ReactionsApp

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
        self.streamer = StreamerApp(on_say=lambda m: self.chat.on_message(m),
                                    on_voice=lambda t: self.on_voice(t))
        self.chatbot = ChatbotApp(on_say=lambda m: self.on_message(m))
        self.reactions = ReactionsApp()

        self.queue = Queue()

    def start(self):
        self.log.info(f"Application started on {datetime.now().strftime('%c')}..")
        self.running = True
        self.loop()

    def ensure_connected(self):
        self.streamer.ensure_connected()
        self.chat.ensure_connected()
        self.chatbot.ensure_connected()
        self.reactions.ensure_connected()

    def shutdown(self):
        self.chat.shutdown()
        self.streamer.shutdown()
        self.chatbot.shutdown()
        self.reactions.shutdown()

    def tick(self):
        self.chatbot.tick()
    
    def loop(self):
        framecount = 0
        while self.running:
            try:
                framecount += 1

                if framecount % 180 == 0:
                    self.ensure_connected()

                self.tick()

                sleep(1/60.0)
            except KeyboardInterrupt:
                break
            except Exception as ex:
                self.log.error("Exception in metachat", exc_info=ex)

        self.log.info("Application loop finished..")
        self.shutdown()

    def on_message(self, message):
        self.log.info("Passing message through to chatbot + reactions..")
        self.chatbot.on_message(message)

    def on_voice(self, text):
        self.reactions.on_voice(text)
        self.chatbot.on_voice(text)

if __name__ == "__main__":    
    application = Application()
    application.start()
