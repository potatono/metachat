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
        self.thread = None

    def on_message(self, rs_msg):
        # Temporary: log the raw payload so the platform/connection field
        # names below can be confirmed against live traffic.
        self.log.debug(f"Raw restream message: {rs_msg}")

        data = json.loads(rs_msg)
        action = data['action']

        if action == "event":
            payload = data['payload']['eventPayload']
            author = payload['author']['displayName']
            text = payload['text']

            chat_msg = {
                "author": author,
                "text": text,
                "sent": time.time(),
                # Restream merges chat from every platform by display name.
                # Publish which platform/connection the message came from so
                # consumers can verify identity beyond the spoofable name.
                "platform": data['payload'].get('eventSourceId'),
                "connection": data['payload'].get('connectionIdentifier'),
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
        finally:
            # Let ensure_connected restart us after a dropped connection.
            self.running = False

    def start(self, token):
        self.log.info("Starting WS thread")
        self.running = True
        websocket.enableTrace = False
        url = CONFIG.get("restream.io", "chat_ws_url", vars={ "access_token": token })
        self.wsa = websocket.WebSocketApp(url, on_message=lambda _, message: self.on_message(message))

        # A Thread can only be started once, so make a fresh one per start.
        self.thread = Thread(daemon=True, target=self.loop)
        self.thread.start()

    def shutdown(self):
        if not self.running:
            self.log.info("Refusing to shutdown.  Not started.")
            return

        self.running = False
        self.log.info("Shutting down..")
        self.wsa.close()

        self.thread.join()
