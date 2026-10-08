// Project side of the one-line setup: wires a project up to the channel
// install this file lives in.  Run by setup/channel.{sh,ps1}; safe to re-run
// (that is also how a project picks up a channel update).
//
//   bun run ~/.metachat/channel/setup/install.ts <project-dir>
//
// Env overrides: METACHAT_BRIDGE_URL, METACHAT_BRIDGE_TOKEN.

import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const SERVER_NAME = "metachat-channel";
const DEFAULT_BRIDGE_URL = "ws://anarchy:9010";

// Forward slashes everywhere: hook commands run under Git Bash on Windows,
// which eats backslashes.
const slash = (p: string) => p.replaceAll("\\", "/");

const channelDir = slash(path.resolve(import.meta.dir, ".."));
const projectDir = path.resolve(process.argv[2] ?? process.cwd());
const claudeDir = path.join(projectDir, ".claude");

function claude(args: string[]): number {
  const proc = Bun.spawnSync(["claude", ...args], {
    cwd: projectDir,
    stdout: "ignore",
    stderr: "inherit",
  });
  return proc.exitCode;
}

// Reads a line without echoing it; the token must never show on stream.
async function promptSecret(question: string): Promise<string> {
  const stdin = process.stdin;
  if (!stdin.isTTY) return prompt(question) ?? "";

  process.stdout.write(`${question} `);
  stdin.setRawMode(true);
  stdin.resume();
  let value = "";
  try {
    for await (const chunk of stdin) {
      for (const ch of chunk.toString("utf8")) {
        if (ch === "\r" || ch === "\n") return value;
        if (ch === "\u0003") process.exit(130); // Ctrl-C
        if (ch === "\u007f" || ch === "\b") value = value.slice(0, -1);
        else value += ch;
      }
    }
    return value;
  } finally {
    stdin.setRawMode(false);
    stdin.pause();
    process.stdout.write("\n");
  }
}

async function registerServer() {
  const registered = claude(["mcp", "get", SERVER_NAME]) === 0;
  let token = process.env.METACHAT_BRIDGE_TOKEN ?? "";
  let url = process.env.METACHAT_BRIDGE_URL ?? "";

  if (registered && !token) {
    const answer = prompt(`${SERVER_NAME} is already registered here. Keep its URL and token? [Y/n]`);
    if (!answer || answer.trim().toLowerCase().startsWith("y")) {
      console.log("Keeping existing registration.");
      return;
    }
  }

  if (!url) url = prompt(`Bridge URL [${DEFAULT_BRIDGE_URL}]:`)?.trim() || DEFAULT_BRIDGE_URL;
  while (!token) token = (await promptSecret("Bridge token (from anarchy's secrets.ini [coder]):")).trim();

  if (registered) claude(["mcp", "remove", SERVER_NAME, "--scope", "local"]);
  const code = claude([
    "mcp", "add", SERVER_NAME, "--scope", "local",
    "-e", `METACHAT_BRIDGE_URL=${url}`,
    "-e", `METACHAT_BRIDGE_TOKEN=${token}`,
    "--", "bun", "run", `${channelDir}/src/index.ts`,
  ]);
  if (code !== 0) throw new Error(`claude mcp add failed (${code})`);
  console.log(`Registered ${SERVER_NAME} -> ${url}`);
}

function installSkill() {
  const dest = path.join(claudeDir, "skills", "pair");
  cpSync(path.join(channelDir, "skills", "pair"), dest, { recursive: true, force: true });
  console.log("Installed pair skill.");
}

type HookGroup = { matcher?: string; hooks: { type: string; command: string }[] };
type Settings = { hooks?: Record<string, HookGroup[]>; [key: string]: unknown };

// Merges the example hook config into the project's settings, adding only
// hook commands that aren't already there.
function mergeHooks() {
  const example: Settings = JSON.parse(
    readFileSync(path.join(channelDir, "settings.example.json"), "utf8")
      .replaceAll("/path/to/metachat/channel", channelDir),
  );
  const settingsPath = path.join(claudeDir, "settings.json");
  const settings: Settings = existsSync(settingsPath)
    ? JSON.parse(readFileSync(settingsPath, "utf8"))
    : {};
  settings.hooks ??= {};

  let added = 0;
  for (const [event, groups] of Object.entries(example.hooks ?? {})) {
    const existing = (settings.hooks[event] ??= []);
    for (const group of groups) {
      const target = existing.find((g) => g.matcher === group.matcher);
      if (!target) {
        existing.push(group);
        added += group.hooks.length;
        continue;
      }
      for (const hook of group.hooks) {
        if (!target.hooks.some((h) => h.command === hook.command)) {
          target.hooks.push(hook);
          added++;
        }
      }
    }
  }

  mkdirSync(claudeDir, { recursive: true });
  writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
  console.log(added ? `Added ${added} hook(s) to .claude/settings.json.` : "Hooks already present.");
}

function installStyleDebt() {
  const debtPath = path.join(projectDir, "docs", "STYLE_DEBT.md");
  if (!existsSync(debtPath)) {
    mkdirSync(path.dirname(debtPath), { recursive: true });
    cpSync(path.join(channelDir, "templates", "STYLE_DEBT.md"), debtPath);
    console.log("Created docs/STYLE_DEBT.md.");
  }

  const claudeMd = path.join(projectDir, "CLAUDE.md");
  const current = existsSync(claudeMd) ? readFileSync(claudeMd, "utf8") : "";
  if (!current.includes("STYLE_DEBT.md")) {
    const section =
      "## Style\n\n" +
      "Rules promoted from docs/STYLE_DEBT.md, each with a before/after example.\n";
    writeFileSync(claudeMd, current ? `${current.trimEnd()}\n\n${section}` : section);
    console.log("Added a Style section to CLAUDE.md; fill in examples as rules land.");
  }
}

try {
  console.log(`Setting up ${SERVER_NAME} in ${projectDir}`);
  await registerServer();
  installSkill();
  mergeHooks();
  installStyleDebt();
  console.log(`\nDone. Launch with:\n  claude --dangerously-load-development-channels server:${SERVER_NAME}`);
} catch (err) {
  console.error(`Setup failed: ${err instanceof Error ? err.message : err}`);
  process.exit(1);
}
