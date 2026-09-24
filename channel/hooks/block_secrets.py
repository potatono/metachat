#!/usr/bin/env python3
"""PreToolUse hook: block reading credential files.

On stream, a helpful `cat .env` puts secrets on screen.  Exit code 2 makes
Claude Code block the tool call and show stderr to the model.
"""
import json
import re
import sys

BLOCKED_PATTERNS = [
    r"\.env(?:\.\w+)?\b",
    r"secrets\.ini",
    r"\.pem\b",
    r"\.key\b",
    r"id_rsa|id_ed25519",
    r"\.npmrc|\.netrc|\.pypirc",
    r"credentials",
    r"\.aws/|\.ssh/",
    r"token\.json|\.credentials\.json",
]

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if tool in ("Read", "Edit", "Write", "NotebookEdit"):
        text = str(tool_input.get("file_path", ""))
    elif tool == "Bash":
        text = str(tool_input.get("command", ""))
    else:
        return 0

    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            print(
                f"Blocked: '{pattern}' matches a credential file pattern. "
                "Secrets must never be read or shown on stream.",
                file=sys.stderr,
            )
            return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
