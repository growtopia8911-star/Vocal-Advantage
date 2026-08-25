"""Unit tests for silence trimming and the silence watchdog (spec items 8, 6b).

Pure numpy. "Speech" here is a loud sine; "silence" is zeros or near-zeros.
Nothing loads a model, so these run in milliseconds.
"""

from __future__ import annotations

import numpy as np
import pytest

from vocal_advantage.vad import (
    SAMPLE_RATE,
    is_silent,
    trailing_silence_s,
    trim_silence,
)


def speech(seconds: float, amplitude: float = 0.4) -> np.ndarray:
    """A loud tone -- what the trimmer must always keep."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def silence(seconds: float, amplitude: float = 0.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    if amplitude == 0.0:
        return np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(0)
    return (rng.standard_normal(n) * amplitude).astype(np.float32)


# --- 8a: silence is trimmed -------------------------------------------------


def test_leading_silence_is_trimmed(clip=None):
    clip = np.concatenate([silence(1.0), speech(1.0)])
    trimmed = trim_silence(clip)
    assert trimmed.size < clip.size
    # The speech itself survives, give or take the guard padding.
    assert trimmed.size >= int(0.9 * SAMPLE_RATE)


def test_trailing_silence_is_trimmed():
    clip = np.concatenate([speech(1.0), silence(1.5)])
    trimmed = trim_silence(clip)
    assert trimmed.size < clip.size
    assert trimmed.size >= int(0.9 * SAMPLE_RATE)


def test_silence_at_both_ends_is_trimmed():
    clip = np.concatenate([silence(0.8), speech(1.0), silence(0.8)])
    trimmed = trim_silence(clip)
    assert trimmed.size == pytest.approx(int(1.0 * SAMPLE_RATE), abs=int(0.35 * SAMPLE_RATE))


def test_quiet_room_noise_counts_as_silence():
    """Real rooms are not digital zero. A quiet hiss must still trim."""
    clip = np.concatenate([silence(1.0, amplitude=0.0008), speech(1.0)])
    trimmed = trim_silence(clip)
    assert trimmed.size < clip.size


# --- 8c: speech is never cut ------------------------------------------------


def test_a_gap_between_words_is_not_cut_out():
    """8c: only the ends are trimmed. A pause mid-sentence stays put."""
    clip = np.concatenate([speech(0.5), silence(0.4), speech(0.5)])
    trimmed = trim_silence(clip)
    assert trimmed.size == pytest.approx(clip.size, abs=int(0.2 * SAMPLE_RATE))


def test_all_speech_is_returned_unchanged():
    clip = speech(2.0)
    assert trim_silence(clip).size == clip.size


def test_trimming_keeps_a_little_padding_around_the_speech():
    """Cutting flush to the first loud sample clips the attack of a word."""
    clip = np.concatenate([silence(1.0), speech(1.0), silence(1.0)])
    trimmed = trim_silence(clip)
    assert trimmed.size > int(1.0 * SAMPLE_RATE)


# --- 8b: an all-silent chunk is skipped -------------------------------------


def test_a_silent_clip_is_reported_silent():
    assert is_silent(silence(2.0)) is True
    assert is_silent(silence(2.0, amplitude=0.0005)) is True


def test_a_clip_with_speech_is_not_reported_silent():
    assert is_silent(np.concatenate([silence(1.0), speech(0.5)])) is False


def test_an_empty_clip_is_silent_and_trims_to_empty():
    empty = np.empty(0, dtype=np.float32)
    assert is_silent(empty) is True
    assert trim_silence(empty).size == 0


def test_trimming_an_entirely_silent_clip_yields_nothing():
    """8b: nothing is left to send, which is what lets the caller skip it."""
    assert trim_silence(silence(2.0)).size == 0


# --- 6b: the silence watchdog -----------------------------------------------


def test_trailing_silence_is_measured_from_the_end():
    clip = np.concatenate([speech(1.0), silence(2.0)])
    assert trailing_silence_s(clip) == pytest.approx(2.0, abs=0.2)


def test_trailing_silence_is_zero_while_speaking():
    clip = np.concatenate([silence(1.0), speech(1.0)])
    assert trailing_silence_s(clip) == pytest.approx(0.0, abs=0.2)


def test_an_entirely_silent_clip_is_all_trailing_silence():
    assert trailing_silence_s(silence(3.0)) == pytest.approx(3.0, abs=0.2)


def test_trailing_silence_of_nothing_is_zero():
    """An empty buffer must not read as "silent for ages" and auto-stop."""
    assert trailing_silence_s(np.empty(0, dtype=np.float32)) == 0.0
