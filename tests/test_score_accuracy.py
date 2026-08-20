"""Tests for the accuracy scorer's arithmetic.

The other tools/ scripts are unmeasured on purpose -- they open microphones and
windows. This one is different: it is the measuring instrument. If the word
error rate is wrong then every accuracy claim made from it is wrong, and
nothing downstream would reveal it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from tools.score_accuracy import normalise, wer


@pytest.mark.parametrize(
    ("reference", "hypothesis", "expected"),
    [
        ("hello there", "hello there", 0.0),
        ("hello there", "hello", 0.5),          # one deletion of two words
        ("a b c d", "a x c d", 0.25),           # one substitution of four
        ("a b c", "a b c d", 1 / 3),            # one insertion
        ("hello", "Hello.", 0.0),               # case and punctuation ignored
        ("", "", 0.0),
        ("", "unexpected words", 1.0),
        ("something", "", 1.0),
    ],
)
def test_word_error_rate(reference, hypothesis, expected):
    rate, _, _ = wer(reference, hypothesis)
    assert rate == pytest.approx(expected)


def test_an_apostrophe_is_part_of_the_word():
    """"don't" and "dont" are one edit apart, not zero -- the app really does
    repair contractions and the score must be able to see it."""
    assert normalise("Don't") == ["don't"]
    assert wer("don't", "dont")[0] == pytest.approx(1.0)


def test_the_edit_count_and_reference_length_come_back_too():
    """The overall row sums edits and reference words across clips; averaging
    the per-clip percentages instead would weight a three-word clip the same
    as a thirty-word one."""
    rate, edits, ref_len = wer("a b c d", "a x c d")
    assert (edits, ref_len) == (1, 4)
    assert rate == pytest.approx(edits / ref_len)


# -- fairness of the measure ------------------------------------------------


def test_spelled_out_numbers_score_the_same_as_digits():
    """Whisper writes "42" for "forty two" and that is desirable, not an error.
    Scoring it as three edits made the digits clip look 62% wrong when the only
    real mistakes in it were "are" for "were" and "suit" for "suite"."""
    assert wer("forty two failures", "42 failures")[0] == pytest.approx(0.0)
    assert wer("twenty minutes", "20 minutes")[0] == pytest.approx(0.0)
    assert wer("one hundred", "100")[0] == pytest.approx(0.0)


def test_a_real_number_error_is_still_an_error():
    assert wer("forty two failures", "43 failures")[0] > 0
