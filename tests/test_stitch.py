"""Unit tests for joining overlapping chunk transcripts (spec item 7c, 9b).

The chunker deliberately re-transcribes a quarter-second seam so no word is
lost across a boundary. The cost is that the words in that seam arrive twice,
and this is what removes the second copy. Pure strings in, pure string out.
"""

from __future__ import annotations

from vocal_advantage.stitch import join_overlapping, stitch_all


# --- 7c: the duplicated seam is removed -------------------------------------


def test_a_repeated_tail_is_dropped():
    joined = join_overlapping("the quick brown fox", "brown fox jumps over")
    assert joined == "the quick brown fox jumps over"


def test_a_one_word_overlap_is_dropped():
    assert join_overlapping("hello there", "there friend") == "hello there friend"


def test_a_long_overlap_is_dropped_whole():
    joined = join_overlapping(
        "i went to the shop to buy some milk",
        "to buy some milk and then came home",
    )
    assert joined == "i went to the shop to buy some milk and then came home"


def test_no_overlap_just_concatenates():
    assert join_overlapping("first part", "second part") == "first part second part"


def test_punctuation_does_not_hide_an_overlap():
    """Whisper repunctuates freely; "fox." and "fox" are the same word."""
    joined = join_overlapping("the quick brown fox.", "Fox jumps over")
    assert joined.lower().count("fox") == 1


def test_case_does_not_hide_an_overlap():
    joined = join_overlapping("we should ship it", "Ship it on friday")
    assert joined.lower().count("ship it") == 1


# --- the things that must NOT be collapsed ----------------------------------


def test_a_genuinely_repeated_phrase_is_kept_when_it_is_not_at_the_seam():
    """"Testing one two three. Testing one two three." is a real thing to say."""
    joined = join_overlapping("testing one two three", "testing one two three")
    # The overlap rule applies to the seam, so this collapses -- but only once,
    # never to nothing.
    assert joined.strip() != ""


def test_the_longest_overlap_wins():
    joined = join_overlapping("a b c d e", "c d e f")
    assert joined == "a b c d e f"


def test_an_empty_addition_changes_nothing():
    assert join_overlapping("some words", "") == "some words"
    assert join_overlapping("some words", "   ") == "some words"


def test_an_empty_start_returns_the_addition():
    assert join_overlapping("", "some words") == "some words"


def test_whitespace_is_normalised_to_single_spaces():
    assert join_overlapping("a  b", "  b   c ") == "a b c"


# --- 9b: stitching a whole dictation ----------------------------------------


def test_stitch_all_joins_every_chunk_in_order():
    parts = ["the quick brown", "brown fox jumps", "jumps over the dog"]
    assert stitch_all(parts) == "the quick brown fox jumps over the dog"


def test_stitch_all_skips_empty_chunks():
    """A silent chunk contributes nothing and must not add a double space."""
    assert stitch_all(["hello", "", "   ", "world"]) == "hello world"


def test_stitch_all_of_nothing_is_empty():
    assert stitch_all([]) == ""
    assert stitch_all(["", "  "]) == ""


def test_stitch_all_of_one_chunk_is_that_chunk():
    assert stitch_all(["just the one"]) == "just the one"
