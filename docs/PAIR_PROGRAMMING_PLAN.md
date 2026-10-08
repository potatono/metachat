# Voice Pair Programming (Dyson + Bobby as Reviewers)

**Status:** Phase 1 (Dyson/Claude pair loop) is implemented and live-tested.
Phase 2 (review page on the main PC, Bobby as a second reviewer, reviewer
debate) is coded and unit-tested (`tests/test_debate.py`,
`tests/test_bridge_debate.py`); live verification on stream is pending.

**Hosts:** metachat runs on **anarchy** (streaming PC). Claude Code and the
channel server run wherever the code lives (porcupine, WSL2, or an SSH host).
I review on **porcupine**, my main PC.

## 1. Goals

The loop is conversational and works on stream:

1. **Plan:** agree on an approach with Dyson by voice.
2. **Implement:** Claude works in small chunks, each reviewable in a couple of minutes.
3. **Review:** walk the diff one function-sized hunk at a time. Dyson
   summarizes, **Bobby gives a second opinion**, and they argue it out. I
   comment, and I break ties.
4. **Revise:** Claude applies the agreed comments, and we re-review only the new diff.

Phase 2 adds three things:

- **Review UI on porcupine.** The review page runs in a browser on my main
  monitor, not as an OBS browser source on anarchy.
- **Bobby reviews.** Bobby reviews each hunk through metachat's existing
  OpenAI integration (`CompletionApp` in `chatgpt.py`, `code_model`).
- **Reviewer debate.** Bobby/GPT and Dyson/Claude discuss a hunk for up to N
  turns. If they still disagree, I decide.

## 2. Key decisions

| Decision | Choice |
|---|---|
| Speech | All STT/TTS stays on anarchy (Rev.ai, Azure TTS). One mic, one conductor. |
| Coder | Dyson is voiced by **Claude Code CLI** through a custom `claude/channel` MCP server. |
| Second reviewer | Bobby is voiced by metachat's **`CompletionApp`** (OpenAI, `chatgpt.py`), called by the bridge with a new `review` context. |
| Debate orchestration | **`coder_bridge.py` owns the turn loop.** Neither model drives it. |
| Review display | Page served by anarchy on port 9010 (`/review/`), opened **in a browser on porcupine**. OBS scene switching becomes optional (`review_scene` blank by default). |
| Fallback | If channels prove unstable, use an Agent SDK orchestrator that speaks the same bridge protocol. |

## 3. Architecture

```
 CODE HOST                               ANARCHY (metachat)                     PORCUPINE
 claude CLI + /pair skill                rev.py ─STREAMER_LINE─┐                browser
   └─ metachat-channel ──WS+token──────► coder_bridge.py ◄─────┘                /review/ page
        push: utterance, nav,              ├─ say → avatar (dyson / bobby)  ──WS──►  diff, caption,
              peer_comment                 ├─ Bobby review via LLMApp               debate thread,
        tools: reply, set_mode,            ├─ debate loop (N turns, tie-break)      comments
               review_begin                ├─ CODER_STATE on eventbus
                                           └─ optional OBS scene switch
```

**Code host:** `metachat-channel` (Bun/TS). It dials out to the bridge, pushes
utterances, and exposes `reply`, `set_mode`, and `review_begin`. It owns hunk
navigation (`git diff -W` against the chunk checkpoint). Also on this host: the
`/pair` skill, hooks (state events, secret blocking, format/lint), and
CLAUDE.md style rules plus `STYLE_DEBT.md`.

**Anarchy:** `coder_bridge.py` (token auth, voice routing, avatar, eventbus,
debate loop), Dyson LLM suppression while a session is connected,
`AvatarApp.stop` for barge-in, Rev partials and dynamic corrections, and the
review page assets in `public/review/`.

## 4. Session flow

**Plan and implement:** I address Dyson by name. Claude replies in 1–2 spoken
sentences, writes a numbered chunk plan, and implements one chunk per
checkpoint. Hooks drive Dyson's avatar state so there's no dead air.

**Review, per hunk:**

1. The channel sends the hunk. The page renders it, and Dyson speaks the summary.
2. **Bobby's take.** The bridge sends the hunk and Dyson's summary to
   `LLMApp`, which returns a 1–2 sentence verdict plus a stance (`approve` or
   `concern`). Bobby speaks it, and it's added to the page thread.
3. **Debate**, only if Bobby has a concern. The bridge forwards Bobby's line
   to Claude as a `peer_comment` event. Claude answers with
   `reply(text, stance)`, then the bridge sends Dyson's answer back to Bobby,
   and so on.
   - **Agreement:** both stances converge. An agreed change becomes a buffered
     comment on the hunk; an agreed "it's fine" moves on.
   - **Deadlock:** after `debate_max_turns` (default 2 each), each reviewer
     restates their position in one line and the bridge prompts me. My next
     utterance settles it ("go with Bobby", "Dyson's right", or my own
     comment), and the ruling is pinned to the hunk.
4. **I can jump in at any time.** Barge-in stops whichever reviewer is
   speaking. Nav words (`next`, `back`, `approve`, …) end the debate
   immediately. Any other utterance is a comment or a ruling.
5. After the last hunk, Claude gets `review complete` with all comments and
   rulings. It revises, or moves to the next chunk.

**Addressing:** in review mode, an utterance that doesn't name a character
goes to the bridge. If it names Bobby, it's a question for Bobby's reviewer
turn.

## 5. Voice rules

- Lines are short and spoken, with no code, raw identifiers, or line numbers.
  Emoji drive avatar emotion.
- Dyson is weary, deadpan, and competent. Bobby is the excitable second
  opinion. They disagree on substance, not for show, and concede readily
  when wrong.
- Debate lines are one point each. It's a review, not a podcast.
- Chat never reaches either model as instructions.

## 6. Bridge protocol

WebSocket, JSON messages, token in `hello`. The most recent authenticated
session wins.

```jsonc
// channel → bridge
{"type": "hello", "token": "...", "host": "porcupine-wsl", "cwd": "..."}
{"type": "say", "text": "...", "seq": 12, "interruptible": true}
{"type": "turn", "speaker": "dyson", "text": "<whole reply>", "stance": "agree" | "disagree"}  // new: a debate reply
{"type": "state", "mode": "review", "chunk": 2, "chunks": 5}
{"type": "hunk", "review": 1, "index": 3, "total": 7, "file": "...", "lang": "python", "diff": "...", "summary": "..."}
{"type": "vocab", "corrections": {"update tracks": "update_tracks"}}

// bridge → channel
{"type": "utterance", "source": "microphone", "text": "...", "ts": 0}
{"type": "nav", "action": "next"}            // back, more_context, whole_file, approve
{"type": "peer_comment", "from": "bobby", "text": "...", "turn": 1, "max_turns": 2}   // new
{"type": "debate", "status": "agreed", "comment": "..."}                              // new: buffer as a comment
{"type": "ruling", "winner": "bobby" | "dyson" | "streamer", "text": "..." | null}   // new
{"type": "barge_in"}

// bridge → review page (new)
{"type": "thread", "speaker": "bobby", "text": "...", "stance": "concern"}
{"type": "debate", "status": "approved" | "agreed" | "deadlock" | "ruled" | "cancelled", ...}
```

The bridge speaks each hunk's `summary` as the coder the first time the hunk
is shown, then asks the reviewer. `review` on hunk frames lets it tell a
re-review from "back" to hunk 1. The reviewer's model replies end with a
`STANCE:` line, parsed off before speech (`debate.py`).

## 7. Review page

- Served by anarchy at `http://anarchy:9010/review/` and opened in a browser
  on porcupine as my primary review surface.
- diff2html rendering (with a plain-text fallback), caption, "hunk 3/7 ·
  chunk 2/5", my pinned comments, and a **debate thread** with speaker,
  stance, and outcome.
- Read-only display. All input stays voice/keyboard through anarchy.
- Viewers see it through the existing porcupine video capture, so no OBS
  browser source is needed.

## 8. Security (carried forward)

- Coder and reviewer input comes **only** from `microphone`/`keyboard`
  eventbus events, never from `CHAT_MESSAGE` (display names can be spoofed
  across Restream platforms).
- The bridge requires a token. The review page receives data but never sends
  commands to either model.
- A PreToolUse hook blocks `.env`, `secrets.ini`, and key files.
- Bobby's output is untrusted data to Claude. It arrives as a quoted
  `peer_comment`, never as instructions, and Claude makes no tool calls on
  Bobby's say-so alone.

## 9. Risks and open questions

- **Channels is a research preview.** Flag and protocol details may change.
- **Turn latency:** a debate turn is a Claude turn plus a GPT call plus TTS.
  Keep `debate_max_turns` low and skip the debate when Bobby approves.
- **Stance reliability:** GPT stance comes from a structured response; Claude
  stance from the `reply` tool argument. If either drifts, fall back to
  "no consensus → ask me".
- **Bobby's context:** in Phase 2 he sees only the hunk and Dyson's summary,
  so some concerns may be naive. Section 11 addresses this.

## 10. Phase 2 implementation order

1. **Review page on porcupine:** confirm LAN access to `:9010/review/` and
   make `review_scene` optional (the stream already captures porcupine).
2. **Bobby reviewer:** add a `review` context to `CompletionApp` (prompt,
   verdict with a `STANCE:` line), and call it from `handle_hunk`. Bobby
   speaks, and the result goes to the page thread.
3. **Debate loop in `coder_bridge.py`:** `peer_comment` and `ruling` events,
   `stance` on `reply`, `debate_max_turns` in `[coder]`, tie-break prompt, and
   nav/barge-in cancellation.
4. **Channel and skill:** handle `peer_comment` and `ruling` in
   `channel/src`, and add debate etiquette to `/pair`.
5. **Page thread UI and tests:** debate state machine unit tests (agree,
   deadlock, ruling, cancel).

## 11. Next milestone: Bobby in planning

- Bobby weighs in on Dyson's chunk plan during plan mode, using the same
  debate loop and tie-break.
- The agreed plan is kept in Bobby's review context, so his hunk reviews
  judge the code against the intent rather than in isolation.
