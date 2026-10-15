import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coder_bridge import CoderBridgeApp
from test_bridge_debate import FakeManager, FakeWs

LABELS = [{"name": "Neutral"}, {"name": "Mild"}, {"name": "Expressive"}]
ITEM = {"type": "label_item", "task": 1, "index": 1, "total": 2, "file": "a.png",
        "image": "data:image/png;base64,AAAA", "labels": LABELS,
        "suggestion": "Mild", "note": "A faint frown."}


def make_bridge(bobby_replies=()):
    manager = FakeManager(bobby_replies)
    bridge = CoderBridgeApp(chatbot_manager=manager)
    bridge.reviewer_name = "bobby"
    # Run the reviewer's look inline instead of on a worker thread.
    bridge.ask_label_reviewer_async = bridge.ask_label_reviewer
    bridge.active = FakeWs()
    viewer = FakeWs()
    bridge.viewers.add(viewer)
    bridge.handle_state({"type": "state", "mode": "label"})
    return bridge, manager, bridge.active, viewer


def say(bridge, text):
    bridge.route_utterance(text, "microphone")


def test_label_mode_suppresses_reviewer_and_restores():
    bridge, manager, _, _ = make_bridge()
    assert "bobby" in manager.suppressed
    bridge.handle_state({"type": "state", "mode": "idle"})
    assert "bobby" not in manager.suppressed


def test_item_goes_to_pages_and_coder_speaks_pick_once():
    bridge, manager, _, viewer = make_bridge()
    bridge.handle_channel_message(ITEM)
    bridge.handle_channel_message(ITEM)  # back to the same image

    assert viewer.of("label_item")[-1]["image"] == ITEM["image"]
    assert manager.tts.said == [("dyson", "I'd say Mild. A faint frown.")]


def test_saying_a_label_is_the_streamers_call():
    bridge, _, channel, viewer = make_bridge()
    bridge.handle_channel_message(ITEM)
    say(bridge, "Okay, neutral.")

    assert channel.of("label") == [{"type": "label", "label": "Neutral", "flags": [], "by": "potate"}]
    assert viewer.of("label_call")[-1]["label"] == "Neutral"


def test_going_with_the_coder_takes_its_pick():
    bridge, _, channel, _ = make_bridge()
    bridge.handle_channel_message(ITEM)
    say(bridge, "Go with Dyson.")
    say(bridge, "Dyson's right")

    assert channel.of("label") == [{"type": "label", "label": "Mild", "flags": [], "by": "dyson"}] * 2


def test_going_with_someone_without_a_pick_does_nothing():
    bridge, _, channel, _ = make_bridge()
    bridge.handle_channel_message(ITEM)
    say(bridge, "Go with Bobby.")

    assert channel.of("label") == []
    assert channel.of("utterance") == []


def test_bobby_sees_the_image_but_not_dysons_pick():
    bridge, manager, _, _ = make_bridge(["a slight frown, so mild.\nLABEL: Mild"])
    bridge.handle_channel_message(ITEM)

    context = manager.bobby.chatgpt.contexts[0]
    assert context["label_item"]["image"] == ITEM["image"]
    assert "Mild" not in context["prompt"]


def test_bobbys_pick_is_spoken_shown_and_sent_to_the_coder():
    bridge, manager, channel, viewer = make_bridge(["looks blank to me.\nLABEL: neutral"])
    bridge.handle_channel_message(ITEM)

    assert manager.tts.said[-1] == ("bobby", "looks blank to me.")
    assert viewer.of("label_pick")[-1]["label"] == "Neutral"
    assert channel.of("label_pick") == [{"type": "label_pick", "from": "bobby", "label": "Neutral"}]


def test_going_with_bobby_takes_her_pick():
    bridge, _, channel, _ = make_bridge(["looks blank to me.\nLABEL: Neutral"])
    bridge.handle_channel_message(ITEM)
    say(bridge, "I'll go with Bobby's pick.")

    assert channel.of("label") == [{"type": "label", "label": "Neutral", "flags": [], "by": "bobby"}]


def test_a_pick_that_is_not_a_label_is_dropped():
    bridge, manager, channel, _ = make_bridge(["furious!\nLABEL: Angry"])
    bridge.handle_channel_message(ITEM)

    assert manager.tts.said == [("dyson", "I'd say Mild. A faint frown.")]
    assert channel.of("label_pick") == []


def test_a_pick_for_an_image_already_left_is_dropped():
    bridge, manager, channel, _ = make_bridge(["blank.\nLABEL: Neutral"])
    bridge.ask_label_reviewer_async = lambda item: None
    bridge.handle_channel_message(ITEM)
    bridge.handle_channel_message(dict(ITEM, index=2))
    bridge.ask_label_reviewer(ITEM)

    assert "bobby" not in bridge.label_picks.get((1, 1), {})
    assert channel.of("label_pick") == []


def test_skip_next_back_and_done_navigate():
    bridge, _, channel, _ = make_bridge()
    bridge.handle_channel_message(ITEM)
    for line in ("Skip this one.", "next", "go back", "That's all."):
        say(bridge, line)

    assert [m["action"] for m in channel.of("nav")] == ["next", "next", "back", "approve"]


def test_looks_good_does_not_end_labeling():
    bridge, _, channel, _ = make_bridge()
    bridge.handle_channel_message(ITEM)
    say(bridge, "Looks good.")

    assert channel.of("nav") == []


def test_other_talk_reaches_the_coder_only_when_named():
    bridge, _, channel, _ = make_bridge()
    bridge.handle_channel_message(ITEM)
    say(bridge, "Hmm, tough one.")
    say(bridge, "Dyson, what does mild mean?")

    assert [m["text"] for m in channel.of("utterance")] == ["Dyson, what does mild mean?"]


def test_leaving_label_mode_forgets_the_image():
    bridge, _, _, _ = make_bridge()
    bridge.handle_channel_message(ITEM)
    bridge.handle_state({"type": "state", "mode": "idle"})

    assert bridge.current_label_item is None
    assert bridge.label_picks == {}


FLAGS = [{"name": "blurry"}, {"name": "occluded"}]
BLIND = dict(ITEM, labels=LABELS + [{"name": "Unclear"}], flags=FLAGS, blind=True,
             suggestion="", note="")


def test_blind_image_gets_no_picks_until_asked():
    bridge, manager, channel, _ = make_bridge(["blank.\nLABEL: Neutral"])
    bridge.handle_channel_message(BLIND)

    assert manager.tts.said == []
    assert manager.bobby.chatgpt.contexts == []
    assert channel.of("label_help") == []


def test_asking_for_help_brings_in_the_coder_and_bobby_once():
    bridge, manager, channel, _ = make_bridge(["blank.\nLABEL: Neutral"])
    bridge.handle_channel_message(BLIND)
    say(bridge, "Hmm, I can't tell.")
    say(bridge, "What do you think?")

    assert channel.of("label_help") == [{"type": "label_help"}]
    assert len(manager.bobby.chatgpt.contexts) == 1
    assert manager.tts.said == [("bobby", "blank.")]


def test_help_phrases():
    bridge, _, _, _ = make_bridge()
    bridge.handle_channel_message(BLIND)
    for line in ("I don't know.", "I can't tell", "what do you guys think", "not sure"):
        assert bridge.match_label(line) == "help"


def test_coders_live_pick_is_spoken_shown_and_usable():
    bridge, manager, channel, viewer = make_bridge()
    bridge.handle_channel_message(BLIND)
    say(bridge, "I don't know.")
    bridge.handle_channel_message({"type": "label_suggestion", "task": 1, "index": 1,
                                   "label": "Unclear:blurry", "note": "Too soft to read."})
    say(bridge, "Go with Dyson.")

    assert manager.tts.said == [("dyson", "I'd say Unclear, blurry. Too soft to read.")]
    assert viewer.of("label_pick")[-1]["label"] == "Unclear:blurry"
    assert channel.of("label")[-1] == {"type": "label", "label": "Unclear",
                                       "flags": ["blurry"], "by": "dyson"}


def test_a_label_can_carry_flags():
    bridge, _, channel, viewer = make_bridge()
    bridge.handle_channel_message(BLIND)
    say(bridge, "Unclear, blurry and occluded.")

    assert channel.of("label") == [{"type": "label", "label": "Unclear",
                                    "flags": ["blurry", "occluded"], "by": "potate"}]
    assert viewer.of("label_call")[-1]["label"] == "Unclear:blurry:occluded"


def test_an_unknown_flag_is_not_a_call():
    bridge, _, channel, _ = make_bridge()
    bridge.handle_channel_message(BLIND)
    say(bridge, "Unclear, sideways.")

    assert channel.of("label") == []


def test_bobbys_pick_can_carry_flags():
    bridge, _, channel, _ = make_bridge(["too soft.\nLABEL: unclear:Blurry"])
    bridge.handle_channel_message(BLIND)
    say(bridge, "I can't tell.")
    say(bridge, "Bobby's right.")

    assert channel.of("label_pick") == [{"type": "label_pick", "from": "bobby",
                                         "label": "Unclear:blurry"}]
    assert channel.of("label")[-1]["flags"] == ["blurry"]
