// Stand-in for metachat's coder bridge, for testing the channel transport
// without anarchy. Serves a tiny web page; text typed there is sent to the
// channel as an utterance, and incoming `say` frames are printed (and spoken
// with the local TTS where available).
//
//   METACHAT_BRIDGE_TOKEN=devtoken bun run proto/fake_bridge.ts

const PORT = Number(process.env.PORT ?? 9010);
const TOKEN = process.env.METACHAT_BRIDGE_TOKEN ?? "devtoken";

type Session = { ws: any; authed: boolean; kind: "channel" | "page" };
const sessions = new Set<Session>();

function speakLocal(text: string) {
  const clean = text.replace(/[^\x20-\x7E]/g, "").replace(/'/g, "");
  try {
    if (process.platform === "win32") {
      Bun.spawn([
        "powershell", "-NoProfile", "-Command",
        `Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('${clean}')`,
      ]);
    } else {
      Bun.spawn(["sh", "-c", `command -v espeak >/dev/null && espeak '${clean}' || true`]);
    }
  } catch {
    // TTS is best-effort; the console line is the real check.
  }
}

const PAGE = `<!doctype html><html><head><title>fake bridge</title>
<style>body{font-family:monospace;background:#181818;color:#ddd;margin:2em}
#log{white-space:pre-wrap;border:1px solid #444;padding:1em;height:300px;overflow-y:auto}
input{width:80%;background:#222;color:#ddd;border:1px solid #444;padding:.5em}</style>
</head><body>
<h3>fake coder bridge</h3><div id="log"></div>
<p><input id="in" placeholder="type an utterance, press enter"></p>
<script>
const log = (t) => { const d = document.getElementById('log');
  d.textContent += t + "\\n"; d.scrollTop = d.scrollHeight; };
const ws = new WebSocket("ws://" + location.host + "/ws");
ws.onopen = () => { ws.send(JSON.stringify({type:"page_hello"})); log("[page connected]"); };
ws.onmessage = (ev) => log("<< " + ev.data);
document.getElementById('in').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) {
    ws.send(JSON.stringify({type:"utterance", source:"keyboard", text:e.target.value.trim(), ts:Date.now()}));
    log(">> " + e.target.value.trim());
    e.target.value = '';
  }
});
</script></body></html>`;

Bun.serve({
  port: PORT,
  fetch(req, server) {
    const url = new URL(req.url);
    if (url.pathname === "/ws") {
      if (server.upgrade(req)) return undefined as any;
      return new Response("upgrade failed", { status: 400 });
    }
    return new Response(PAGE, { headers: { "content-type": "text/html" } });
  },
  websocket: {
    open(ws) {
      const session: Session = { ws, authed: false, kind: "page" };
      (ws as any).data = session;
      sessions.add(session);
    },
    message(ws, raw) {
      const session = (ws as any).data as Session;
      let msg: any;
      try { msg = JSON.parse(String(raw)); } catch { return; }

      if (msg.type === "page_hello") {
        session.authed = true;
        session.kind = "page";
        return;
      }
      if (msg.type === "hello") {
        session.authed = msg.token === TOKEN;
        session.kind = "channel";
        console.log(`[fake-bridge] channel hello from ${msg.host} (${msg.cwd}) auth=${session.authed}`);
        if (!session.authed) ws.close(1008, "bad token");
        return;
      }
      if (!session.authed) return;

      if (session.kind === "page") {
        // Page input goes to every authed channel session.
        for (const s of sessions) {
          if (s.kind === "channel" && s.authed) s.ws.send(JSON.stringify(msg));
        }
        return;
      }

      // Channel -> bridge traffic: print, mirror to pages, speak says.
      console.log(`[fake-bridge] ${JSON.stringify(msg)}`);
      for (const s of sessions) {
        if (s.kind === "page" && s.authed) s.ws.send(JSON.stringify(msg));
      }
      if (msg.type === "say") speakLocal(String(msg.text ?? ""));
    },
    close(ws) {
      sessions.delete((ws as any).data as Session);
    },
  },
});

console.log(`[fake-bridge] http://localhost:${PORT} (token: ${TOKEN})`);
