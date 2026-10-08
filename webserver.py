import os
import re
import http.server
import mimetypes
import threading
import json
import urllib

from config import *
from logs import *

class WebserverApp:
    """Static HTTP server for public/: the request console at / and the
    review page at /review.  Both talk to the coder bridge directly."""

    def __init__(self):
        self.log = Logger("webserver")
        self.running = False
        self.address = CONFIG.get("webserver", "address", fallback="0.0.0.0")
        self.port = CONFIG.getint("webserver", "port", fallback=9000)
        self.static_path = CONFIG.get("webserver", "static_path", fallback="public")

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
                    # Strip any query string; routing and static lookup use
                    # the bare path (pages read query params client-side).
                    path = urllib.parse.urlparse(this.path).path
                    response = self.handle_webserver_request(method, path, data)
                    if response:
                        mime_type, _ = mimetypes.guess_type(path)

                        if type(response) is dict:
                            this.respond(json.dumps(response).encode(), 'application/json')
                        elif type(response) is str:
                            this.respond(response.encode(), mime_type or 'text/html')
                        else:
                            this.respond(response, mime_type or 'text/html')
                    else:
                        this.respond("Not Found".encode(), "text/plain", 404)

                except Exception as ex:
                    self.log.error(ex)
                    this.respond(str(ex), "text/plain", 500)

            def do_GET(this):
                this.do("GET")

        self.webserver = http.server.ThreadingHTTPServer((self.address, self.port), RequestHandler)
        self.webthread = threading.Thread(target=self.run_webserver)

    def ensure_connected(self):
        if not self.running:
            self.running = True
            self.webthread.start()

    def shutdown(self):
        if self.running:
            self.log.info("Shutting down web server...")
            self.running = False
            self.webserver.shutdown()
            self.webthread.join()

    def run_webserver(self):
        self.log.info(f"Starting web server on {self.address}:{self.port}...")
        self.webserver.serve_forever()

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
        if method != "GET":
            return None
        elif path == "/":
            return self.handle_get_path("/index.html")
        elif self.path_is_valid_and_exists(path):
            return self.handle_get_path(path)

        return None

    def handle_get_path(self, path):
        # Serve index.html for directories (e.g. /review/).
        if os.path.isdir(self.get_absolute_path(path)):
            path = path.rstrip("/") + "/index.html"

        mimetype, _ = mimetypes.guess_type(path)
        mode = "r" if mimetype and mimetype.startswith("text/") else "rb"

        with open(self.get_absolute_path(path), mode) as file:
            return file.read()
