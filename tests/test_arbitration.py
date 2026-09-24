import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from util import pick_winner

class FakeCharacter:
    def __init__(self, name):
        self.name = name

bobby = FakeCharacter("bobby")
dyson = FakeCharacter("dyson")

def test_single_addressed_wins():
    addressed = [(bobby, {"type": "activation"})]
    # Even with a stale last_addressed pointing elsewhere.
    winner, context = pick_winner(addressed, (dyson, {"type": "code"}))
    assert winner is bobby
    assert context == {"type": "activation"}

def test_multi_addressed_prefers_last_winner_with_current_context():
    addressed = [(bobby, {"type": "activation"}), (dyson, {"type": "discussion"})]
    stale_context = {"type": "code"}
    winner, context = pick_winner(addressed, (dyson, stale_context))
    assert winner is dyson
    # Must use the current context, not the previous message's.
    assert context == {"type": "discussion"}

def test_multi_addressed_stale_last_winner_falls_back_to_first():
    other = FakeCharacter("kirby")
    addressed = [(bobby, {"type": "activation"}), (dyson, {"type": "discussion"})]
    winner, context = pick_winner(addressed, (other, {"type": "boredom"}))
    assert winner is bobby
    assert context == {"type": "activation"}

def test_multi_addressed_no_last_winner():
    addressed = [(bobby, {"type": "activation"}), (dyson, {"type": "discussion"})]
    winner, context = pick_winner(addressed, None)
    assert winner is bobby
