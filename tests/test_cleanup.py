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

from vocal_advantage.cleanup import collapse_corrections  # noqa: F401
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


# ---------------------------------------------------------------------------
# Self-corrections
#
# "Tuesday, no, Wednesday" should type Wednesday. The AI pass used to do this
# and was withdrawn in f26c54d, because on real hardware it collapsed that
# exact sentence BACKWARDS -- it kept Tuesday, the day Kevin had just rejected,
# and nothing caught it.
#
# A model has to infer which side was meant. A rule does not: the marker says
# so. Keeping what follows "no" cannot produce the rejected day, which is why
# this is a rule and not a prompt.
# ---------------------------------------------------------------------------


def test_a_correction_keeps_the_word_after_the_marker():
    assert clean_speech("Let's meet Tuesday, no, Wednesday.") == "Let's meet Wednesday."


def test_the_rejected_word_is_never_the_one_kept():
    """The specific failure this exists to make impossible."""
    out = clean_speech("Let's meet Tuesday, no, Wednesday.")

    assert "Wednesday" in out
    assert "Tuesday" not in out


def test_a_full_stop_before_the_marker_works_too():
    """Whisper writes a comma when the speaker runs on and a full stop when
    they pause. Kevin's real dictation produced the full stop, so a rule that
    only handled commas would have missed the case it was built for."""
    assert clean_speech("Let us meet Tuesday. No, Wednesday.") == "Let us meet Wednesday."


def test_a_multi_word_correction_takes_back_the_same_number_of_words():
    # "next Tuesday" -> "next Wednesday", not "next next Wednesday".
    assert (clean_speech("Let us meet next Tuesday, no, next Wednesday.")
            == "Let us meet next Wednesday.")


def test_sorry_marks_a_correction_as_well_as_no():
    assert clean_speech("Ship it Friday, sorry, Thursday.") == "Ship it Thursday."


def test_a_filler_inside_the_correction_does_not_break_it():
    # Fillers are removed first, so the marker and the replacement end up
    # adjacent by the time this rule looks for them.
    assert (clean_speech("Let's meet Tuesday, no, uh, Wednesday.")
            == "Let's meet Wednesday.")


def test_no_at_the_start_of_a_sentence_is_not_a_correction():
    """There is nothing in front of it to take back."""
    said = "No, I do not think that is right."

    assert clean_speech(said) == said


def test_no_without_a_delimiter_before_it_is_not_a_correction():
    """"I told him no" -- the "no" is what he was told, not a retraction.
    Collapsing it would delete "him"."""
    said = "I told him no, and he left."

    assert clean_speech(said) == said


def test_a_stock_reply_after_the_marker_is_not_a_correction():
    """Otherwise "That works. No, thanks." becomes "That thanks."."""
    said = "That works. No, thanks."

    assert clean_speech(said) == said


def test_a_long_tail_after_the_marker_is_left_alone():
    """Someone carrying on talking, not swapping a phrase. Matching its length
    would delete most of the sentence."""
    said = "Let us meet Tuesday, no, let us do it at the end of the week."

    assert clean_speech(said) == said


def test_a_correction_never_invents_a_word():
    """The promise the whole module makes: removal only. Every surviving word
    must be one the speaker actually said."""
    said = "Send it to Dave, no, Sarah."
    out = clean_speech(said)

    said_words = {w.strip(",.").lower() for w in said.split()}
    for word in out.split():
        assert word.strip(",.").lower() in said_words
