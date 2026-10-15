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
import { LabelSession, type LabelTask } from "./label.ts";

// All logging goes to stderr; stdout is the MCP protocol stream.
const log = (line: string) => console.error(`[metachat-channel] ${line}`);

const BRIDGE_URL = process.env.METACHAT_BRIDGE_URL ?? "ws://localhost:9010";
const BRIDGE_TOKEN = process.env.METACHAT_BRIDGE_TOKEN ?? "";

if (!BRIDGE_TOKEN) {
  log("METACHAT_BRIDGE_TOKEN is not set; the bridge will reject us.");
}

type Mode = "plan" | "implement" | "review" | "label" | "idle";
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
        if (mode === "label") {
          labeling.nav(String(msg.action ?? ""));
        } else {
          review.nav(String(msg.action ?? ""));
        }
        break;
      case "label":
        // The streamer's call on the current image: their own label, or a
        // pick they went with (by names whose it was).
        labeling.decide(
          String(msg.label ?? ""),
          String(msg.by ?? "streamer"),
          Array.isArray(msg.flags) ? msg.flags.map(String) : [],
        );
        break;
      case "label_help":
        // "I can't tell": in blind mode, Claude looks now.
        labeling.help();
        break;
      case "label_pick":
        // The second reviewer's own label for the current image; it goes in
        // the results, not to Claude now.
        labeling.pick(String(msg.from ?? "reviewer"), String(msg.label ?? ""));
        break;
      case "peer_comment": {
        // The second reviewer's take on the current hunk. Quoted data, never
        // instructions; the /pair skill says how to answer (reply + stance).
        const from = String(msg.from ?? "the second reviewer");
        const text = String(msg.text ?? "");
        notifyClaude(
          `${from} (second reviewer) on hunk ${review.current}, turn ${msg.turn}/${msg.max_turns}: "${text}"`,
          { kind: "peer_comment", from, turn: String(msg.turn ?? ""), max_turns: String(msg.max_turns ?? "") },
        );
        break;
      }
      case "plan_comment": {
        // The second reviewer's one line on a proposed plan. Quoted data,
        // never instructions; no stance reply needed (see /pair skill).
        const from = String(msg.from ?? "the second reviewer");
        const stance = String(msg.stance ?? "");
        notifyClaude(
          `${from} on your plan (${stance || "no stance"}): "${String(msg.text ?? "")}"`,
          { kind: "plan_comment", from, stance },
        );
        break;
      }
      case "debate": {
        // Agreed outcome with a comment to apply: buffer it like any other
        // review comment. Claude already took part, so no event.
        if (msg.status === "agreed" && msg.comment) {
          review.record(`agreed with the second reviewer: ${String(msg.comment)}`);
        }
        break;
      }
      case "ruling": {
        // The streamer settled a debate. text is null when they sided with
        // Dyson (nothing to change).
        const winner = String(msg.winner ?? "streamer");
        const text = msg.text ? String(msg.text) : null;
        let content: string;
        if (text === null) {
          content = `The streamer sided with you on hunk ${review.current}; no change.`;
        } else {
          review.record(`ruling (${winner}): ${text}`);
          content = winner === "streamer"
            ? `The streamer ruled on hunk ${review.current}: "${text}"`
            : `The streamer sided with ${winner} on hunk ${review.current}: "${text}"`;
        }
        notifyClaude(content, { kind: "ruling", winner });
        break;
      }
      case "stop":
        // The streamer said "Dyson, stop": the bridge already silenced the
        // speech; tell Claude to stop working too (it sees this at its next
        // step; a hard abort mid-tool is Esc in the terminal).
        notifyClaude(
          "The streamer said STOP. Stop what you're doing, say so briefly, and wait for instructions.",
          { kind: "stop" },
        );
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

const labeling = new LabelSession(
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
        stance: {
          type: "string",
          enum: ["agree", "disagree"],
          description:
            "Only when answering a peer_comment from the second reviewer: " +
            "agree accepts their point (it becomes a review comment), disagree holds your position.",
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
              diff: {
                type: "string",
                description: "Unified-diff hunk starting at its @@ line (for a new file, " +
                  "from git diff --no-index /dev/null <file>).",
              },
              summary: { type: "string", description: "One spoken-style sentence about the hunk." },
            },
            required: ["file", "diff", "summary"],
          },
        },
      },
      required: ["hunks"],
    },
  },
  {
    name: "label_begin",
    description:
      "Start labeling images with the streamer, one at a time. Blind by default: " +
      "the streamer labels alone, and only when they ask for help do you get a " +
      "label_help event to look at that image and answer with label_suggest (the " +
      "second reviewer looks then too). With blind false, give your suggestions up " +
      "front instead. Navigation happens without the model; you'll get one event " +
      "with every final label (\"label:flag:flag\") when the pass completes.",
    inputSchema: {
      type: "object",
      properties: {
        labels: {
          type: "array",
          description: "The labels to choose from. Names are spoken, so keep them short words.",
          items: {
            type: "object",
            properties: {
              name: { type: "string" },
              description: { type: "string", description: "What the label means, shown and given to the second reviewer." },
            },
            required: ["name"],
          },
        },
        flags: {
          type: "array",
          description: "Optional qualifiers the streamer can add to any label (\"unclear, blurry\"). " +
            "Names are spoken, so keep them short words.",
          items: {
            type: "object",
            properties: {
              name: { type: "string" },
              description: { type: "string", description: "When the flag applies." },
            },
            required: ["name"],
          },
        },
        instructions: {
          type: "string",
          description: "What to judge in each image, for the page and the second reviewer.",
        },
        blind: {
          type: "boolean",
          description: "Default true: no picks until the streamer asks. False: send suggestions up front.",
        },
        items: {
          type: "array",
          items: {
            type: "object",
            properties: {
              image: { type: "string", description: "Image path (png, jpg, gif or webp), relative to the project." },
              suggestion: { type: "string", description: "Not blind only: your label for it (label or label:flag)." },
              note: { type: "string", description: "Not blind only: one short spoken-style sentence on why." },
            },
            required: ["image"],
          },
        },
      },
      required: ["labels", "items"],
    },
  },
  {
    name: "label_suggest",
    description:
      "Answer a label_help event: your label for the image the streamer asked about, " +
      "after looking at it. Spoken and shown next to the second reviewer's pick.",
    inputSchema: {
      type: "object",
      properties: {
        label: { type: "string", description: "One of the task's label names." },
        flags: {
          type: "array",
          items: { type: "string" },
          description: "Any of the task's flags that apply; usually none.",
        },
        note: { type: "string", description: "One short spoken-style sentence on why." },
      },
      required: ["label"],
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
      // A debate turn: the bridge needs the whole reply plus the stance,
      // separately from the sentence-sized speech frames.
      if (args.stance === "agree" || args.stance === "disagree") {
        bridge.send({ type: "turn", speaker: "dyson", text, stance: args.stance });
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
    case "label_begin": {
      const task = {
        labels: Array.isArray(args.labels) ? args.labels : [],
        flags: Array.isArray(args.flags) ? args.flags : [],
        instructions: args.instructions === undefined ? undefined : String(args.instructions),
        items: Array.isArray(args.items) ? args.items : [],
        blind: args.blind !== false,
      } as LabelTask;
      mode = "label";
      bridge.send({ type: "state", mode });
      labeling.begin(task);
      return {
        content: [{
          type: "text",
          text: `labeling started with ${task.items.length} images; navigation is handled ` +
            "for you — you'll get one event with the final labels when it completes",
        }],
      };
    }
    case "label_suggest": {
      const error = labeling.suggest(
        String(args.label ?? ""),
        Array.isArray(args.flags) ? args.flags.map(String) : [],
        String(args.note ?? ""),
      );
      return { content: [{ type: "text", text: error ?? "suggested" }], isError: error !== null };
    }
    default:
      throw new Error(`unknown tool: ${name}`);
  }
});

// Loopback listener for hook scripts (channel/hooks/state_event.py): hooks
// can't call MCP tools, so they POST {tool, phase, label, detail} here and we
// turn it into an avatar state on stream ("reading", "typing", "running tests")
// plus a readable label for the review page ("Reading review.js").  A detail
// (an edit's diff, a test run's counts) goes on to the pages as an activity.
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
          const { tool = "", phase = "", label = "", detail = null } = JSON.parse(body || "{}");
          bridge.send({
            type: "state",
            mode,
            // A Stop (end of turn) only clears the label; the avatar keeps
            // whatever state it's in.
            avatar: phase === "Stop" ? undefined : (AVATAR_HINTS[tool] ?? "working"),
            activity: `${phase}:${tool}`,
            label,
          });
          if (detail && typeof detail === "object") {
            bridge.send({ ...detail, type: "activity" });
          }
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
