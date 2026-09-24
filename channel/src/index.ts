// metachat-channel: a Claude Code channel (MCP stdio server) that connects a
// session on the code host to metachat's coder bridge on the streaming PC.
//
// Launch (research preview; see README.md for the pinned invocation):
//   claude --dangerously-load-development-channels server:metachat-channel

import os from "node:os";
import { createServer } from "node:http";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { BridgeClient, type BridgeMessage } from "./bridge.ts";
import { ReviewSession, type Hunk } from "./review.ts";

// All logging goes to stderr; stdout is the MCP protocol stream.
const log = (line: string) => console.error(`[metachat-channel] ${line}`);

const BRIDGE_URL = process.env.METACHAT_BRIDGE_URL ?? "ws://localhost:9010";
const BRIDGE_TOKEN = process.env.METACHAT_BRIDGE_TOKEN ?? "";

if (!BRIDGE_TOKEN) {
  log("METACHAT_BRIDGE_TOKEN is not set; the bridge will reject us.");
}

type Mode = "plan" | "implement" | "review" | "idle";
let mode: Mode = "idle";
let saySeq = 0;

const capabilities = {
  tools: {},
  experimental: {
    // The channel capability. We deliberately do NOT declare
    // 'claude/channel/permission': anyone who can reach the bridge must
    // never be able to approve tool use remotely.
    "claude/channel": {},
  } as Record<string, unknown>,
};

if ("claude/channel/permission" in capabilities.experimental) {
  throw new Error("permission relay must never be declared on this channel");
}

const server = new Server(
  { name: "metachat-channel", version: "0.1.0" },
  { capabilities },
);

function notifyClaude(content: string, meta: Record<string, string> = {}) {
  server
    .notification({
      method: "notifications/claude/channel",
      params: { content, meta: { source: "metachat", mode, ...meta } },
    })
    .catch((err) => log(`notification failed: ${err}`));
}

const bridge = new BridgeClient({
  url: BRIDGE_URL,
  token: BRIDGE_TOKEN,
  host: os.hostname(),
  cwd: process.cwd(),
  log,
  onMessage: (msg: BridgeMessage) => {
    switch (msg.type) {
      case "utterance": {
        const text = String(msg.text ?? "");
        if (mode === "review" && review.active) {
          // Buffered as a review comment; questions also reach Claude
          // immediately (plan §4.3).
          review.comment(text);
        } else {
          notifyClaude(text, { input: String(msg.source ?? "microphone") });
        }
        break;
      }
      case "nav":
        review.nav(String(msg.action ?? ""));
        break;
      case "barge_in":
        // Informational: the bridge already stopped TTS on its side.
        log("barge_in received");
        break;
      default:
        log(`unhandled bridge message type: ${msg.type}`);
    }
  },
});

const review = new ReviewSession(
  (msg) => bridge.send(msg),
  (content, meta) => notifyClaude(content, meta),
  log,
);

// Splits a reply into sentence-sized say messages so the avatar starts
// talking after the first sentence (plan §5).
export function splitSentences(text: string): string[] {
  const parts = text.match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g);
  return (parts ?? [text]).map((s) => s.trim()).filter(Boolean);
}

const TOOLS = [
  {
    name: "reply",
    description:
      "Speak to the streamer as Dyson. Short conversational sentences, no raw " +
      "identifiers or line numbers. Emoji set the avatar's emotion.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "What Dyson says out loud." },
        interruptible: {
          type: "boolean",
          description: "Whether the streamer talking should cut this off (default true).",
        },
      },
      required: ["text"],
    },
  },
  {
    name: "set_mode",
    description: "Set the pairing mode shown on stream: plan, implement, review, or idle.",
    inputSchema: {
      type: "object",
      properties: {
        mode: { type: "string", enum: ["plan", "implement", "review", "idle"] },
        chunk: { type: "number", description: "Current chunk number (implement/review)." },
        total: { type: "number", description: "Total chunks planned." },
      },
      required: ["mode"],
    },
  },
  {
    name: "review_begin",
    description:
      "Start a hunk-by-hunk review. Hands the full hunk list to the bridge; " +
      "navigation happens without the model until the review completes.",
    inputSchema: {
      type: "object",
      properties: {
        hunks: {
          type: "array",
          items: {
            type: "object",
            properties: {
              file: { type: "string" },
              lang: { type: "string" },
              start: { type: "number" },
              end: { type: "number" },
              diff: { type: "string" },
              summary: { type: "string", description: "One spoken-style sentence about the hunk." },
            },
            required: ["file", "diff", "summary"],
          },
        },
      },
      required: ["hunks"],
    },
  },
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args = {} } = request.params;

  switch (name) {
    case "reply": {
      const text = String(args.text ?? "");
      const interruptible = args.interruptible !== false;
      for (const sentence of splitSentences(text)) {
        bridge.send({ type: "say", text: sentence, seq: ++saySeq, interruptible });
      }
      return { content: [{ type: "text", text: "spoken" }] };
    }
    case "set_mode": {
      mode = String(args.mode ?? "idle") as Mode;
      const state: BridgeMessage = { type: "state", mode };
      if (args.chunk !== undefined) state.chunk = Number(args.chunk);
      if (args.total !== undefined) state.chunks = Number(args.total);
      bridge.send(state);
      return { content: [{ type: "text", text: `mode set to ${mode}` }] };
    }
    case "review_begin": {
      const hunks = (Array.isArray(args.hunks) ? args.hunks : []) as Hunk[];
      mode = "review";
      bridge.send({ type: "state", mode });
      review.begin(hunks);
      return {
        content: [{
          type: "text",
          text: `review started with ${hunks.length} hunks; navigation is handled ` +
            "for you — you'll get one event when the review completes",
        }],
      };
    }
    default:
      throw new Error(`unknown tool: ${name}`);
  }
});

// Loopback listener for hook scripts (channel/hooks/state_event.py): hooks
// can't call MCP tools, so they POST {tool, phase} here and we turn it into
// an avatar state on stream ("reading", "typing", "running tests").
const STATE_PORT = Number(process.env.METACHAT_STATE_PORT ?? 9011);
const AVATAR_HINTS: Record<string, string> = {
  Read: "reading", Grep: "reading", Glob: "reading", WebFetch: "reading",
  Edit: "typing", Write: "typing", NotebookEdit: "typing",
  Bash: "running", Task: "thinking",
};

function startStateListener() {
  createServer((req, res) => {
    if (req.method === "POST" && req.url === "/state") {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        try {
          const { tool = "", phase = "" } = JSON.parse(body || "{}");
          bridge.send({
            type: "state",
            mode,
            avatar: AVATAR_HINTS[tool] ?? "working",
            activity: `${phase}:${tool}`,
          });
        } catch (err) {
          log(`bad state POST: ${err}`);
        }
        res.end("ok");
      });
    } else {
      res.statusCode = 404;
      res.end();
    }
  }).listen(STATE_PORT, "127.0.0.1", () =>
    log(`state listener on 127.0.0.1:${STATE_PORT}`),
  );
}

async function main() {
  bridge.connect();
  startStateListener();
  const transport = new StdioServerTransport();
  await server.connect(transport);
  log(`ready; bridge=${BRIDGE_URL} host=${os.hostname()}`);
}

main().catch((err) => {
  log(`fatal: ${err}`);
  process.exit(1);
});
