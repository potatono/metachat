"""Interactive test client that impersonates the remote channel server.

Connects to the coder bridge and lets you drive the §6 protocol by hand:

    python tools/bridge_client.py [ws://localhost:9010] [token]

Commands:
    say <text>                Dyson speaks (interruptible)
    say! <text>               Dyson speaks (not interruptible)
    state <mode> [n] [total]  set mode (plan|implement|review|idle)
    hunk [file]               send a fake diff hunk
    vocab <bad>:<good>        add a session STT correction
    raw <json>                send an arbitrary frame
    quit

Incoming frames (utterance/nav/barge_in) are printed as they arrive.
The token defaults to [coder] token in secrets.ini.
"""
import json
import sys
import os
import socket
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from websockets.sync.client import connect

from config import SECRETS

FAKE_DIFF = """@@ -42,7 +42,9 @@ def update_tracks(self):
-    if age > 30:
-        self.tracks.remove(track)
+    max_age = self.config.get("max_age", 30)
+    if age > max_age:
+        self.tracks.remove(track)
+        self.log.debug("expired track %s", track.id)
"""

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:9010"
    token = sys.argv[2] if len(sys.argv) > 2 else SECRETS.get("coder", "token", fallback="devtoken")

    seq = 0
    with connect(url) as ws:
        ws.send(json.dumps({
            "type": "hello", "token": token,
            "host": socket.gethostname(), "cwd": os.getcwd(),
        }))
        print(f"connected to {url}")

        def reader():
            try:
                for raw in ws:
                    print(f"\n<< {raw}\n> ", end="", flush=True)
            except Exception:
                print("\n[connection closed]")
                os._exit(0)

        threading.Thread(daemon=True, target=reader).start()

        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            cmd, _, rest = line.partition(" ")

            if cmd in ("quit", "exit"):
                break
            elif cmd in ("say", "say!"):
                seq += 1
                ws.send(json.dumps({
                    "type": "say", "text": rest, "seq": seq,
                    "interruptible": cmd == "say",
                }))
            elif cmd == "state":
                parts = rest.split()
                msg = {"type": "state", "mode": parts[0] if parts else "idle"}
                if len(parts) > 1:
                    msg["chunk"] = int(parts[1])
                if len(parts) > 2:
                    msg["chunks"] = int(parts[2])
                ws.send(json.dumps(msg))
            elif cmd == "hunk":
                ws.send(json.dumps({
                    "type": "hunk", "index": 1, "total": 3,
                    "file": rest or "src/tracker.py", "lang": "python",
                    "start": 42, "end": 50, "diff": FAKE_DIFF,
                    "summary": "Swapped the hardcoded age threshold for a config value.",
                }))
            elif cmd == "vocab":
                bad, _, good = rest.partition(":")
                ws.send(json.dumps({"type": "vocab", "corrections": {bad: good}}))
            elif cmd == "raw":
                ws.send(rest)
            else:
                print("commands: say, say!, state, hunk, vocab, raw, quit")

if __name__ == "__main__":
    main()
