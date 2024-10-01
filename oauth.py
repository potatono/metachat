from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qsl
from hashlib import md5
from random import random

from config import *
from logs import *

import requests

''' Web server for handling OAuth redirect backs '''
class OAuthApp():
    thread = None
    server = None
    token = None
    code = None
    running = False

    def __init__(self, config_section):
        self.cs = config_section
        self.log = Logger(f"OAuth {config_section}")
        self.state = md5(bytes(str(random()), encoding='utf8')).hexdigest()

        self.client_id = CONFIG.get(self.cs, "client_id")
        self.client_secret = SECRETS.get(self.cs, "client_secret")
        self.refresh_token = SECRETS.get(self.cs, "refresh_token", fallback=None)
        self.token_url = CONFIG.get(self.cs, "token_url", vars={ "state": self.state })
        self.token_auth = CONFIG.get(self.cs, "token_auth", fallback="basic")

        self.auth_url = CONFIG.get(self.cs, "auth_url", vars={ "state": self.state })
        self.port = CONFIG.getint(self.cs, "port")
        self.redirect_url = CONFIG.get(self.cs, "oauth_redirect_url")

        self.server = HTTPServer(("localhost", self.port), OAuthApp.WebHandler)
        self.thread = Thread(name=f"Transcript.ServerApp.{self.port}", daemon=True, target=self.loop)
        self.server.app = self

    def loop(self):
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass

        self.server.server_close()
        self.log.info(f"Server stopped..")

    def start(self):
        self.running = True
        self.log.info(f"Starting server at http://localhost:{self.port}...")
        self.thread.start()

    def shutdown(self):
        if not self.running:
            self.log.info(f"Refusing to shutdown, not started.")
            return
        
        self.running = False
        self.log.info("Shutting down server...")
        self.server.shutdown()
        self.log.info("Shutdown complete.  Joining thread.")
        self.thread.join()
        self.log.info("Join complete.")

    def request_token(self, exchange, exchange_type="code"):
        grant_types = { "code": "authorization_code", "refresh_token": "refresh_token" }

        data = { 
            "grant_type": grant_types[exchange_type],
            "redirect_uri": self.redirect_url,
            exchange_type: exchange
        }

        if self.token_auth == "param":
            data['client_id'] = self.client_id
            data['client_secret'] = self.client_secret
            resp = requests.post(self.token_url, data)
        else:
            resp = requests.post(self.token_url, data, auth=(self.client_id, self.client_secret))

        result = resp.json()
        #self.log.info(result)

        if resp.status_code != 200:
            print(result['error']['message'])
            self.refresh_token = None
            self.token = None
        else:
            self.refresh_token =  result['refresh_token']
            self.token = result['access_token']
    
        SECRETS.set(self.cs, "refresh_token", self.refresh_token)
        with open(SECRETS_FILE, "w") as f:
            f.write("\n" * 100)
            SECRETS.write(f)

        return self.token

    def get_token(self):
        if self.token:
            pass
        elif self.refresh_token:
            self.token = self.request_token(self.refresh_token, "refresh_token")
        elif self.code:
            self.token = self.request_token(self.code)
        elif not self.running:
            self.log.info("Need interactive authorization.  Starting.")
            self.start()

        if self.token and self.running:
            self.log.info("Token received.  Shutting down server.")
            self.shutdown()

        return self.token

    class WebHandler(BaseHTTPRequestHandler):           
        def page(self, message="", title="Transcript Tool"):
            return (
                f"<?DOCTYPE html>"
                f"<html><head><title>{title}</title>"
                f"<style>BODY {{ font-family: sans-serif; }}</style></head>"
                f"<body><h1>{title}</h1>"
                f"<p>{message}</p>"
                f"</body></html"
            )

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qsl(u.query)
            params = { x[0]:x[1] for x in q }

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()

            if (u.path == '/oauth'):
                if (params['state'] != self.server.app.state):
                    html = self.page("Bad State", "Error")

                #self.request_token(params['code'])
                #application.code = params['code']
                self.server.app.code = params['code']
                html = self.page("All set")
            else:
                html = self.page(f"Please <a href='{self.server.app.auth_url}'>authorize</a>.")

            self.wfile.write(bytes(html, "utf-8"))
