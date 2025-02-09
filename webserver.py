import os
import re
import http.server
import mimetypes
import threading
import json
import urllib
import websockets
from websockets.sync.server import serve as wbs_serve

from config import *
from logs import *

class WebserverApp:
    def __init__(self, on_copilot_message=None):
        self.log = Logger("webserver")
        self.running = False
        self.address = CONFIG.get("webserver", "address", fallback="0.0.0.0")
        self.port = CONFIG.getint("webserver", "port", fallback=9000)
        self.static_path = CONFIG.get("webserver", "static_path", fallback="public")
        self.last_message = None
        self.last_active_file = None
        self.websockets = set()
        self.on_copilot_message = on_copilot_message

        self.init_webserver()

    def init_webserver(self):
        class RequestHandler(http.server.BaseHTTPRequestHandler):
            def respond(this, response, type, code=200):
                this.send_response(code)
                this.send_header('Content-type', type)
                this.end_headers()
                
                this.wfile.write(response)
                this.wfile.write("\n".encode())

            def do(this, method, data=None):
                try:
                    
                    response = self.handle_webserver_request(method, this.path, data)
                    if response:
                        mime_type, _ = mimetypes.guess_type(this.path)

                        if type(response) is dict:
                            this.respond(json.dumps(response).encode(), 'application/json')
                        elif type(response) is str:
                            this.respond(response.encode(), mime_type or 'text/html')
                        else:
                            this.respond(response, mime_type or 'text/html')
                    else:
                        this.respond("Not Found", "text/plain", 404)

                except Exception as ex:
                    self.log.error(ex)
                    this.respond(str(ex), "text/plain", 500)

            def do_GET(this):
                this.do("GET")

            def do_POST(this):
                data = this.rfile.read(int(this.headers['Content-Length'])).decode()
                this.do("POST", data)

        self.webserver = http.server.ThreadingHTTPServer((self.address, self.port), RequestHandler)
        self.webthread = threading.Thread(target=self.run_webserver)
        self.wbsthread = threading.Thread(target=self.run_wbsserver)

    def ensure_connected(self):
        if not self.running:
            self.running = True
            self.webthread.start()
            self.wbsthread.start()

    def shutdown(self):
        if self.running:
            self.log.info("Shutting down web server...")
            self.running = False
            self.webserver.shutdown()
            self.webthread.join()

            self.log.info("Shutting down WebSocket server...")

            # Make a copy so when we close the sockets, we don't modify the set
            websockets = self.websockets.copy()
            for socket in websockets:
                socket.close()
            self.wbsserver.shutdown()
            self.wbsthread.join()

    def run_webserver(self):
        self.log.info(f"Starting web server on {self.address}:{self.port}...")
        self.webserver.serve_forever()

    def run_wbsserver(self):
        self.log.info(f"Starting WebSocket server on {self.address}:{self.port + 1}...")
        with wbs_serve(self.handle_websocket_connect, self.address, self.port + 1) as server:
            self.wbsserver = server
            server.serve_forever()

    def path_is_valid(self, path):
        result = re.match(r'^[a-zA-Z0-9_\-/]+(?:\.[a-zA-Z0-9]+)?$', path)
        self.log.debug(f"Path is valid: {result}")
        return result
    
    def get_absolute_path(self, path):
        return os.path.abspath(self.static_path + path)
    
    def path_is_valid_and_exists(self, path):
        abspath = self.get_absolute_path(path)
        self.log.debug(f"Checking if path exists: {abspath}")
        return self.path_is_valid(path) and os.path.exists(abspath)
    
    def handle_webserver_request(self, method, path, data=None):

        if method == "POST" and path == "/active_file":
            return self.handle_update_active_file(json.loads(data))
        elif method != "GET":
            return None
        elif path == "/message":
            return self.last_message
        elif path == "/":
            return self.handle_get_path("/index.html")
        elif self.path_is_valid_and_exists(path):
            return self.handle_get_path(path)
        
        return None

    def extract_query_params(self, path):
        parsed_url = urllib.parse.urlparse(path)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        return query_params

    def handle_get_path(self, path):
        mimetype, _ = mimetypes.guess_type(path)
        mode = "r" if mimetype and mimetype.startswith("text/") else "rb"

        with open(self.get_absolute_path(path), mode) as file:
            return file.read()

    def handle_websocket_connect(self, websocket):
        self.log.info(f"WebSocket connection established")
        self.websockets.add(websocket)
        try:
            for message in websocket:
                self.log.info(f"Received WebSocket message: {message}")
                if self.on_copilot_message:
                    self.on_copilot_message(message)

        except websockets.exceptions.ConnectionClosed as e:
            self.log.info(f"WebSocket connection closed: {e}")
        finally:
            self.websockets.remove(websocket)

    def handle_update_active_file(self, data):
        self.last_active_file = data
        self.log.debug(f"Updated active file: {data['path']} {data['language']}")
        return { "status": "ok" }

    def say(self, message):
        self.log.info("Updated webserver message")
        self.last_message = message
        self.broadcast_message(message)

    def broadcast_message(self, message):
        if self.websockets:
            for socket in self.websockets:
                socket.send(message)

