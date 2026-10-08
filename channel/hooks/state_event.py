#!/usr/bin/env python3
"""Pre/PostToolUse hook: report tool activity to the metachat channel.

POSTs {tool, phase, label, detail} to the channel's loopback state listener,
which turns it into an avatar state on stream (Dyson "reading" / "typing" /
"running") and a readable label for the review page ("Reading review.js").
detail, when present, is something worth showing in full on the pages: an
edit as a unified diff (before it lands), or a test run's pass/fail counts.
Always exits 0; a dead listener must never block tool use.
"""
import difflib
import json
import os
import re
import sys
import urllib.request

# Hooks run in parallel, so block_secrets refusing a credential file doesn't
# stop this one; never send their contents either.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from block_secrets import BLOCKED_PATTERNS

MAX_LABEL = 80
MAX_DIFF_LINES = 200
MAX_FILE_BYTES = 1_000_000
# pytest's closing summary, e.g. "== 1 failed, 46 passed in 0.31s ==".
TEST_SUMMARY = re.compile(r"^=*\s*(.*\b\d+ (?:passed|failed)\b.*) in [\d.]+s", re.M)


def basename(path):
    return os.path.basename(str(path).replace("\\", "/")) or "a file"


def describe(tool, tool_input, phase):
    """A short, speakable label for what the tool is doing.  After a tool
    finishes, the model is back to thinking about the result; once it stops
    (end of turn), it's doing nothing at all."""
    if phase == "Stop":
        return ""
    if phase == "PostToolUse":
        return "Thinking"
    if tool == "Read":
        return f"Reading {basename(tool_input.get('file_path', ''))}"
    if tool in ("Edit", "Write", "NotebookEdit"):
        return f"Editing {basename(tool_input.get('file_path') or tool_input.get('notebook_path', ''))}"
    if tool in ("Grep", "Glob"):
        return "Searching the code"
    if tool in ("Bash", "PowerShell"):
        return tool_input.get("description") or "Running a command"
    if tool == "WebSearch":
        return "Searching the web"
    if tool == "WebFetch":
        return "Reading a web page"
    if tool in ("Agent", "Task"):
        return "Handing off to a helper"
    return f"Using {tool}"


def is_secret(path):
    return any(re.search(p, path, re.IGNORECASE) for p in BLOCKED_PATTERNS)


def read_text(path):
    """The file's current text ('' if it doesn't exist yet), or None when
    it's too big or unreadable to diff."""
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError):
        return None


def edit_detail(tool, tool_input, cwd):
    """The pending Edit/Write as a unified diff against the file on disk
    (this runs before the tool, so the file is still the old version)."""
    path = str(tool_input.get("file_path") or "")
    if tool not in ("Edit", "Write") or not path or is_secret(path):
        return None
    before = read_text(path)
    if before is None:
        return None
    if tool == "Write":
        after = tool_input.get("content", "").replace("\r\n", "\n")
    else:
        old = tool_input.get("old_string", "").replace("\r\n", "\n")
        new = tool_input.get("new_string", "").replace("\r\n", "\n")
        if old and old in before:
            count = -1 if tool_input.get("replace_all") else 1
            after = before.replace(old, new, count)
        else:
            # Can't place it in the file: show the strings on their own.
            before, after = old, new

    try:
        rel = os.path.relpath(path, cwd or os.getcwd()).replace("\\", "/")
    except ValueError:
        rel = basename(path)
    lines = [line if line.endswith("\n") else line + "\n"
             for line in difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                              f"a/{rel}", f"b/{rel}", n=3)]
    if not lines:
        return None
    return {"kind": "edit", "file": rel, "diff": "".join(lines[:MAX_DIFF_LINES]),
            "truncated": len(lines) > MAX_DIFF_LINES}


def test_detail(tool, tool_input, response):
    """Pass/fail counts from a finished test run's output (pytest style)."""
    if tool not in ("Bash", "PowerShell"):
        return None
    if not re.search(r"\b(pytest|tests?)\b", str(tool_input.get("command", ""))):
        return None
    if isinstance(response, dict):
        output = f"{response.get('stdout', '')}\n{response.get('stderr', '')}"
    else:
        output = str(response or "")
    summaries = TEST_SUMMARY.findall(output)
    if not summaries:
        return None
    summary = summaries[-1]
    count = lambda word: sum(int(n) for n in re.findall(rf"(\d+) {word}", summary))
    passed, failed = count("passed"), count("failed") + count("errors?")
    return {"kind": "tests", "summary": summary.strip(), "passed": passed,
            "failed": failed, "ok": failed == 0}


def main():
    port = os.environ.get("METACHAT_STATE_PORT", "9011")
    try:
        data = json.load(sys.stdin)
        tool = data.get("tool_name", "")
        # The channel's own tools (speaking, set_mode) aren't "work" to show.
        if tool.startswith("mcp__metachat-channel__"):
            return 0
        phase = data.get("hook_event_name", "")
        tool_input = data.get("tool_input") or {}
        label = describe(tool, tool_input, phase)
        if phase == "PreToolUse":
            detail = edit_detail(tool, tool_input, data.get("cwd"))
        elif phase == "PostToolUse":
            detail = test_detail(tool, tool_input, data.get("tool_response"))
        else:
            detail = None
        payload = json.dumps({
            "tool": tool,
            "phase": phase,
            "label": label[:MAX_LABEL],
            "detail": detail,
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
