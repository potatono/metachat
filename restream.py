from threading import Thread
import time
import json

import websocket

from config import CONFIG
from logs import Logger
from eventbus import eventbus, Events

''' WebSocket App for communicating with restream.io '''
class RestreamApp():
    app = None
    wsa = None
    thread = None
    running = False

    def __init__(self):    
        self.log = Logger("restream")
        self.thread = Thread(daemon=True, target=self.loop)

    def on_message(self, rs_msg):
        data = json.loads(rs_msg)
        action = data['action']

        self.log.info(action)
        if action == "event":
            payload = data['payload']['eventPayload']
            author = payload['author']['displayName']
            text = payload['text']

            chat_msg = {
                "author": author,
                "text": text,
                "sent": time.time()
            }
            self.log.info(f"<{author}> {text}")
            #self.on_message_cb(m)
            eventbus.publish(Events.CHAT_MESSAGE, data=chat_msg, source="restream")

    def loop(self):
        self.log.info("Starting WS run_forever")
        try:
            self.wsa.run_forever()
        except Exception as ex:
            self.log.error("Exception in restream", exc_info=ex)

    def start(self, token):
        self.log.info("Starting WS thread")
        self.running = True
        websocket.enableTrace = True
        url = CONFIG.get("restream.io", "chat_ws_url", vars={ "access_token": token })
        self.wsa = websocket.WebSocketApp(url, on_message=lambda _, message: self.on_message(message))
        
        self.thread.start()
    
    def shutdown(self):
        if not self.running:
            self.log.info("Refusing to shutdown.  Not started.")
            return

        running = False
        self.log.info("Shutting down..")
        self.wsa.close()

        self.thread.join()
