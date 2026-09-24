#!/usr/bin/env python3
"""Pre/PostToolUse hook: report tool activity to the metachat channel.

POSTs {tool, phase} to the channel's loopback state listener, which turns it
into an avatar state on stream (Dyson "reading" / "typing" / "running").
Always exits 0; a dead listener must never block tool use.
"""
import json
import os
import sys
import urllib.request

def main():
    port = os.environ.get("METACHAT_STATE_PORT", "9011")
    try:
        data = json.load(sys.stdin)
        payload = json.dumps({
            "tool": data.get("tool_name", ""),
            "phase": data.get("hook_event_name", ""),
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
