"""Tests for filler and stutter removal.

Whisper hands back well-punctuated text -- Kevin's "um so I I think we should
uh ship it on friday" came back as "Um, so I think we should ship it on
Friday." It even resolved the stutter. What it does not do is drop the filler,
so that is the whole job here.

This runs BEFORE the streaming agreement logic sees the text, so the cleaned
words are the only words that ever reach the document. Cleaning afterwards
would put StreamingTranscript's idea of the document out of step with the real
one, which is the bug fixed in 8dda0cd.
"""

from __future__ import annotations

import pytest

from vocal_advantage.cleanup import clean_speech


# -- the case that started it ----------------------------------------------


def test_the_sentence_kevin_actually_dictated():
    assert clean_speech("Um, so I think we should ship it on Friday.") == (
        "So I think we should ship it on Friday."
    )


# -- fillers ---------------------------------------------------------------


def test_a_filler_in_the_middle_is_removed():
    assert clean_speech("I think, um, we should") == "I think, we should"


@pytest.mark.parametrize("filler", ["um", "uh", "er", "erm", "ah", "hmm", "mm"])
def test_every_filler_word_is_removed(filler):
    assert clean_speech(f"send {filler} the file") == "send the file"


@pytest.mark.parametrize("word", ["umbrella", "uhuru", "ermine", "another", "hmmm"])
def test_words_that_merely_start_like_a_filler_survive(word):
    """Substring matching here would silently eat real words."""
    assert word in clean_speech(f"the {word} is fine")


@pytest.mark.parametrize("written", ["Um", "um", "UM", "Um,", "um..."])
def test_fillers_are_removed_whatever_the_case_or_punctuation(written):
    assert clean_speech(f"{written} send it") == "Send it"


# -- stutters --------------------------------------------------------------


def test_a_doubled_word_collapses():
    assert clean_speech("send the the file") == "send the file"


def test_a_doubled_pronoun_collapses():
    assert clean_speech("I I think so") == "I think so"


def test_the_tidier_of_a_doubled_pair_survives():
    """'I, I think' must not become 'I, think'."""
    assert clean_speech("I, I think so") == "I think so"


def test_the_tidier_one_wins_even_when_it_comes_first():
    """Guards the choice itself. In "I, I think" the clean token is last, so
    "keep the last" passes that test by luck; here it would leave "I, think"."""
    assert clean_speech("I I, think so") == "I think so"


def test_a_word_repeated_legitimately_far_apart_is_kept():
    assert clean_speech("the file and the folder") == "the file and the folder"


def test_had_had_is_collapsed_too():
    """No cleverness about which repeats are grammatical -- dictation says
    the same word twice by accident far more often than on purpose."""
    assert clean_speech("we we should go") == "we should go"


# -- capitalisation --------------------------------------------------------


def test_removing_the_first_word_recapitalises_the_new_one():
    assert clean_speech("Um, so I think") == "So I think"


def test_a_mid_sentence_removal_does_not_capitalise_anything():
    assert clean_speech("I think um so") == "I think so"


# -- safety ----------------------------------------------------------------


def test_clean_text_comes_back_character_for_character_identical():
    original = "So I think we should ship it on Friday."
    assert clean_speech(original) == original


def test_cleaning_is_idempotent():
    once = clean_speech("Um, so I I think we should uh ship it")
    assert clean_speech(once) == once


def test_an_all_filler_utterance_cleans_to_nothing():
    assert clean_speech("um uh er") == ""


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_input_is_harmless(text):
    assert clean_speech(text) == ""


def test_no_word_is_ever_invented():
    """Removal and capitalisation only -- never a substitution."""
    said = "um so the the API kept timing out uh badly"
    cleaned = clean_speech(said)
    said_words = {w.strip(",.?!").lower() for w in said.split()}
    for word in cleaned.split():
        assert word.strip(",.?!").lower() in said_words, word
