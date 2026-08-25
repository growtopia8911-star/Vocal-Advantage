"""Tap/hold hotkey modes, the two watchdogs, and the chunk pump.

Spec items 4, 5, 6, 7 and 9. Every collaborator is faked in this file and the
clock is injected, so the 300-second watchdog and the 300-millisecond tap
threshold are both testable in microseconds.

The rig is deliberately separate from test_controller.py: that file covers the
rules that did not change (debounce, PROCESSING gating, the minimum duration
guard), and this one covers the behaviour that is new.
"""

from __future__ import annotations

import numpy as np
import pytest

from vocal_advantage.controller import DictationController, State
from vocal_advantage.hotkey_spec import parse_hotkey

RATE = 16000
KEY = "right ctrl"


def loud(seconds: float) -> np.ndarray:
    """Audio the silence detector will call speech."""
    n = int(seconds * RATE)
    t = np.arange(n, dtype=np.float32) / RATE
    return (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def quiet(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRecorder:
    """Grows a buffer on demand, the way a real capture does."""

    def __init__(self) -> None:
        self.buffer = np.empty(0, dtype=np.float32)
        self.is_recording = False
        self.starts = 0
        self.first_block_at = None

    def start(self) -> None:
        self.starts += 1
        self.is_recording = True
        self.buffer = np.empty(0, dtype=np.float32)
        self.first_block_at = None

    def stop(self):
        self.is_recording = False
        return self.buffer

    def snapshot(self):
        return self.buffer

    def speak(self, seconds: float, silent: bool = False) -> None:
        block = quiet(seconds) if silent else loud(seconds)
        self.buffer = np.concatenate([self.buffer, block])
        if self.first_block_at is None:
            self.first_block_at = 1000.0


class FakeTranscriber:
    """Returns a numbered string per call, so stitching order is visible."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def transcribe(self, audio) -> str:
        self.calls.append(int(np.asarray(audio).size))
        return f"chunk{len(self.calls)}"


class FakePaster:
    def __init__(self) -> None:
        self.pasted: list[str] = []

    def paste_text(self, text: str) -> bool:
        self.pasted.append(text)
        return True


class FakeIndicator:
    def __init__(self) -> None:
        self.events: list[str] = []

    def show_recording(self) -> None:
        self.events.append("recording")

    def show_processing(self) -> None:
        self.events.append("processing")

    def hide(self) -> None:
        self.events.append("hide")

    def flash(self, message: str) -> None:
        self.events.append(f"flash:{message}")


def build(**kwargs):
    """A controller with every collaborator faked. Returns (ctl, parts)."""
    clock = kwargs.pop("clock", None) or FakeClock()
    recorder = kwargs.pop("recorder", None) or FakeRecorder()
    transcriber = kwargs.pop("transcriber", None) or FakeTranscriber()
    paster = FakePaster()
    indicator = FakeIndicator()
    reports: list = []
    options = {
        "min_duration_s": 0.0,
        "max_duration_s": 300.0,
        "tap_threshold_s": 0.3,
        "silence_timeout_s": 0.0,
        "chunk_s": 2.0,
        "overlap_s": 0.25,
    }
    options.update(kwargs)
    ctl = DictationController(
        hotkey=parse_hotkey(KEY),
        recorder=recorder,
        transcriber=transcriber,
        paster=paster,
        indicator=indicator,
        clock=clock,
        on_timings=reports.append,
        **options,
    )
    parts = dict(
        clock=clock, recorder=recorder, transcriber=transcriber,
        paster=paster, indicator=indicator, reports=reports,
    )
    return ctl, parts


def down(ctl):
    ctl.on_key_event(KEY, True)


def up(ctl):
    ctl.on_key_event(KEY, False)


# --- 4a/4b: tap to start, tap to stop ---------------------------------------


def test_a_quick_tap_starts_recording_and_keeps_it_going(ctl=None):
    ctl, p = build()
    down(ctl)
    p["clock"].advance(0.1)
    up(ctl)
    assert ctl.state is State.RECORDING


def test_a_second_quick_tap_stops_and_processes():
    """4b: press again and the dictation is delivered."""
    ctl, p = build()
    down(ctl)
    p["clock"].advance(0.1)
    up(ctl)
    p["recorder"].speak(1.0)
    p["clock"].advance(1.0)
    down(ctl)
    assert ctl.state is State.IDLE
    assert p["paster"].pasted, "nothing was pasted"


def test_the_release_after_a_toggle_stop_starts_nothing():
    """4e: the key-up belonging to the stopping tap must not re-arm."""
    ctl, p = build()
    down(ctl); p["clock"].advance(0.1); up(ctl)      # tap 1: start
    p["recorder"].speak(1.0)
    down(ctl)                                          # tap 2: stop
    up(ctl)
    assert ctl.state is State.IDLE
    assert p["recorder"].starts == 1


# --- 4c: hold to talk -------------------------------------------------------


def test_holding_past_the_threshold_stops_on_release():
    ctl, p = build()
    down(ctl)
    p["clock"].advance(0.5)
    p["recorder"].speak(1.0)
    up(ctl)
    assert ctl.state is State.IDLE
    assert p["paster"].pasted


def test_holding_does_not_stop_before_release():
    ctl, p = build()
    down(ctl)
    p["clock"].advance(2.0)
    ctl.tick()
    assert ctl.state is State.RECORDING


# --- 4d: the threshold is a setting -----------------------------------------


def test_a_longer_threshold_makes_a_slow_press_count_as_a_tap():
    ctl, p = build(tap_threshold_s=1.0)
    down(ctl)
    p["clock"].advance(0.5)   # would be a hold at the 0.3 default
    up(ctl)
    assert ctl.state is State.RECORDING


def test_a_shorter_threshold_makes_a_fast_press_count_as_a_hold():
    ctl, p = build(tap_threshold_s=0.05)
    down(ctl)
    p["clock"].advance(0.1)
    p["recorder"].speak(0.5)
    up(ctl)
    assert ctl.state is State.IDLE


# --- 5: instant capture and feedback ----------------------------------------


def test_capture_starts_on_the_key_down_edge():
    """5a: not on release, not after a threshold has elapsed."""
    ctl, p = build()
    down(ctl)
    assert p["recorder"].starts == 1
    assert ctl.state is State.RECORDING


def test_the_indicator_fires_on_the_key_down_edge():
    ctl, p = build()
    down(ctl)
    assert "recording" in p["indicator"].events


# --- 6a: the length watchdog ------------------------------------------------


def test_the_max_length_watchdog_stops_and_processes():
    ctl, p = build(max_duration_s=5.0)
    down(ctl); p["clock"].advance(0.1); up(ctl)   # toggle mode
    p["recorder"].speak(6.0)
    p["clock"].advance(6.0)
    ctl.tick()
    assert ctl.state is State.IDLE
    assert p["paster"].pasted, "6d: the audio must be processed, not binned"


# --- 6b/6c/6d: the silence watchdog -----------------------------------------


def test_trailing_silence_auto_stops_a_forgotten_toggle():
    ctl, p = build(silence_timeout_s=2.0)
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(1.0)
    p["recorder"].speak(3.0, silent=True)
    p["clock"].advance(4.0)
    ctl.tick()
    assert ctl.state is State.IDLE


def test_the_silence_watchdog_processes_rather_than_discards():
    """6d: what was said before the silence still gets pasted."""
    ctl, p = build(silence_timeout_s=2.0)
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(1.0)
    p["recorder"].speak(3.0, silent=True)
    p["clock"].advance(4.0)
    ctl.tick()
    assert p["paster"].pasted


def test_short_pauses_do_not_trigger_the_silence_watchdog():
    ctl, p = build(silence_timeout_s=2.0)
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(1.0)
    p["recorder"].speak(0.5, silent=True)
    p["clock"].advance(1.5)
    ctl.tick()
    assert ctl.state is State.RECORDING


def test_zero_disables_the_silence_watchdog():
    """6c: a toggle then stays open until told otherwise."""
    ctl, p = build(silence_timeout_s=0.0)
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(30.0, silent=True)
    p["clock"].advance(30.0)
    ctl.tick()
    assert ctl.state is State.RECORDING


def test_silence_before_any_speech_does_not_stop_the_recording():
    """Thinking for three seconds before speaking must not end the dictation."""
    ctl, p = build(silence_timeout_s=2.0)
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(3.0, silent=True)
    p["clock"].advance(3.0)
    ctl.tick()
    assert ctl.state is State.RECORDING


# --- 7d: partials are never displayed ---------------------------------------


def test_nothing_is_pasted_while_still_recording():
    ctl, p = build()
    down(ctl); p["clock"].advance(0.1); up(ctl)
    for _ in range(5):
        p["recorder"].speak(2.0)
        p["clock"].advance(2.0)
        ctl.tick()
    assert p["paster"].pasted == [], "7d: a partial reached the document"
    assert ctl.state is State.RECORDING


def test_chunks_are_transcribed_while_recording():
    """The work happens during the recording; that is the whole point."""
    ctl, p = build()
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(4.0)
    p["clock"].advance(4.0)
    ctl.tick()
    assert len(p["transcriber"].calls) == 2


# --- 9a/9b: stop processes only the tail, then stitches ----------------------


def test_on_stop_only_the_remainder_is_transcribed():
    ctl, p = build()
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(4.0)
    p["clock"].advance(4.0)
    ctl.tick()                       # two chunks transcribed
    before = len(p["transcriber"].calls)
    p["recorder"].speak(1.0)
    down(ctl)                        # stop
    assert len(p["transcriber"].calls) == before + 1


def test_the_final_text_is_every_chunk_stitched():
    ctl, p = build()
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(4.0)
    p["clock"].advance(4.0)
    ctl.tick()
    p["recorder"].speak(1.0)
    down(ctl)
    pasted = p["paster"].pasted[0]
    assert "chunk1" in pasted and "chunk2" in pasted and "chunk3" in pasted


def test_a_recording_shorter_than_one_chunk_still_works():
    """Nothing was chunked, so the remainder is the whole utterance."""
    ctl, p = build()
    down(ctl)
    p["clock"].advance(0.5)
    p["recorder"].speak(0.5)
    up(ctl)
    assert p["paster"].pasted == ["chunk1"]


# --- 8b: an all-silent chunk never reaches the model ------------------------


def test_a_silent_chunk_is_not_sent_to_the_model():
    ctl, p = build()
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(4.0, silent=True)
    p["clock"].advance(4.0)
    ctl.tick()
    assert p["transcriber"].calls == []


# --- 9c: the cleanup pass runs once, on the whole ----------------------------


def test_cleanup_runs_once_on_the_stitched_text():
    seen: list[str] = []

    def clean(text: str) -> str:
        seen.append(text)
        return text.upper()

    ctl, p = build(clean=clean)
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(4.0)
    p["clock"].advance(4.0)
    ctl.tick()
    p["recorder"].speak(1.0)
    down(ctl)
    assert len(seen) == 1, "cleanup ran per chunk instead of once"
    assert p["paster"].pasted[0].isupper()


# --- 11: the timing report --------------------------------------------------


def test_a_timing_report_is_produced_for_every_dictation():
    ctl, p = build()
    down(ctl); p["clock"].advance(0.5); p["recorder"].speak(0.5); up(ctl)
    assert len(p["reports"]) == 1


def test_a_report_is_produced_even_when_nothing_was_heard():
    """11f: including the dictation that produced no text at all."""

    class Silent:
        def transcribe(self, audio) -> str:
            return ""

    ctl, p = build(transcriber=Silent())
    down(ctl); p["clock"].advance(0.5); p["recorder"].speak(0.5); up(ctl)
    assert len(p["reports"]) == 1


def test_the_report_carries_a_number_for_each_stage():
    ctl, p = build()
    down(ctl); p["clock"].advance(0.1); up(ctl)
    p["recorder"].speak(4.0)
    p["clock"].advance(4.0)
    ctl.tick()
    p["recorder"].speak(1.0)
    down(ctl)
    t = p["reports"][0]
    assert len(t.chunk_ms) == 2, "11b: per-chunk numbers missing"
    assert t.final_chunk_ms is not None, "11c"
    assert t.cleanup_ms is not None, "11d"
    assert t.insertion_ms is not None, "11e"


def test_keypress_to_first_audio_is_reported():
    ctl, p = build()
    down(ctl)
    p["clock"].advance(0.2)
    p["recorder"].speak(0.5)
    p["clock"].advance(0.5)
    up(ctl)
    assert p["reports"][0].first_audio_ms is not None


# --- typing while toggled on ------------------------------------------------


def test_typing_another_key_does_not_cancel_a_toggled_recording():
    """A bare-modifier hotkey cancels on another key -- but only while held.

    In toggle mode the user is not holding anything, so typing is a normal
    thing to do while dictating and must not silently bin the recording.
    """
    ctl, p = build()
    ctl.set_hotkey(parse_hotkey("right alt"))
    ctl.on_key_event("right alt", True)
    p["clock"].advance(0.1)
    ctl.on_key_event("right alt", False)
    ctl.on_key_event("a", True)
    assert ctl.state is State.RECORDING


def test_typing_another_key_still_cancels_while_the_hotkey_is_held():
    ctl, p = build()
    ctl.set_hotkey(parse_hotkey("right alt"))
    ctl.on_key_event("right alt", True)
    ctl.on_key_event("a", True)
    assert ctl.state is State.IDLE
