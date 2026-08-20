"""The dictation history.

The failure it exists for: a long paragraph is dictated, the paste lands in the
wrong window, and the words are gone. They existed for a moment and nothing
kept them.

JSON Lines rather than a JSON array, and that is the decision worth pinning: one
self-contained object per line means appending is a single write. An array would
have to be read, parsed, extended and rewritten every time, so an interrupted
write could corrupt every previous entry rather than just the last line.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vocal_advantage.history import History, entry, trimmed

WHEN = datetime(2026, 8, 20, 17, 30, 0, tzinfo=timezone.utc)


# --- entry ------------------------------------------------------------------

def test_an_entry_is_one_line_of_json():
    line = entry("hello there", WHEN)
    assert "\n" not in line
    assert json.loads(line)["text"] == "hello there"


def test_an_entry_carries_a_readable_timestamp():
    # Not a Unix epoch: this file is meant to be scrolled by a person looking
    # for something they lost, and a bare number is not readable.
    at = json.loads(entry("hi", WHEN))["at"]
    assert at.startswith("2026-08-20")


def test_the_app_and_duration_are_optional():
    record = json.loads(entry("hi", WHEN))
    assert "app" not in record and "seconds" not in record


def test_the_app_and_duration_are_kept_when_given():
    record = json.loads(entry("hi", WHEN, app="Notes", seconds=3.456))
    assert record["app"] == "Notes"
    assert record["seconds"] == 3.46


def test_accents_stay_readable_rather_than_escaped():
    assert "café" in entry("café", WHEN)


def test_a_newline_in_the_text_cannot_break_the_line_format():
    # JSON escapes it; the guarantee is one record per physical line.
    line = entry("first\nsecond", WHEN)
    assert "\n" not in line
    assert json.loads(line)["text"] == "first\nsecond"


# --- trimmed ----------------------------------------------------------------

def test_trimming_keeps_the_newest():
    assert trimmed(["a", "b", "c", "d"], keep=2) == ["c", "d"]


def test_trimming_a_short_file_changes_nothing():
    assert trimmed(["a", "b"], keep=10) == ["a", "b"]


def test_keeping_nothing_is_honoured():
    assert trimmed(["a", "b"], keep=0) == []


# --- History ----------------------------------------------------------------

def test_a_dictation_is_appended(tmp_path):
    path = tmp_path / "history.jsonl"
    History(path).record("hello there")
    assert json.loads(path.read_text(encoding="utf-8"))["text"] == "hello there"


def test_dictations_accumulate(tmp_path):
    path = tmp_path / "history.jsonl"
    history = History(path)
    history.record("one")
    history.record("two")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(line)["text"] for line in lines] == ["one", "two"]


def test_the_directory_is_created_if_missing(tmp_path):
    path = tmp_path / "logs" / "history.jsonl"
    History(path).record("hello")
    assert path.exists()


def test_a_disabled_history_writes_nothing(tmp_path):
    path = tmp_path / "history.jsonl"
    History(path, enabled=False).record("hello")
    assert not path.exists()


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_an_empty_dictation_is_not_recorded(tmp_path, text):
    path = tmp_path / "history.jsonl"
    History(path).record(text)
    assert not path.exists()


def test_an_unwritable_path_never_stops_a_dictation(tmp_path, capsys):
    # A full disk or a read-only folder costs the history, never the words.
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    history = History(blocked / "history.jsonl")
    history.record("hello")
    assert capsys.readouterr().err != ""


def test_a_write_failure_is_only_reported_once(tmp_path, capsys):
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    history = History(blocked / "history.jsonl")
    for _ in range(5):
        history.record("hello")
    assert capsys.readouterr().err.count("history is off") == 1


def test_the_file_stays_bounded_as_it_grows(tmp_path):
    """Bounded, not exactly `keep`.

    Trimming reads the whole file, so it runs every 200 records rather than on
    every one -- otherwise each dictation gets slower as the history grows, and
    the cost creeps up for weeks before anyone connects it to this. So the file
    sits between `keep` and `keep + 200` lines, and the guarantee is that it
    cannot grow without limit.
    """
    path = tmp_path / "history.jsonl"
    history = History(path, keep=50)
    for i in range(1000):
        history.record(f"line {i}")

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) < 1000, "the file grew without limit"
    assert len(lines) <= 50 + 200
    # Newest kept, oldest dropped.
    assert json.loads(lines[-1])["text"] == "line 999"


def test_trimming_leaves_no_temporary_file_behind(tmp_path):
    path = tmp_path / "history.jsonl"
    history = History(path, keep=20)
    for i in range(500):
        history.record(f"line {i}")
    assert list(tmp_path.glob("*.tmp")) == []


def test_every_kept_line_is_still_valid_json_after_a_trim(tmp_path):
    path = tmp_path / "history.jsonl"
    history = History(path, keep=30)
    for i in range(500):
        history.record(f"line {i}")
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        json.loads(line)
