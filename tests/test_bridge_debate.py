import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coder_bridge import CoderBridgeApp


def without_at(frame):
    """Chat frames carry a timestamp; compare the rest."""
    assert isinstance(frame.pop("at"), float)
    return frame


class FakeWs:
    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append(json.loads(frame))

    def of(self, mtype):
        return [f for f in self.frames if f["type"] == mtype]


class FakeAvatar:
    def __init__(self):
        self.said = []
        self.acks = []
        self.working = []
        self.noacks = 0
        self.stopped = []
        self.is_talking = False
        self.current = None
        self.queue = []

    def say(self, text, character="bobby"):
        self.said.append((character, text))

    def ack(self, character="bobby"):
        self.acks.append(character)

    def work(self, character="bobby"):
        self.working.append(character)

    def noack(self):
        self.noacks += 1

    def stop(self, character=None):
        self.stopped.append(character)


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.contexts = []

    def get_response(self, history, context, on_thinking=None):
        self.contexts.append(context)
        if on_thinking:
            on_thinking("Weighing the hunk")
        return self.replies.pop(0) if self.replies else None


class FakeCharacter:
    def __init__(self, character, nicknames, replies=()):
        self.character = character
        self.nicknames = nicknames
        self.twitch = None
        self.chatgpt = FakeLLM(replies)

    def is_activated(self, message):
        import re
        return re.search(rf"\b(?:{self.nicknames})\b", message["text"], re.I) is not None

    def is_addressed(self, message):
        return self.is_activated(message)

    def is_discussion_continued(self, message):
        return False


class FakeManager:
    def __init__(self, bobby_replies):
        self.streamer_name = "potate"
        self.tts = FakeAvatar()
        self.bobby = FakeCharacter("bobby", "bob|bobby", bobby_replies)
        self.dyson = FakeCharacter("dyson", "dyson|kirby")
        self.chatbots = {"bobby": self.bobby, "dyson": self.dyson}
        self.ordered = [self.bobby, self.dyson]
        self.code_character = self.dyson
        self.suppressions = {}

    @property
    def suppressed(self):
        return {c for c, reasons in self.suppressions.items() if reasons}

    def suppress(self, character, reason="default"):
        self.suppressions.setdefault(character, set()).add(reason)

    def unsuppress(self, character, reason="default"):
        self.suppressions.get(character, set()).discard(reason)


HUNK = {"type": "hunk", "review": 1, "index": 1, "total": 2, "file": "a.py",
        "diff": "+x = 1", "summary": "Sets x."}


def make_bridge(bobby_replies, max_turns=2):
    manager = FakeManager(bobby_replies)
    bridge = CoderBridgeApp(chatbot_manager=manager)
    bridge.reviewer_name = "bobby"
    bridge.debate.reviewer = "bobby"
    bridge.debate.max_turns = max_turns
    # Run the reviewer's LLM call inline instead of on a worker thread.
    bridge.ask_reviewer_async = lambda question=None: bridge.ask_reviewer(
        bridge.debate.generation, bridge.debate.hunk or bridge.current_hunk,
        list(bridge.debate.thread), question)
    bridge.active = FakeWs()
    viewer = FakeWs()
    bridge.viewers.add(viewer)
    bridge.handle_state({"type": "state", "mode": "review"})
    return bridge, manager, bridge.active, viewer


def test_review_mode_suppresses_reviewer_and_restores():
    bridge, manager, _, _ = make_bridge([])
    assert "bobby" in manager.suppressed
    bridge.handle_state({"type": "state", "mode": "implement"})
    assert "bobby" not in manager.suppressed


def test_first_look_speaks_summary_then_bobby_approves():
    bridge, manager, channel, viewer = make_bridge(["looks fine to me 😏\nSTANCE: approve"])
    bridge.handle_hunk(HUNK)

    assert manager.tts.said == [("dyson", "Sets x."), ("bobby", "looks fine to me 😏")]
    assert channel.of("peer_comment") == []
    assert viewer.of("debate")[-1]["status"] == "approved"
    assert viewer.of("thread")[-1] == {"type": "thread", "speaker": "bobby",
                                       "text": "looks fine to me 😏", "stance": "approve"}

    # Re-rendering the same hunk (more context) stays quiet.
    bridge.handle_hunk(dict(HUNK, diff="+x = 1\n context"))
    assert len(manager.tts.said) == 2
    assert len(manager.bobby.chatgpt.contexts) == 1


def test_concern_round_trips_through_claude_and_is_recorded():
    bridge, manager, channel, viewer = make_bridge(["no bounds check 🤨\nSTANCE: concern"])
    bridge.handle_hunk(HUNK)

    peer = channel.of("peer_comment")
    assert peer == [{"type": "peer_comment", "from": "bobby", "text": "no bounds check 🤨",
                     "turn": 1, "max_turns": 2}]
    ctx = manager.bobby.chatgpt.contexts[0]
    assert ctx["type"] == "review" and ctx["hunk"]["file"] == "a.py" and ctx["thread"] == []

    # Dyson concedes: the concern goes to the channel as an agreed comment.
    bridge.handle_turn({"type": "turn", "speaker": "dyson", "text": "Fine. 🙄", "stance": "agree"})
    assert channel.of("debate") == [{"type": "debate", "status": "agreed", "comment": "no bounds check 🤨"}]
    assert viewer.of("debate")[-1]["status"] == "agreed"
    assert not bridge.debate.active


def test_deadlock_then_streamer_rules():
    bridge, manager, channel, viewer = make_bridge([
        "no bounds check\nSTANCE: concern",
        "still think it needs one\nSTANCE: disagree",
    ], max_turns=2)
    bridge.handle_hunk(HUNK)
    bridge.handle_turn({"type": "turn", "text": "Caller guarantees it.", "stance": "disagree"})
    # Bobby's rebuttal ran inline and went back to Claude as turn 2.
    assert [p["turn"] for p in channel.of("peer_comment")] == [1, 2]
    rebuttal_ctx = manager.bobby.chatgpt.contexts[1]
    assert [t["speaker"] for t in rebuttal_ctx["thread"]] == ["bobby", "dyson"]

    bridge.handle_turn({"type": "turn", "text": "Still no.", "stance": "disagree"})
    assert bridge.debate.state == "deadlock"
    assert viewer.of("debate")[-1]["status"] == "deadlock"
    assert manager.tts.said[-1][0] == "bobby" and "you call it" in manager.tts.said[-1][1]

    bridge.route_utterance("Okay, go with Bobby.", "microphone")
    assert channel.of("ruling") == [{"type": "ruling", "winner": "bobby", "text": "still think it needs one"}]
    assert channel.of("utterance") == []
    assert not bridge.debate.active


def test_nav_cancels_debate_and_moves_on():
    bridge, manager, channel, viewer = make_bridge(["hmm\nSTANCE: concern"])
    bridge.handle_hunk(HUNK)
    assert bridge.debate.active
    bridge.route_utterance("next chunk", "microphone")
    assert not bridge.debate.active
    assert channel.of("nav") == [{"type": "nav", "action": "next"}]
    assert viewer.of("debate")[-1]["status"] == "cancelled"
    # A late turn from Claude about the cancelled debate is ignored.
    bridge.handle_turn({"type": "turn", "text": "late", "stance": "agree"})
    assert channel.of("debate") == []


def test_question_for_bobby_by_name():
    bridge, manager, channel, viewer = make_bridge([
        "fine\nSTANCE: approve",
        "because it's clearer that way",
    ])
    bridge.handle_hunk(HUNK)
    bridge.route_utterance("Bobby, why do you like it?", "microphone")
    ctx = manager.bobby.chatgpt.contexts[-1]
    assert ctx["question"] == "Bobby, why do you like it?"
    assert manager.tts.said[-1] == ("bobby", "because it's clearer that way")
    assert manager.tts.acks[-1] == "bobby"
    # Not forwarded to Claude.
    assert channel.of("utterance") == []


def test_review_comment_must_name_the_coder():
    bridge, manager, channel, viewer = make_bridge(["fine\nSTANCE: approve"])
    bridge.handle_hunk(HUNK)
    bridge.route_utterance("chat, what do you think of this one", "microphone")
    assert channel.of("utterance") == []
    bridge.route_utterance("Dyson, rename that to max age", "microphone")
    assert channel.of("utterance")[-1]["text"] == "Dyson, rename that to max age"


def test_new_review_pass_resets_bookkeeping():
    bridge, manager, channel, viewer = make_bridge(["fine\nSTANCE: approve", "fine\nSTANCE: approve"])
    bridge.handle_hunk(HUNK)
    bridge.handle_hunk(dict(HUNK, review=2))
    assert len(manager.bobby.chatgpt.contexts) == 2


def test_dyson_stop_silences_both_reviewers_and_tells_the_coder():
    bridge, manager, channel, viewer = make_bridge([])
    bridge.route_utterance("Dyson, stop!", "microphone")
    assert set(manager.tts.stopped) == {"bobby", "dyson"}
    assert len(channel.of("stop")) == 1
    assert channel.of("utterance") == []


def test_stop_from_partial_and_final_only_fires_once():
    bridge, manager, channel, _ = make_bridge([])
    bridge.handle_stop()
    bridge.route_utterance("kirby stop", "microphone")
    assert len(channel.of("stop")) == 1


def test_ordinary_talk_never_interrupts():
    bridge, manager, channel, _ = make_bridge([])
    for text in ("hey chat, how's it going", "stop that, cat", "dyson stopped working?"):
        assert not bridge.is_stop(text)


def test_state_label_reaches_viewers_and_persists():
    bridge, _, _, viewer = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "review", "label": "Reading review.js"})
    assert viewer.of("state")[-1]["label"] == "Reading review.js"
    # A state without a label (e.g. set_mode) keeps the last one.
    bridge.handle_state({"type": "state", "mode": "review"})
    assert viewer.of("state")[-1]["label"] == "Reading review.js"


def test_reviewer_thinking_streams_to_viewers_then_clears():
    bridge, _, _, viewer = make_bridge(["looks fine 😏\nSTANCE: approve"])
    bridge.handle_hunk(HUNK)
    thinking = viewer.of("thinking")
    assert [t.get("text") for t in thinking[:2]] == ["", "Weighing the hunk"]
    assert thinking[-1] == {"type": "thinking", "speaker": "bobby", "done": True}


def test_console_request_goes_to_coder_and_echoes_to_viewers():
    bridge, _, channel, viewer = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "plan"})
    bridge.handle_request(viewer, "  add a dark mode  ")
    assert channel.of("utterance")[-1]["text"] == "add a dark mode"
    assert channel.of("utterance")[-1]["source"] == "console"
    assert without_at(viewer.of("chat")[-1]) == {"type": "chat", "speaker": "potate",
                                                 "text": "add a dark mode", "source": "console"}


def test_console_request_without_session_tells_only_the_sender():
    bridge, _, _, viewer = make_bridge([])
    bridge.active = None
    other = FakeWs()
    bridge.viewers.add(other)
    bridge.handle_request(viewer, "hello?")
    assert viewer.of("chat")[-1]["speaker"] == "system"
    assert other.of("chat") == []


def test_coder_say_echoes_to_viewers():
    bridge, _, _, viewer = make_bridge([])
    bridge.handle_say({"type": "say", "text": "Ready to review."})
    assert without_at(viewer.of("chat")[-1]) == {"type": "chat", "speaker": "dyson",
                                                 "text": "Ready to review."}


def test_viewer_ignores_junk_frames_and_handles_requests():
    bridge, _, channel, _ = make_bridge([])

    class ScriptedWs(FakeWs):
        def __init__(self, incoming):
            super().__init__()
            self.incoming = incoming

        def __iter__(self):
            return iter(self.incoming)

    ws = ScriptedWs(["not json", "[1, 2]", "42", '"hi"',
                     json.dumps({"type": "request", "text": "do the thing"})])
    bridge.handle_viewer(ws)
    assert channel.of("utterance")[-1]["text"] == "do the thing"
    assert ws not in bridge.viewers


def test_tool_activity_holds_coder_avatar_until_turn_ends():
    bridge, manager, _, _ = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "implement", "label": "Reading a.py"})
    assert manager.tts.working == ["dyson"]
    # set_mode carries no label: leave the avatar alone.
    bridge.handle_state({"type": "state", "mode": "implement"})
    assert manager.tts.working == ["dyson"] and manager.tts.noacks == 0
    # End of turn (Stop hook) releases it.
    bridge.handle_state({"type": "state", "mode": "implement", "label": ""})
    assert manager.tts.noacks == 1


def test_tool_activity_while_idle_does_not_hold_avatar():
    bridge, manager, _, _ = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "idle", "label": "Reading a.py"})
    assert manager.tts.working == []


def test_activity_from_channel_is_relayed_to_viewers():
    bridge, _, _, viewer = make_bridge([])
    bridge.handle_channel_message({"type": "activity", "kind": "tests", "passed": 3,
                                   "failed": 0, "ok": True, "summary": "3 passed"})
    assert viewer.of("activity")[-1]["passed"] == 3


def test_new_viewer_gets_recent_chat_then_state():
    bridge, _, _, _ = make_bridge([])
    bridge.handle_say({"type": "say", "text": "First."})
    bridge.handle_request(bridge.active, "second")

    class ScriptedWs(FakeWs):
        def __iter__(self):
            return iter([])

    late = ScriptedWs()
    bridge.handle_viewer(late)
    assert [f["type"] for f in late.frames[:3]] == ["chat", "chat", "state"]
    assert [f["text"] for f in late.of("chat")] == ["First.", "second"]


def test_review_verdict_is_relayed_to_viewers():
    bridge, _, _, viewer = make_bridge([])
    bridge.handle_channel_message({"type": "review_result", "approved": False, "comments": 2})
    assert viewer.of("review_result")[-1]["approved"] is False


def test_dictated_change_request_is_captured_then_sent_as_one():
    bridge, manager, channel, viewer = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "plan"})
    bridge.route_utterance("Dyson, here's my change request: add a dark mode.", "microphone")
    bridge.route_utterance("It should remember the choice.", "microphone")
    bridge.route_utterance("Dyson, do you hear me?", "microphone")  # captured, not routed
    assert channel.of("utterance") == []
    assert viewer.of("dictation")[-1]["state"] == "active"
    assert "remember the choice" in viewer.of("dictation")[-1]["text"]
    bridge.route_utterance("Okay Dyson, begin.", "microphone")
    sent = channel.of("utterance")
    assert len(sent) == 1 and sent[0]["source"] == "dictation"
    assert sent[0]["text"] == ("add a dark mode. It should remember the choice. "
                               "Dyson, do you hear me?")
    assert viewer.of("dictation")[-1]["state"] == "sent"
    assert bridge.dictation is None


def test_dictation_can_be_cancelled_and_stop_does_not_end_it():
    bridge, manager, channel, viewer = make_bridge([])
    bridge.route_utterance("Kirby, I have a change request", "microphone")
    bridge.route_utterance("Dyson stop", "microphone")
    assert bridge.dictation == []
    bridge.route_utterance("Dyson, cancel.", "microphone")
    assert bridge.dictation is None
    assert viewer.of("dictation")[-1]["state"] == "cancelled"
    assert channel.of("utterance") == []


def test_page_connecting_mid_dictation_sees_the_draft():
    bridge, _, _, _ = make_bridge([])
    bridge.route_utterance("Dyson, here's my change request", "microphone")
    bridge.route_utterance("rename the helper", "microphone")

    class ScriptedWs(FakeWs):
        def __iter__(self):
            return iter([])

    late = ScriptedWs()
    bridge.handle_viewer(late)
    assert late.of("dictation")[-1]["text"] == "rename the helper"


def test_dictation_starts_with_heres_what_im_thinking():
    bridge, _, channel, viewer = make_bridge([])
    bridge.route_utterance("Dyson. Here's what I'm thinking.", "microphone")
    assert bridge.dictation == []
    bridge.route_utterance("Dyson, cancel", "microphone")
    bridge.route_utterance("Kirby, here is what I am thinking: smaller stamps", "microphone")
    assert bridge.dictation == ["smaller stamps"]


def test_page_connecting_after_an_edit_sees_it():
    bridge, _, _, _ = make_bridge([])
    edit = {"type": "activity", "kind": "edit", "file": "a.py", "diff": "--- a/a.py\n"}
    bridge.handle_channel_message(edit)

    class ScriptedWs(FakeWs):
        def __iter__(self):
            return iter([])

    late = ScriptedWs()
    bridge.handle_viewer(late)
    assert late.of("activity")[-1]["file"] == "a.py"
    # After the state, so a console knows the mode before it sees the edit.
    types = [f["type"] for f in late.frames]
    assert types.index("state") < types.index("activity")


def test_short_change_request_phrase_starts_dictation():
    bridge, _, _, _ = make_bridge([])
    bridge.route_utterance("Dyson change request.", "microphone")
    assert bridge.dictation == []
    bridge.route_utterance("Dyson, cancel", "microphone")
    bridge.route_utterance("Kirby, new change request: bigger font", "microphone")
    assert bridge.dictation == ["bigger font"]


def test_dictation_mutes_everyone_without_undoing_review_muting(monkeypatch):
    monkeypatch.setattr("coder_bridge.DICTATION_UNMUTE_DELAY", 0)
    bridge, manager, _, _ = make_bridge([])  # starts in review: bobby muted
    bridge.route_utterance("Dyson, here's my change request", "microphone")
    assert manager.suppressed >= {"bobby", "dyson"}
    bridge.route_utterance("Dyson, cancel", "microphone")
    import time
    time.sleep(0.1)  # the delayed unmute
    assert manager.suppressions["bobby"] == {"review"}
    # Still reviewing, so Bobby stays muted; the coder stays muted for the session.
    assert "bobby" in manager.suppressed
    bridge.handle_state({"type": "state", "mode": "implement"})
    assert "bobby" not in manager.suppressed


def test_session_ending_mid_dictation_unmutes_everyone():
    bridge, manager, _, _ = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "plan"})
    bridge.route_utterance("Dyson, change request", "microphone")
    bridge.on_session_ended()
    assert bridge.dictation is None
    assert "bobby" not in manager.suppressed


def test_unmute_waits_for_chat_echo_and_skips_if_dictating_again(monkeypatch):
    monkeypatch.setattr("coder_bridge.DICTATION_UNMUTE_DELAY", 0.05)
    bridge, manager, _, _ = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "plan"})
    bridge.route_utterance("Dyson, change request", "microphone")
    bridge.route_utterance("Dyson, cancel", "microphone")
    assert "bobby" in manager.suppressed  # still waiting on the echo
    bridge.route_utterance("Dyson, change request", "microphone")  # again
    import time
    time.sleep(0.15)
    assert "bobby" in manager.suppressed  # the stale unmute didn't fire


def test_bare_go_reaches_the_coder_only_while_planning():
    bridge, _, channel, _ = make_bridge([])
    bridge.handle_state({"type": "state", "mode": "plan"})
    bridge.route_utterance("Let's do it.", "microphone")
    assert channel.of("utterance")[-1]["text"] == "Let's do it."
    bridge.handle_state({"type": "state", "mode": "implement"})
    before = len(channel.of("utterance"))
    bridge.route_utterance("Let's do it.", "microphone")
    assert len(channel.of("utterance")) == before


def test_bobby_weighs_in_once_on_a_settled_plan(monkeypatch):
    monkeypatch.setattr("coder_bridge.PLAN_SETTLE", 0.01)
    bridge, manager, channel, viewer = make_bridge(["solid, but test the regex 😏\nSTANCE: concern"])
    bridge.handle_state({"type": "state", "mode": "plan"})
    bridge.handle_say({"type": "say", "text": "Two chunks."})
    bridge.handle_say({"type": "say", "text": "First the regex, then the page."})
    import time
    time.sleep(0.1)
    context = manager.bobby.chatgpt.contexts[-1]
    assert context["plan"] == "Two chunks. First the regex, then the page."
    assert ("bobby", "solid, but test the regex 😏") in manager.tts.said
    comment = channel.of("plan_comment")[-1]
    assert comment["stance"] == "concern" and comment["from"] == "bobby"


def test_bobby_skips_non_plans_and_stale_plans():
    bridge, manager, channel, _ = make_bridge(["SKIP", "fine\nSTANCE: approve"])
    bridge.handle_state({"type": "state", "mode": "plan"})
    bridge.review_plan("On it.")
    assert channel.of("plan_comment") == []
    bridge.handle_state({"type": "state", "mode": "implement"})
    bridge.review_plan("Two chunks.")
    assert channel.of("plan_comment") == []
