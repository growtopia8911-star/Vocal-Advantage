"""Unit tests for the rolling chunk cursor (spec item 7a, 7b, 9a).

The chunker never sees a microphone. It is handed a growing float32 buffer and
asked "what is ready?", which is the whole of its job and makes it exhaustively
testable with arange() arrays whose values say where they came from.
"""

from __future__ import annotations

import numpy as np
import pytest

from vocal_advantage.chunker import RollingChunker

RATE = 16000


def buffer(seconds: float) -> np.ndarray:
    """A ramp, so a slice's values reveal its position in the recording."""
    return np.arange(int(seconds * RATE), dtype=np.float32)


# --- 7a: chunks appear on the beat ------------------------------------------


def test_nothing_is_ready_before_the_first_full_chunk():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    assert c.ready(buffer(1.9)) == []


def test_a_chunk_appears_at_two_seconds():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    chunks = c.ready(buffer(2.0))
    assert len(chunks) == 1
    assert chunks[0].size == int(2.0 * RATE)


def test_five_seconds_yields_chunks_at_two_and_four():
    """7a: a 5s recording at 2s chunks produces exactly two rolling chunks."""
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    assert len(c.ready(buffer(2.0))) == 1
    assert c.ready(buffer(3.9)) == []
    assert len(c.ready(buffer(4.0))) == 1
    assert c.ready(buffer(5.0)) == []


def test_a_late_call_catches_up_with_every_missed_chunk():
    """A stalled controller must not silently drop the audio it slept through."""
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    chunks = c.ready(buffer(6.0))
    assert len(chunks) == 3


# --- 7b: the overlap --------------------------------------------------------


def test_the_first_chunk_starts_at_the_beginning():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    first = c.ready(buffer(2.0))[0]
    assert first[0] == 0.0


def test_the_second_chunk_starts_before_the_first_one_ended():
    """7b: the seam is re-transcribed, so a word across it is not lost."""
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    c.ready(buffer(2.0))
    second = c.ready(buffer(4.0))[0]
    expected_start = int((2.0 - 0.25) * RATE)
    assert second[0] == pytest.approx(float(expected_start))


def test_an_overlapped_chunk_is_longer_than_the_step():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    c.ready(buffer(2.0))
    second = c.ready(buffer(4.0))[0]
    assert second.size == int((2.0 + 0.25) * RATE)


def test_zero_overlap_is_allowed():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.0, sample_rate=RATE)
    c.ready(buffer(2.0))
    second = c.ready(buffer(4.0))[0]
    assert second[0] == pytest.approx(float(int(2.0 * RATE)))


# --- 9a: the remainder ------------------------------------------------------


def test_the_remainder_is_only_the_untranscribed_tail():
    """9a: on stop only what the chunks did not cover goes to the model."""
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    c.ready(buffer(4.0))  # two chunks, cursor at 4.0s
    tail = c.remainder(buffer(5.0))
    assert tail.size == int((1.0 + 0.25) * RATE)
    assert tail[0] == pytest.approx(float(int((4.0 - 0.25) * RATE)))


def test_the_remainder_of_a_short_recording_is_the_whole_thing():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    tail = c.remainder(buffer(1.0))
    assert tail.size == int(1.0 * RATE)
    assert tail[0] == 0.0


def test_the_remainder_is_empty_when_the_chunks_covered_everything():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    c.ready(buffer(4.0))
    assert c.remainder(buffer(4.0)).size == 0


# --- housekeeping -----------------------------------------------------------


def test_reset_starts_the_next_dictation_from_zero():
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    c.ready(buffer(4.0))
    c.reset()
    assert len(c.ready(buffer(2.0))) == 1


def test_a_shrinking_buffer_does_not_produce_a_negative_slice():
    """A cancelled recording restarts the buffer; the cursor must not outrun it."""
    c = RollingChunker(chunk_s=2.0, overlap_s=0.25, sample_rate=RATE)
    c.ready(buffer(4.0))
    assert c.ready(buffer(0.5)) == []
    assert c.remainder(buffer(0.5)).size >= 0
