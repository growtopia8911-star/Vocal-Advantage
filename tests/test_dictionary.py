"""The personal dictionary: names and jargon Whisper keeps getting wrong.

Two halves that work at opposite ends of the pipeline, which is the whole
design:

* **words** are handed to Whisper as *hotwords* before it transcribes, so it
  leans toward hearing them in the first place. A nudge, not a guarantee.
* **fixes** are applied to the finished text, so anything that still slipped
  through gets corrected. This runs LAST in the pipeline, after every cleanup
  pass, so nothing downstream can quietly undo a correction.

Everything here is pure text handling and file loading, so all of it is tested.
The rule the whole file serves: **a broken dictionary must never stop dictation.**
It is an accuracy aid; losing it costs accuracy, not the product.
"""

from __future__ import annotations

import json

import pytest

from vocal_advantage.dictionary import (
    Dictionary,
    apply_fixes,
    hotword_text,
    load_dictionary,
)


# --- apply_fixes: the correcting half ---------------------------------------

FIXES = {"kelvin": "Kevin", "vocal advantaged": "Vocal Advantage"}


def test_a_known_mistake_is_corrected():
    assert apply_fixes("send it to kelvin", FIXES) == "send it to Kevin"


def test_matching_ignores_case():
    assert apply_fixes("Send it to KELVIN", FIXES) == "Send it to KEVIN"


def test_a_capitalised_mistake_stays_capitalised():
    # Whisper capitalises after a full stop. Replacing "Kelvin" with a
    # lower-case correction would break the sentence it is starting.
    assert apply_fixes("Kelvin is here.", {"kelvin": "kevin"}) == "Kevin is here."


def test_an_all_caps_mistake_stays_all_caps():
    assert apply_fixes("TELL KELVIN", FIXES) == "TELL KEVIN"


def test_the_replacement_keeps_its_own_capitals_by_default():
    # A name written "Kevin" in the dictionary must come out "Kevin" even when
    # the mistake was heard in lower case.
    assert apply_fixes("tell kelvin", FIXES) == "tell Kevin"


def test_a_fix_does_not_match_inside_a_longer_word():
    # The bug this prevents: "kelvin" inside "kelvinator" becoming "Kevinator".
    assert apply_fixes("a kelvinator fridge", FIXES) == "a kelvinator fridge"


def test_a_fix_matches_next_to_punctuation():
    assert apply_fixes("thanks, kelvin!", FIXES) == "thanks, Kevin!"


def test_a_multi_word_phrase_is_corrected():
    assert (
        apply_fixes("welcome to vocal advantaged", FIXES)
        == "welcome to Vocal Advantage"
    )


def test_a_longer_phrase_wins_over_a_shorter_one():
    # Applied shortest-first, "vocal" would fire inside "vocal advantaged" and
    # the longer rule could never match.
    fixes = {"vocal": "Vokal", "vocal advantaged": "Vocal Advantage"}
    assert apply_fixes("vocal advantaged", fixes) == "Vocal Advantage"


def test_corrections_do_not_cascade():
    # One pass over the original text. Chaining a -> b -> c would make the
    # result depend on dict ordering, and could loop forever on a cycle.
    assert apply_fixes("a", {"a": "b", "b": "c"}) == "b"


def test_a_cycle_cannot_hang():
    assert apply_fixes("a b", {"a": "b", "b": "a"}) == "b a"


def test_an_empty_dictionary_changes_nothing():
    assert apply_fixes("send it to kelvin", {}) == "send it to kelvin"


def test_empty_text_is_safe():
    assert apply_fixes("", FIXES) == ""


def test_every_occurrence_is_corrected():
    assert apply_fixes("kelvin and kelvin", FIXES) == "Kevin and Kevin"


def test_a_fix_containing_regex_characters_is_taken_literally():
    # "c++" would otherwise be an invalid pattern and take the whole app down
    # on a hand-edited dictionary.
    assert apply_fixes("i like c++ a lot", {"c++": "C++"}) == "i like C++ a lot"


def test_a_fix_with_a_dot_does_not_match_any_character():
    assert apply_fixes("a xby c", {"x.y": "Z"}) == "a xby c"


def test_an_empty_key_is_ignored_rather_than_matching_everywhere():
    assert apply_fixes("hello", {"": "X"}) == "hello"


# --- hotword_text: the biasing half -----------------------------------------

def test_hotwords_are_joined_into_one_string():
    assert hotword_text(["Obsidian", "pytest"]) == "Obsidian, pytest"


def test_no_words_gives_an_empty_string():
    # Empty, not None: faster-whisper is handed this directly, and "" is the
    # documented way to say "no hotwords".
    assert hotword_text([]) == ""


def test_blank_and_duplicate_words_are_dropped():
    assert hotword_text(["Obsidian", "  ", "", "Obsidian"]) == "Obsidian"


def test_word_order_is_preserved():
    assert hotword_text(["b", "a", "c"]) == "b, a, c"


# --- loading ----------------------------------------------------------------

def test_a_missing_file_is_created_with_an_empty_dictionary(tmp_path):
    path = tmp_path / "dictionary.json"
    loaded = load_dictionary(path)
    assert loaded.words == []
    assert loaded.fixes == {}
    assert path.exists(), "first run should leave a file to edit"


def test_the_created_file_explains_itself(tmp_path):
    # It is meant to be hand-edited, and JSON cannot carry comments.
    path = tmp_path / "dictionary.json"
    load_dictionary(path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "words" in written and "fixes" in written
    assert any("_" in key for key in written), "no help key to explain the format"


def test_a_real_dictionary_is_read_back(tmp_path):
    path = tmp_path / "dictionary.json"
    path.write_text(
        json.dumps({"words": ["Obsidian"], "fixes": {"kelvin": "Kevin"}}),
        encoding="utf-8",
    )
    loaded = load_dictionary(path)
    assert loaded.words == ["Obsidian"]
    assert loaded.fixes == {"kelvin": "Kevin"}


def test_broken_json_warns_and_still_dictates(tmp_path, capsys):
    # The rule: the dictionary is an accuracy aid. Losing it costs accuracy,
    # never the product.
    path = tmp_path / "dictionary.json"
    path.write_text("{not json at all", encoding="utf-8")
    loaded = load_dictionary(path)
    assert loaded.words == [] and loaded.fixes == {}
    assert capsys.readouterr().err != ""


def test_a_broken_file_is_not_overwritten(tmp_path):
    # The user must still be able to see what they typed and repair it.
    path = tmp_path / "dictionary.json"
    original = "{not json at all"
    path.write_text(original, encoding="utf-8")
    load_dictionary(path)
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "payload",
    ['{"words": "Obsidian"}', '{"words": [1, 2]}', '{"fixes": ["a", "b"]}',
     '{"fixes": {"a": 3}}', "[]", '"a string"'],
)
def test_wrong_shapes_warn_and_fall_back(tmp_path, capsys, payload):
    path = tmp_path / "dictionary.json"
    path.write_text(payload, encoding="utf-8")
    loaded = load_dictionary(path)
    assert isinstance(loaded.words, list)
    assert isinstance(loaded.fixes, dict)
    assert capsys.readouterr().err != ""


def test_a_partial_dictionary_keeps_the_half_that_is_valid(tmp_path):
    path = tmp_path / "dictionary.json"
    path.write_text('{"words": ["Obsidian"]}', encoding="utf-8")
    loaded = load_dictionary(path)
    assert loaded.words == ["Obsidian"]
    assert loaded.fixes == {}


# --- the Dictionary object ---------------------------------------------------

def test_apply_runs_the_fixes():
    assert Dictionary(words=[], fixes=FIXES).apply("hi kelvin") == "hi Kevin"


def test_hotwords_come_from_the_words_list():
    assert Dictionary(words=["Obsidian"], fixes={}).hotwords == "Obsidian"


def test_an_empty_dictionary_is_falsey():
    # main.py uses this to skip wiring the pass in at all.
    assert not Dictionary(words=[], fixes={})
    assert Dictionary(words=["x"], fixes={})
    assert Dictionary(words=[], fixes={"a": "b"})


def test_apply_on_an_empty_dictionary_is_the_identity():
    text = "nothing to change here"
    assert Dictionary(words=[], fixes={}).apply(text) is text
