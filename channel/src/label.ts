// Label-mode state: like a review, the channel owns navigation so moving
// between images needs no model round trip.  Each image goes to the bridge;
// the streamer's call (their own label, or "go with" a reviewer's pick) comes
// back as a label frame.  Claude gets one event with every final label when
// the pass completes.
//
// Blind (the default): nobody else looks at an image until the streamer asks
// for help ("I can't tell"); only then is Claude asked for its pick, and the
// bridge asks the second reviewer.  Otherwise Claude's suggestions come up
// front with the task and are shown as each image comes up.
//
// A label can carry flags from the task's flag list: "unclear, blurry" is
// recorded as "unclear:blurry".

import { readFileSync, statSync } from "node:fs";
import { extname } from "node:path";
import type { BridgeMessage } from "./bridge.ts";

export interface LabelOption {
  name: string;
  description?: string;
}

export interface LabelItem {
  image: string;
  suggestion?: string;
  note?: string;
}

export interface LabelTask {
  labels: LabelOption[];
  flags?: LabelOption[];
  instructions?: string;
  items: LabelItem[];
  blind?: boolean;
}

interface Decision {
  label: string;
  by: string;
}

interface Suggestion {
  label: string;
  note: string;
}

// Each image's picks from everyone but the streamer, by name.
type Picks = Record<string, string>;

// The bridge's websocket takes frames up to 16 MiB; base64 adds a third.
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

const MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

export class LabelSession {
  private task: LabelTask = { labels: [], items: [] };
  private cursor = 0;
  private decisions = new Map<number, Decision>();
  private picks = new Map<number, Picks>();
  // Claude's picks given when the streamer asked, in blind mode.
  private suggestions = new Map<number, Suggestion>();
  // Bumped per begin() and sent with every item, like a review id.
  private taskId = 0;
  active = false;

  constructor(
    private send: (msg: BridgeMessage) => void,
    private notify: (content: string, meta?: Record<string, string>) => void,
    private log: (line: string) => void,
  ) {}

  begin(task: LabelTask) {
    this.task = task;
    this.cursor = 0;
    this.decisions.clear();
    this.picks.clear();
    this.suggestions.clear();
    this.taskId++;
    this.active = task.items.length > 0 && task.labels.length > 0;
    if (this.active) this.sendCurrent();
  }

  // 1-based index of the image on screen, for messages to Claude.
  get current(): number {
    return this.cursor + 1;
  }

  private get blind(): boolean {
    return this.task.blind !== false;
  }

  // Claude's pick for the current image: given up front, or (blind) given
  // since the streamer asked.
  private suggestionFor(index: number): Suggestion | null {
    if (this.blind) return this.suggestions.get(index) ?? null;
    const item = this.task.items[index];
    return item.suggestion ? { label: item.suggestion, note: item.note ?? "" } : null;
  }

  private sendCurrent() {
    const item = this.task.items[this.cursor];
    const decision = this.decisions.get(this.cursor);
    const suggestion = this.suggestionFor(this.cursor);
    this.send({
      type: "label_item",
      task: this.taskId,
      index: this.cursor + 1,
      total: this.task.items.length,
      file: item.image,
      image: this.dataUrl(item.image),
      labels: this.task.labels,
      flags: this.task.flags ?? [],
      instructions: this.task.instructions ?? "",
      blind: this.blind,
      suggestion: suggestion?.label ?? "",
      note: suggestion?.note ?? "",
      decided: decision?.label ?? null,
    });
  }

  // The image inline, since the pages and the second reviewer are on
  // another machine.  Null when it can't be read; the page says so.
  private dataUrl(path: string): string | null {
    const mime = MIME[extname(path).toLowerCase()];
    if (!mime) {
      this.log(`label: unsupported image type: ${path}`);
      return null;
    }
    try {
      if (statSync(path).size > MAX_IMAGE_BYTES) {
        this.log(`label: image too large to send: ${path}`);
        return null;
      }
      return `data:${mime};base64,${readFileSync(path).toString("base64")}`;
    } catch (err) {
      this.log(`label: can't read ${path}: ${err}`);
      return null;
    }
  }

  // "label:flag:flag" in the task's own spelling, or null (logged) when the
  // label or a flag isn't one of the task's.
  combine(label: string, flags: string[] = []): string | null {
    const find = (list: LabelOption[], name: string) =>
      list.find((o) => o.name.toLowerCase() === name.trim().toLowerCase());
    const option = find(this.task.labels, label);
    if (!option) {
      this.log(`label: not one of the labels: ${label}`);
      return null;
    }
    const names: string[] = [option.name];
    for (const flag of flags) {
      const known = find(this.task.flags ?? [], flag);
      if (!known) {
        this.log(`label: not one of the flags: ${flag}`);
        return null;
      }
      if (!names.includes(known.name)) names.push(known.name);
    }
    return names.join(":");
  }

  // The streamer asked for help on the current image.  Blind, that's the
  // first Claude hears of it; otherwise its pick is already on screen.
  help() {
    if (!this.active || !this.blind) return;
    const item = this.task.items[this.cursor];
    const known = this.suggestions.get(this.cursor);
    if (known) {
      this.sendSuggestion(known);
      return;
    }
    const flags = (this.task.flags ?? []).map((f) => f.name).join(", ") || "none";
    this.notify(
      `The streamer can't call image ${this.current} of ${this.task.items.length} ` +
        `(${item.image}) and wants your take. Look at it now, then call label_suggest ` +
        `with one of the labels (${this.task.labels.map((o) => o.name).join(", ")}), ` +
        `any flags that apply (${flags}), and a short spoken note on why.`,
      { kind: "label_help", index: String(this.current), image: item.image },
    );
  }

  // Claude's pick after a help request; it goes on stream like any pick.
  suggest(label: string, flags: string[], note: string): string | null {
    if (!this.active) return "no labeling pass is running";
    const combined = this.combine(label, flags);
    if (!combined) return "not one of the task's labels and flags";
    const suggestion = { label: combined, note };
    this.suggestions.set(this.cursor, suggestion);
    this.sendSuggestion(suggestion);
    return null;
  }

  private sendSuggestion(suggestion: Suggestion) {
    this.send({
      type: "label_suggestion",
      task: this.taskId,
      index: this.current,
      label: suggestion.label,
      note: suggestion.note,
    });
  }

  // A second reviewer's pick for the current image, kept for the results.
  pick(from: string, label: string) {
    if (!this.active) return;
    this.picks.set(this.cursor, { ...this.picks.get(this.cursor), [from]: label });
  }

  // The streamer's call on the current image; moves on to the next.
  decide(label: string, by: string, flags: string[] = []) {
    if (!this.active) return;
    const combined = this.combine(label, flags);
    if (!combined) return;
    this.decisions.set(this.cursor, { label: combined, by });
    this.nav("next");
  }

  nav(action: string) {
    if (!this.active) return;
    switch (action) {
      case "next":
        if (this.cursor < this.task.items.length - 1) {
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
      case "approve":
        // "That's it" / "looks good": stop here, whatever is left unlabeled.
        this.complete();
        break;
      default:
        this.log(`label: unknown nav action: ${action}`);
    }
  }

  private complete() {
    this.active = false;
    const results = this.task.items.map((item, i) => {
      const decision = this.decisions.get(i);
      return {
        image: item.image,
        label: decision?.label ?? null,
        by: decision?.by ?? null,
        suggestion: this.suggestionFor(i)?.label ?? null,
        picks: this.picks.get(i) ?? {},
      };
    });
    const labeled = results.filter((r) => r.label !== null).length;
    this.send({ type: "label_result", labeled, total: results.length });
    this.notify(
      `Labeling complete: ${labeled} of ${results.length} images labeled ` +
        "(label null means skipped; flags follow the label after colons). " +
        "Final labels as JSON:\n" +
        JSON.stringify(results, null, 2),
      { kind: "label_complete" },
    );
  }
}
