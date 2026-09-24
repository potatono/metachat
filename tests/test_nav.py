import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coder_bridge import match_nav

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
    assert match_nav("looks good") == "approve"
    assert match_nav("LGTM") == "approve"

def test_non_nav_utterances_are_comments():
    assert match_nav("rename that to max age") is None
    assert match_nav("why the early return?") is None
    assert match_nav("the next function is wrong") is None
    assert match_nav("that looks good but rename it") is None
    assert match_nav("") is None
