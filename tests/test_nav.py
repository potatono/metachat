import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coder_bridge import match_nav, match_plan_go

def test_nav_words():
    assert match_nav("next") == "next"
    assert match_nav("Next.") == "next"
    assert match_nav("go on") == "next"
    assert match_nav("back") == "back"
    assert match_nav("go back") == "back"
    assert match_nav("more context") == "more_context"
    assert match_nav("show me more") == "more_context"
    assert match_nav("whole file") == "whole_file"
    assert match_nav("show the whole file") == "whole_file"
    assert match_nav("approve") == "approve"
    assert match_nav("Approved.") == "approve"
    assert match_nav("Dyson, approved.") == "approve"
    assert match_nav("looks good") == "approve"
    assert match_nav("LGTM") == "approve"

def test_spoken_phrasings():
    # Real utterances from live testing that must match.
    assert match_nav("Next chunk.") == "next"
    assert match_nav("Move onto the next chunk.") == "next"
    assert match_nav("I approve.") == "approve"
    assert match_nav("I approve this chunk.") == "approve"
    assert match_nav("All right, this Chuck looks good.") == "approve"  # STT mishears chunk
    assert match_nav("Okay, this chunk looks good.") == "approve"
    assert match_nav("Dyson, I approve this chunk.") == "approve"
    assert match_nav("yeah looks good to me") == "approve"

def test_loose_natural_phrasings():
    for text in ("Looks good to me too.", "I like it.", "I like it, Dyson.",
                 "That works for me.", "Sounds good.", "Seems fine to me.",
                 "Nice work.", "That's perfect.", "Good to go.", "this chunk looks great"):
        assert match_nav(text) == "approve", text
    for text in ("Next please.", "Moving on.", "What's next?", "On to the next chunk."):
        assert match_nav(text) == "next", text


def test_non_nav_utterances_are_comments():
    assert match_nav("rename that to max age") is None
    assert match_nav("why the early return?") is None
    assert match_nav("the next function is wrong") is None
    assert match_nav("that looks good but rename it") is None
    assert match_nav("I like it but the name is wrong") is None
    assert match_nav("nice try") is None
    for text in ("Nice.", "Great!", "Fine.", "Perfect."):
        assert match_nav(text) is None, text
    assert match_nav("Okay.") is None
    assert match_nav("") is None


def test_plan_approvals():
    for text in ("Go.", "Go ahead.", "Let's do it!", "Okay, let's go.", "Do it.",
                 "Sounds good to me.", "That plan works.", "I like it.", "Go, Dyson."):
        assert match_plan_go(text), text
    for text in ("go away", "let's talk about it", "I don't like it", "goes without saying"):
        assert not match_plan_go(text), text
