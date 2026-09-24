# metachat-channel

Claude Code channel server for voice pair programming with Dyson.
Runs on the code host (porcupine / WSL2 / SSH host); dials out to metachat's
coder bridge on anarchy. Design: `docs/PAIR_PROGRAMMING_PLAN.md`.

## Setup (code host)

```bash
cd channel
bun install
```

Register the MCP server with Claude Code in the target project:

```bash
claude mcp add metachat-channel \
  --env METACHAT_BRIDGE_URL=ws://anarchy:9010 \
  --env METACHAT_BRIDGE_TOKEN=<token from anarchy's secrets.ini [coder]> \
  -- bun run /path/to/metachat/channel/src/index.ts
```

From an SSH host off the LAN, add `RemoteForward 9010 anarchy:9010` to
`~/.ssh/config` and use `ws://localhost:9010` instead.

## Launch (pinned invocation)

Channels are a research preview; custom channels need the development flag.
This is the invocation verified working — if it fails after a Claude Code
update, re-check https://code.claude.com/docs/en/channels-reference.md:

```bash
claude --dangerously-load-development-channels server:metachat-channel
```

First use shows a full-screen "I am using this for local development"
confirmation.

Verified behavior (2026-09): inbound channel events queue until the current
turn ends — they never interrupt Claude mid-turn. Barge-in therefore only
stops TTS on anarchy; Escape in the terminal is the hard stop.

## Testing without anarchy

```bash
METACHAT_BRIDGE_TOKEN=devtoken bun run proto/fake_bridge.ts
```

Then open http://localhost:9010, launch Claude Code with the channel (env
`METACHAT_BRIDGE_URL=ws://localhost:9010 METACHAT_BRIDGE_TOKEN=devtoken`),
type into the page, and watch utterances arrive in the session and `say`
frames come back.

## Installing into a target project

1. Copy `skills/pair/` to the project's `.claude/skills/pair/` (or symlink).
2. Merge `settings.example.json` into the project's `.claude/settings.json`,
   fixing the hook script paths (secrets block + avatar state events).
3. Copy `templates/STYLE_DEBT.md` to the project's `docs/STYLE_DEBT.md` and
   add a style section with before/after examples to its CLAUDE.md.

## Security invariants

- Never declare `claude/channel/permission` (enforced by a startup assert):
  nothing reachable through the bridge may approve tool use.
- The bridge only relays the streamer's mic/keyboard; chat is never
  forwarded as instructions.
