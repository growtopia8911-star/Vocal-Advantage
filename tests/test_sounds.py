"""The generated tones.

No .wav files in the repo, same principle as the tray icon. What is worth
asserting is the shape of the waveform, because a tone that starts or ends at a
non-zero sample produces an audible click -- which sounds like a fault rather
than a chime, and is exactly the kind of thing nobody thinks to test.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from vocal_advantage.sounds import TONES, Player, chime, tone


def test_a_tone_is_the_length_it_asks_for():
    assert tone(440.0, 100.0, samplerate=44100).size == 4410


def test_a_tone_is_float32_in_range():
    samples = tone(440.0, 50.0)
    assert samples.dtype == np.float32
    assert np.abs(samples).max() <= 1.0


def test_a_tone_starts_and_ends_at_silence():
    # The click guard. A waveform cut off mid-cycle pops.
    samples = tone(440.0, 80.0)
    assert abs(float(samples[0])) < 1e-4
    assert abs(float(samples[-1])) < 1e-4


def test_a_tone_actually_makes_a_sound():
    assert np.abs(tone(440.0, 80.0)).max() > 0.05


def test_a_tone_is_quiet_enough_to_live_with():
    # It fires many times a day. This is a confirmation, not an alarm.
    assert np.abs(tone(440.0, 80.0)).max() < 0.35


@pytest.mark.parametrize("kind", sorted(TONES))
def test_every_named_chime_produces_samples(kind):
    assert chime(kind).size > 0


@pytest.mark.parametrize("kind", sorted(TONES))
def test_every_chime_starts_and_ends_at_silence(kind):
    samples = chime(kind)
    assert abs(float(samples[0])) < 1e-4
    assert abs(float(samples[-1])) < 1e-4


def test_the_three_chimes_are_distinguishable():
    # If done and error sound the same there is no point playing either.
    assert not np.array_equal(chime("done"), chime("error"))
    assert not np.array_equal(chime("start"), chime("done"))


def test_an_unknown_chime_is_silence_rather_than_an_error():
    assert chime("nonsense").size == 0


def test_every_chime_is_short():
    # Long enough to hear, short enough not to overlap the next dictation.
    for kind in TONES:
        assert chime(kind).size / 44100 < 0.5


# --- the player -------------------------------------------------------------

def test_a_disabled_player_plays_nothing(monkeypatch):
    played = []
    player = Player(enabled=False)
    monkeypatch.setattr(player, "_play_now", lambda kind: played.append(kind))
    player.play("done")
    assert played == []


def test_the_start_tone_is_off_by_default():
    # It would play while the microphone is open. On speakers it goes straight
    # back into the recording and Whisper transcribes something for it.
    assert Player().on_start is False


def test_the_start_tone_is_skipped_unless_asked_for():
    played = []
    player = Player(enabled=True, on_start=False)
    player._play_now = lambda kind: played.append(kind)
    player.play("start")
    assert played == []


def test_the_start_tone_plays_when_switched_on():
    played = []
    player = Player(enabled=True, on_start=True)
    player._play_now = lambda kind: played.append(kind)
    player.play("start")
    # It runs on a background thread, so give it a moment to arrive.
    import time

    for _ in range(400):
        if played:
            break
        time.sleep(0.005)
    assert played == ["start"]


class DeadAudio:
    """A sounddevice that has no output device, which is a real machine."""

    @staticmethod
    def play(*_args, **_kwargs):
        raise RuntimeError("no output device")


def test_a_machine_with_no_speaker_still_dictates(monkeypatch, capsys):
    # Patched into sys.modules rather than replacing _play_now: replacing the
    # method would let the exception escape onto the worker thread, which is
    # the test lying about the thing it claims to check.
    monkeypatch.setitem(sys.modules, "sounddevice", DeadAudio)
    Player(enabled=True)._play_now("done")
    assert "Sounds are unavailable" in capsys.readouterr().err


def test_a_missing_device_is_only_reported_once(monkeypatch, capsys):
    # It runs on every dictation; five hundred identical warnings would bury
    # the console output that matters.
    monkeypatch.setitem(sys.modules, "sounddevice", DeadAudio)
    player = Player(enabled=True)
    for _ in range(5):
        player._play_now("done")
    assert capsys.readouterr().err.count("Sounds are unavailable") == 1


def test_the_tests_never_actually_make_a_noise(monkeypatch):
    # Belt and braces on the suite itself: a test that really plays audio is
    # obnoxious to run and impossible to run on CI.
    monkeypatch.setitem(sys.modules, "sounddevice", DeadAudio)
    Player(enabled=True, on_start=True)._play_now("start")
