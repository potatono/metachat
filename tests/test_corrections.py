import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from util import apply_corrections

def test_basic_replacement():
    corrections = [("Val Heim", "Valheim")]
    assert apply_corrections("we played Val Heim today", corrections) == "we played Valheim today"

def test_case_insensitive():
    corrections = [("val heim", "Valheim")]
    assert apply_corrections("Val Heim and VAL HEIM", corrections) == "Valheim and Valheim"

def test_more_than_two_occurrences():
    # The old code passed re.IGNORECASE (2) as count, capping replacements.
    corrections = [("foo", "bar")]
    assert apply_corrections("foo foo foo foo", corrections) == "bar bar bar bar"

def test_word_boundaries():
    corrections = [("cat", "dog")]
    assert apply_corrections("cat catalog concat", corrections) == "dog catalog concat"

def test_regex_metachars_in_bad_are_literal():
    corrections = [("C++", "cpp")]
    # Unescaped, "C++" is an invalid/greedy pattern; escaped it matches literally.
    assert apply_corrections("I like C++ a lot", corrections) == "I like cpp a lot"

def test_multiple_corrections_apply_in_order():
    corrections = [("chat G P T", "ChatGPT"), ("miss lands", "Mistlands")]
    text = "ask chat G P T about miss lands"
    assert apply_corrections(text, corrections) == "ask ChatGPT about Mistlands"

def test_custom_flags():
    # avatar.py passes re.IGNORECASE | re.A; behavior should still replace.
    corrections = [("potate_oh_no", "potate oh no")]
    result = apply_corrections("hi potate_oh_no!", corrections, flags=re.IGNORECASE | re.A)
    assert result == "hi potate oh no!"
