import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from debate import Debate, parse_stance, match_ruling

BOBBY = "bobbychatbot|bob|bobby|robert"
DYSON = "dyson|vacuum|kirby|roomba|hoover|dice"
HUNK = {"index": 1, "file": "a.py", "diff": "+x", "summary": "Adds x."}


def test_parse_stance():
    assert parse_stance("looks fine to me 😏\nSTANCE: approve") == ("looks fine to me 😏", "approve")
    assert parse_stance("that retry loop never backs off.\n\nstance: Concern") == \
        ("that retry loop never backs off.", "concern")
    assert parse_stance("fine, you win. STANCE: agree.") == ("fine, you win.", "agree")
    assert parse_stance("**Stance:** disagree") == ("", "disagree")
    assert parse_stance("no tag here") == ("no tag here", None)
    assert parse_stance(None) == ("", None)


def test_match_ruling():
    assert match_ruling("Go with Bobby.", BOBBY, DYSON) == "reviewer"
    assert match_ruling("okay, Bobby's right", BOBBY, DYSON) == "reviewer"
    assert match_ruling("Dyson is right on this one", BOBBY, DYSON) == "coder"
    assert match_ruling("I agree with Dyson", BOBBY, DYSON) == "coder"
    assert match_ruling("rename it to max age instead", BOBBY, DYSON) is None
    assert match_ruling("Bobby, what do you think?", BOBBY, DYSON) is None


def test_opening_approval_ends_quietly():
    d = Debate(max_turns=2)
    d.start(HUNK)
    action = d.reviewer_said("ship it", "approve")
    assert action == ("done", {"status": "approved", "comment": None})
    assert not d.active


def test_opening_without_stance_is_approval():
    d = Debate()
    d.start(HUNK)
    assert d.reviewer_said("seems ok", None)[0] == "done"


def test_concern_asks_coder_then_coder_concedes():
    d = Debate(max_turns=2)
    d.start(HUNK)
    action = d.reviewer_said("no timeout on that call", "concern")
    assert action == ("ask_coder", {"text": "no timeout on that call", "turn": 1, "max_turns": 2})
    action = d.coder_said("Fine. I'll add one. 🙄", "agree")
    assert action == ("done", {"status": "agreed", "comment": "no timeout on that call"})
    assert d.state == "done"


def test_reviewer_withdraws():
    d = Debate(max_turns=2)
    d.start(HUNK)
    d.reviewer_said("no timeout", "concern")
    assert d.coder_said("The client has a default.", "disagree") == ("ask_reviewer", {"text": "The client has a default."})
    assert d.reviewer_said("oh. fair.", "agree") == ("done", {"status": "agreed", "comment": None})


def test_deadlock_after_max_turns():
    d = Debate(max_turns=2)
    d.start(HUNK)
    d.reviewer_said("concern one", "concern")
    d.coder_said("nope", "disagree")
    d.reviewer_said("still a concern", "disagree")
    action = d.coder_said("still nope", "disagree")
    assert action == ("deadlock", {"reviewer": "still a concern", "coder": "still nope"})
    assert d.state == "deadlock"
    assert d.active


def test_missing_stance_mid_debate_holds_position():
    d = Debate(max_turns=1)
    d.start(HUNK)
    d.reviewer_said("concern", "concern")
    assert d.coder_said("mumble", None)[0] == "deadlock"


def test_rulings():
    def deadlocked():
        d = Debate(max_turns=1)
        d.start(HUNK)
        d.reviewer_said("needs a lock", "concern")
        d.coder_said("it's single threaded", "disagree")
        return d

    assert deadlocked().rule("go with Bobby", BOBBY, DYSON) == {"winner": "bobby", "text": "needs a lock"}
    assert deadlocked().rule("Dyson's right", BOBBY, DYSON) == {"winner": "dyson", "text": None}
    assert deadlocked().rule("add a comment saying why", BOBBY, DYSON) == \
        {"winner": "streamer", "text": "add a comment saying why"}
    d = deadlocked()
    d.rule("go with bobby", BOBBY, DYSON)
    assert not d.active
    # No debate, no ruling.
    assert Debate().rule("go with bobby", BOBBY, DYSON) is None


def test_streamer_can_rule_before_deadlock():
    d = Debate(max_turns=3)
    d.start(HUNK)
    d.reviewer_said("concern", "concern")
    assert d.active
    assert d.rule("leave it, next", BOBBY, DYSON)["winner"] == "streamer"


def test_cancel_and_generation():
    d = Debate()
    gen = d.start(HUNK)
    d.reviewer_said("concern", "concern")
    d.cancel()
    assert not d.active
    assert d.generation > gen
    assert d.reviewer_said("late result", "concern") == ("ignore",)
    assert d.coder_said("late result", "agree") == ("ignore",)
