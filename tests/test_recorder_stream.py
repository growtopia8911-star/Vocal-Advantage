"""Unit tests for the always-open microphone stream (spec item 2).

Same seam as test_recorder.py: ``recorder.sd`` is replaced with a fake, so all
of the real logic runs with no PortAudio and no microphone. The fake itself is
imported from that file rather than copied -- two drifting fakes would be worse
than the duplication they saved.

What changed and why it needed its own file: the stream used to open on the
keypress and close on release. It now opens once at startup and stays open, so
the first block of audio is already flowing when the hotkey is pressed instead
of being 100-300ms of PortAudio device negotiation away. Audio arriving while
nobody is dictating goes into a small rolling buffer and is thrown away.
"""

from __future__ import annotations

import numpy as np
import pytest

from vocal_advantage import recorder as recorder_module
from vocal_advantage.recorder import SAMPLE_RATE, Recorder, RecorderError

from tests.test_recorder import FakeSounddevice, fake_sd  # noqa: F401


def block(seconds: float, value: float = 0.2) -> np.ndarray:
    return np.full(int(seconds * SAMPLE_RATE), value, dtype=np.float32)


# --- 2a: the stream is open before any key is pressed -----------------------


def test_open_starts_a_stream(fake_sd):
    r = Recorder()
    assert r.is_open is False
    r.open()
    assert r.is_open is True
    assert len(fake_sd.streams) == 1
    assert fake_sd.stream.started is True


def test_open_twice_does_not_open_a_second_stream(fake_sd):
    r = Recorder()
    r.open()
    r.open()
    assert len(fake_sd.streams) == 1


def test_the_stream_is_open_but_not_capturing(fake_sd):
    """Open is not recording. The distinction is the whole feature."""
    r = Recorder()
    r.open()
    assert r.is_open is True
    assert r.is_recording is False


# --- 2b/2c: idle audio is discarded -----------------------------------------


def test_audio_arriving_while_idle_is_not_captured(fake_sd):
    """2b: ten seconds of idle then one second of speech returns one second."""
    r = Recorder()
    r.open()
    for _ in range(10):
        fake_sd.stream.feed(block(1.0))
    r.start()
    fake_sd.stream.feed(block(1.0))
    captured = r.stop()
    assert captured.size == pytest.approx(SAMPLE_RATE, abs=SAMPLE_RATE // 10)


def test_the_idle_buffer_is_capped(fake_sd):
    """2c: idling for an hour must not grow the process by an hour of audio."""
    r = Recorder()
    r.open()
    for _ in range(60):
        fake_sd.stream.feed(block(1.0))
    assert r.idle_samples <= r.idle_cap_samples


def test_the_idle_buffer_is_dropped_when_capture_begins(fake_sd):
    """Nothing said before the keypress belongs in the transcript."""
    r = Recorder()
    r.open()
    fake_sd.stream.feed(block(0.5, value=0.9))
    r.start()
    fake_sd.stream.feed(block(0.5, value=0.1))
    captured = r.stop()
    assert float(np.max(np.abs(captured))) == pytest.approx(0.1, abs=0.01)


# --- 2d: the keypress does not open anything --------------------------------


def test_start_on_an_open_recorder_opens_no_new_stream(fake_sd):
    """2d: the press must cost nothing but a flag flip."""
    r = Recorder()
    r.open()
    assert len(fake_sd.streams) == 1
    r.start()
    assert len(fake_sd.streams) == 1


def test_stop_leaves_the_stream_open(fake_sd):
    """The replacement for the old close-on-release behaviour.

    NOTE this is the deliberate reversal of the original design, which closed
    the stream between dictations so the OS "microphone in use" indicator went
    out while idle. Requirement 2 asks for the stream to stay open; the
    indicator therefore stays lit for as long as the app runs.
    """
    r = Recorder()
    r.open()
    r.start()
    fake_sd.stream.feed(block(0.2))
    r.stop()
    assert r.is_open is True
    assert fake_sd.stream.closed is False


def test_many_dictations_reuse_the_one_stream(fake_sd):
    r = Recorder()
    r.open()
    for _ in range(5):
        r.start()
        fake_sd.stream.feed(block(0.1))
        r.stop()
    assert len(fake_sd.streams) == 1


# --- 2e: shutdown -----------------------------------------------------------


def test_close_stops_the_stream(fake_sd):
    r = Recorder()
    r.open()
    r.close()
    assert r.is_open is False
    assert fake_sd.stream.closed is True


def test_close_while_recording_still_closes(fake_sd):
    r = Recorder()
    r.open()
    r.start()
    r.close()
    assert r.is_open is False
    assert r.is_recording is False


def test_close_on_an_unopened_recorder_is_harmless(fake_sd):
    Recorder().close()


def test_close_of_an_unplugged_device_does_not_raise(fake_sd):
    """Shutdown must not be the thing that throws on the way out."""
    r = Recorder()
    r.open()
    fake_sd.unplug()
    r.close()
    assert r.is_open is False


# --- 11a: when the first block of a capture arrived --------------------------


def test_first_block_time_is_unset_until_audio_arrives(fake_sd):
    r = Recorder()
    r.open()
    r.start()
    assert r.first_block_at is None


def test_first_block_time_is_stamped_by_the_audio_thread(fake_sd):
    ticks = iter([100.05, 100.9])
    r = Recorder(clock=lambda: next(ticks))
    r.open()
    r.start()
    fake_sd.stream.feed(block(0.1))
    assert r.first_block_at == pytest.approx(100.05)


def test_first_block_time_does_not_move_on_later_blocks(fake_sd):
    ticks = iter([100.05, 100.5, 101.0])
    r = Recorder(clock=lambda: next(ticks))
    r.open()
    r.start()
    fake_sd.stream.feed(block(0.1))
    fake_sd.stream.feed(block(0.1))
    assert r.first_block_at == pytest.approx(100.05)


def test_first_block_time_resets_for_the_next_dictation(fake_sd):
    r = Recorder()
    r.open()
    r.start()
    fake_sd.stream.feed(block(0.1))
    r.stop()
    r.start()
    assert r.first_block_at is None


# --- error handling ---------------------------------------------------------


def test_open_failing_raises_recorder_error(fake_sd):
    fake_sd.device_present = False
    fake_sd.reinit_recovers = False
    with pytest.raises(RecorderError):
        Recorder().open()


def test_start_opens_the_stream_if_it_is_somehow_not_open(fake_sd):
    """Belt and braces: a failed startup open must not mean no dictation ever."""
    r = Recorder()
    r.start()
    assert r.is_open is True
    assert r.is_recording is True


def test_level_is_zero_while_idle_even_though_the_stream_is_open(fake_sd):
    """The Flow Bar must rest flat between dictations, not react to the room."""
    r = Recorder()
    r.open()
    fake_sd.stream.feed(block(0.1, value=0.9))
    assert r.level == 0.0


def test_level_follows_the_audio_while_capturing(fake_sd):
    r = Recorder()
    r.open()
    r.start()
    fake_sd.stream.feed(block(0.1, value=0.5))
    assert r.level > 0.0
