import os
import re
import http.server
import io
import mimetypes
import threading
import json
import tarfile
import urllib

from config import *
from logs import *

class WebserverApp:
    """Static HTTP server for public/: the request console at / and the
    review page at /review.  Both talk to the coder bridge directly.

    Also serves the channel's one-line setup under /setup/ (see
    channel/setup/channel.sh)."""

    def __init__(self):
        self.log = Logger("webserver")
        self.running = False
        self.address = CONFIG.get("webserver", "address", fallback="0.0.0.0")
        self.port = CONFIG.getint("webserver", "port", fallback=9000)
        self.static_path = CONFIG.get("webserver", "static_path", fallback="public")
        self.channel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel")

        self.init_webserver()

    def init_webserver(self):
        class RequestHandler(http.server.BaseHTTPRequestHandler):
            def respond(this, response, type, code=200, newline=True):
                this.send_response(code)
                this.send_header('Content-type', type)
                this.end_headers()

                this.wfile.write(response)
                # Binary bodies stay byte-exact (a stray newline after a
                # gzip stream makes tar fail).
                if newline:
                    this.wfile.write("\n".encode())

            def do(this, method, data=None):
                try:
                    # Strip any query string; routing and static lookup use
                    # the bare path (pages read query params client-side).
                    path = urllib.parse.urlparse(this.path).path
                    # The setup scripts point back at whatever host the
                    # caller reached us on.
                    base_url = f"http://{this.headers.get('Host', f'{self.address}:{self.port}')}"
                    response = self.handle_webserver_request(method, path, data, base_url)
                    if response:
                        mime_type, _ = mimetypes.guess_type(path)

                        if type(response) is dict:
                            this.respond(json.dumps(response).encode(), 'application/json')
                        elif type(response) is str:
                            this.respond(response.encode(), mime_type or 'text/html')
                        else:
                            this.respond(response, mime_type or 'text/html', newline=False)
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

    def handle_webserver_request(self, method, path, data=None, base_url=None):
        if method != "GET":
            return None
        elif path.startswith("/setup/"):
            return self.handle_setup(path, base_url)
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

    SETUP_SCRIPTS = ("/setup/channel.sh", "/setup/channel.ps1")
    SETUP_SKIP = {"node_modules", "__pycache__", ".git"}

    def handle_setup(self, path, base_url):
        if path in self.SETUP_SCRIPTS:
            # newline="" keeps the .sh file's LF endings intact on Windows.
            with open(os.path.join(self.channel_path, path.lstrip("/")), newline="") as file:
                return file.read().replace("__METACHAT_SETUP_URL__", base_url)
        elif path == "/setup/channel.tgz":
            return self.build_channel_tarball()

        return None

    def build_channel_tarball(self):
        # Built per request so a new host always gets what the bridge runs.
        def skip(info):
            return None if self.SETUP_SKIP.intersection(info.name.split("/")) else info

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for name in sorted(os.listdir(self.channel_path)):
                tar.add(os.path.join(self.channel_path, name), arcname=name, filter=skip)
        return buffer.getvalue()
