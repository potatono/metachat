// WebSocket client that dials out to metachat's coder bridge on anarchy.
// The bridge protocol is defined in docs/PAIR_PROGRAMMING_PLAN.md §6.

export type BridgeMessage = Record<string, unknown> & { type: string };

export interface BridgeOptions {
  url: string;
  token: string;
  host: string;
  cwd: string;
  onMessage: (msg: BridgeMessage) => void;
  onOpen?: () => void;
  log: (line: string) => void;
}

const MAX_BACKOFF_MS = 30_000;

export class BridgeClient {
  private ws: WebSocket | null = null;
  private backoffMs = 1000;
  private closed = false;
  private sendQueue: string[] = [];

  constructor(private opts: BridgeOptions) {}

  connect() {
    if (this.closed) return;
    this.opts.log(`connecting to ${this.opts.url}`);
    const ws = new WebSocket(this.opts.url);
    this.ws = ws;

    ws.onopen = () => {
      this.backoffMs = 1000;
      this.opts.log("connected, sending hello");
      ws.send(JSON.stringify({
        type: "hello",
        token: this.opts.token,
        host: this.opts.host,
        cwd: this.opts.cwd,
      }));
      for (const frame of this.sendQueue.splice(0)) ws.send(frame);
      this.opts.onOpen?.();
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(String(ev.data)) as BridgeMessage;
        this.opts.onMessage(msg);
      } catch (err) {
        this.opts.log(`bad frame from bridge: ${err}`);
      }
    };

    ws.onclose = () => this.scheduleReconnect();
    ws.onerror = () => { /* onclose fires after; avoid double-scheduling */ };
  }

  private scheduleReconnect() {
    if (this.closed) return;
    this.ws = null;
    this.opts.log(`disconnected, retrying in ${this.backoffMs}ms`);
    setTimeout(() => this.connect(), this.backoffMs);
    this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
  }

  // Sends now, or queues until the next successful hello.
  send(msg: BridgeMessage) {
    const frame = JSON.stringify(msg);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(frame);
    } else {
      this.sendQueue.push(frame);
    }
  }

  close() {
    this.closed = true;
    this.ws?.close();
  }
}
