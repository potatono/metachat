import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "channel", "hooks"))

import state_event as hook


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_edit_diff_is_placed_in_the_file_with_line_numbers(tmp_path):
    path = write(tmp_path, "a.py", "one\ntwo\nthree\nfour\n")
    detail = hook.edit_detail("Edit", {"file_path": path, "old_string": "three",
                                       "new_string": "THREE"}, str(tmp_path))
    assert detail["kind"] == "edit" and detail["file"] == "a.py"
    assert "@@ -1,4 +1,4 @@" in detail["diff"]
    assert "-three\n+THREE\n" in detail["diff"]
    assert not detail["truncated"]


def test_edit_replace_all_changes_every_match(tmp_path):
    path = write(tmp_path, "a.py", "x\ny\nx\n")
    detail = hook.edit_detail("Edit", {"file_path": path, "old_string": "x", "new_string": "z",
                                       "replace_all": True}, str(tmp_path))
    assert detail["diff"].count("+z\n") == 2


def test_edit_not_found_falls_back_to_the_strings(tmp_path):
    path = write(tmp_path, "a.py", "unrelated\n")
    detail = hook.edit_detail("Edit", {"file_path": path, "old_string": "old",
                                       "new_string": "new"}, str(tmp_path))
    assert "-old\n+new\n" in detail["diff"]


def test_write_of_new_file_is_all_additions(tmp_path):
    detail = hook.edit_detail("Write", {"file_path": str(tmp_path / "new.py"),
                                        "content": "a\nb"}, str(tmp_path))
    assert "+a\n+b\n" in detail["diff"]


def test_credential_files_are_never_diffed(tmp_path):
    path = write(tmp_path, "secrets.ini", "token = hunter2\n")
    assert hook.edit_detail("Edit", {"file_path": path, "old_string": "hunter2",
                                     "new_string": "x"}, str(tmp_path)) is None


def test_test_run_counts_from_pytest_summary():
    detail = hook.test_detail("Bash", {"command": "venv/Scripts/python -m pytest -q tests"},
                              {"stdout": "....F\nFAILED tests/x.py::t\n1 failed, 46 passed in 0.31s\n",
                               "stderr": ""})
    assert detail == {"kind": "tests", "summary": "1 failed, 46 passed",
                      "passed": 46, "failed": 1, "ok": False}


def test_non_test_commands_and_missing_summaries_are_ignored():
    assert hook.test_detail("Bash", {"command": "ls"}, {"stdout": "3 passed in 1s"}) is None
    assert hook.test_detail("Bash", {"command": "pytest"}, {"stdout": "collected 0 items"}) is None
