// Review-mode state: the channel owns hunk navigation so "next"/"back" are
// instant, with no model round trip (plan §4.3). Comments are buffered per
// hunk and delivered to Claude in one "review complete" event; questions are
// forwarded immediately for a spoken answer.

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import type { BridgeMessage } from "./bridge.ts";

export interface Hunk {
  file: string;
  lang?: string;
  start?: number;
  end?: number;
  diff: string;
  summary: string;
}

const QUESTION_RE = /\?\s*$|^(?:why|what|how|when|where|who|does|do|did|is|are|was|can|could|should|would|will)\b/i;

export class ReviewSession {
  private hunks: Hunk[] = [];
  private cursor = 0;
  private contextLines = new Map<number, number>();
  private comments = new Map<number, string[]>();
  active = false;

  constructor(
    private send: (msg: BridgeMessage) => void,
    private notify: (content: string, meta?: Record<string, string>) => void,
    private log: (line: string) => void,
  ) {}

  begin(hunks: Hunk[]) {
    this.hunks = hunks;
    this.cursor = 0;
    this.contextLines.clear();
    this.comments.clear();
    this.active = hunks.length > 0;
    if (this.active) this.sendCurrent();
  }

  private sendCurrent(overrides: Partial<Hunk> & { content?: string } = {}) {
    const hunk = this.hunks[this.cursor];
    this.send({
      type: "hunk",
      index: this.cursor + 1,
      total: this.hunks.length,
      ...hunk,
      ...overrides,
    });
    this.jumpEditor(hunk);
  }

  // When run from the VS Code integrated terminal, jump the editor to the
  // hunk being reviewed.
  private jumpEditor(hunk: Hunk) {
    if (process.env.TERM_PROGRAM !== "vscode" || !hunk.start) return;
    try {
      spawnSync("code", ["-g", `${hunk.file}:${hunk.start}`], { timeout: 3000 });
    } catch {
      // best-effort
    }
  }

  nav(action: string) {
    if (!this.active) return;
    switch (action) {
      case "next":
        if (this.cursor < this.hunks.length - 1) {
          this.cursor++;
          this.sendCurrent();
        } else {
          this.complete();
        }
        break;
      case "back":
        if (this.cursor > 0) this.cursor--;
        this.sendCurrent();
        break;
      case "more_context":
        this.moreContext();
        break;
      case "whole_file":
        this.wholeFile();
        break;
      case "approve":
        this.complete();
        break;
      default:
        this.log(`unknown nav action: ${action}`);
    }
  }

  comment(text: string) {
    if (!this.active) return;
    const list = this.comments.get(this.cursor) ?? [];
    list.push(text);
    this.comments.set(this.cursor, list);

    if (QUESTION_RE.test(text.trim())) {
      const hunk = this.hunks[this.cursor];
      this.notify(text, {
        kind: "question",
        hunk: String(this.cursor + 1),
        file: hunk.file,
      });
    }
  }

  // Re-render the current hunk with progressively more surrounding context.
  private moreContext() {
    const hunk = this.hunks[this.cursor];
    const lines = (this.contextLines.get(this.cursor) ?? 3) + 10;
    this.contextLines.set(this.cursor, lines);

    const result = spawnSync("git", ["diff", `-U${lines}`, "--", hunk.file], {
      encoding: "utf8",
    });
    if (result.status === 0 && result.stdout) {
      const expanded = extractHunkContaining(result.stdout, hunk.start);
      if (expanded) {
        this.sendCurrent({ diff: expanded });
        return;
      }
    }
    this.log(`more_context: falling back to stored hunk for ${hunk.file}`);
    this.sendCurrent();
  }

  private wholeFile() {
    const hunk = this.hunks[this.cursor];
    try {
      this.sendCurrent({ content: readFileSync(hunk.file, "utf8") });
    } catch (err) {
      this.log(`whole_file failed for ${hunk.file}: ${err}`);
      this.sendCurrent();
    }
  }

  private complete() {
    this.active = false;
    const parts: string[] = ["Review complete."];
    if (this.comments.size === 0) {
      parts.push("No comments; move on to the next chunk.");
    } else {
      parts.push("Comments by hunk:");
      for (const [idx, list] of [...this.comments.entries()].sort((a, b) => a[0] - b[0])) {
        const hunk = this.hunks[idx];
        for (const c of list) {
          parts.push(`- hunk ${idx + 1} (${hunk.file}): ${c}`);
        }
      }
      parts.push("Apply the comments, then re-review only the new diff.");
    }
    this.notify(parts.join("\n"), { kind: "review_complete" });
  }
}

// Finds the @@ hunk in a unified diff whose new-file range contains line.
export function extractHunkContaining(diff: string, line?: number): string | null {
  const lines = diff.split("\n");
  let current: string[] | null = null;
  let currentMatches = false;
  const hunks: { text: string; matches: boolean }[] = [];

  for (const l of lines) {
    const m = l.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/);
    if (m) {
      if (current) hunks.push({ text: current.join("\n"), matches: currentMatches });
      current = [l];
      const start = Number(m[1]);
      const count = Number(m[2] ?? 1);
      currentMatches = line === undefined || (line >= start && line < start + count + 20);
    } else if (current) {
      current.push(l);
    }
  }
  if (current) hunks.push({ text: current.join("\n"), matches: currentMatches });

  const found = hunks.find((h) => h.matches) ?? hunks[0];
  return found ? found.text : null;
}
