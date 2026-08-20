"""Tests for the live-dictation agreement logic.

Whisper is not a streaming model. Re-run it on a growing recording and it
revises what it previously said -- "I scream" becomes "ice cream" once it hears
another syllable. We are typing into the user's document, where deleting text we
already typed is far worse than being half a second late.

So a word is only typed once two consecutive passes agree on it. That is the
LocalAgreement-2 rule, and everything in this module exists to implement it.
"""

from __future__ import annotations

import pytest

from vocal_advantage.streaming import StreamingTranscript


def test_nothing_is_typed_from_a_single_pass():
    """One pass is a guess. Two passes agreeing is evidence."""
    s = StreamingTranscript()
    assert s.commit("hello there") == ""


def test_words_two_passes_agree_on_are_typed():
    s = StreamingTranscript()
    s.commit("hello there")
    assert s.commit("hello there") == "hello there"


def test_only_the_agreed_prefix_is_typed():
    s = StreamingTranscript()
    s.commit("hello there friend")
    # The tail changed, so only the agreeing head is safe.
    assert s.commit("hello there world") == "hello there"


def test_a_revised_word_is_never_typed():
    """The whole point: 'I scream' must not reach the document before the
    model settles on 'ice cream'."""
    s = StreamingTranscript()
    s.commit("i scream")
    assert s.commit("ice cream") == ""


def test_later_words_are_typed_with_a_leading_space():
    """Otherwise the document reads 'hellothere'."""
    s = StreamingTranscript()
    s.commit("hello there")
    assert s.commit("hello there") == "hello there"
    s.commit("hello there my friend")
    assert s.commit("hello there my friend") == " my friend"


def test_committed_words_are_never_retyped():
    s = StreamingTranscript()
    s.commit("one two")
    assert s.commit("one two") == "one two"
    assert s.commit("one two") == ""
    assert s.commit("one two") == ""


def test_the_final_pass_types_everything_still_owed():
    """On release the recording is transcribed once more in full; whatever has
    not been typed yet has to arrive now, agreement or not."""
    s = StreamingTranscript()
    s.commit("one two three")
    assert s.commit("one two three") == "one two three"
    assert s.finish("one two three four five") == " four five"


def test_the_final_pass_alone_types_the_whole_thing():
    """A dictation short enough that no partial pass ever ran."""
    s = StreamingTranscript()
    assert s.finish("hello world") == "hello world"


def test_the_final_pass_owes_nothing_when_everything_was_typed():
    s = StreamingTranscript()
    s.commit("all done")
    assert s.commit("all done") == "all done"
    assert s.finish("all done") == ""


def test_a_final_pass_that_shortens_the_text_types_nothing_rather_than_deleting():
    """We cannot un-type. If the model's final answer is shorter than what we
    already committed, the honest thing is to add nothing and leave the extra
    word standing -- a documented cost of typing live."""
    s = StreamingTranscript()
    s.commit("one two three")
    assert s.commit("one two three") == "one two three"
    assert s.finish("one two") == ""


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_passes_are_harmless(text):
    s = StreamingTranscript()
    assert s.commit(text) == ""
    assert s.commit(text) == ""
    assert s.finish(text) == ""


def test_whitespace_is_normalised_so_spacing_never_doubles():
    s = StreamingTranscript()
    s.commit("hello   there")
    assert s.commit("hello there") == "hello there"


def test_typed_so_far_reports_what_the_document_should_contain():
    s = StreamingTranscript()
    s.commit("one two")
    s.commit("one two")
    assert s.typed_so_far == "one two"
    s.finish("one two three")
    assert s.typed_so_far == "one two three"


def test_a_session_can_be_reset_for_the_next_dictation():
    s = StreamingTranscript()
    s.commit("first")
    s.commit("first")
    s.reset()
    assert s.typed_so_far == ""
    assert s.commit("second") == ""      # a fresh pass proves nothing again
    assert s.commit("second") == "second"
