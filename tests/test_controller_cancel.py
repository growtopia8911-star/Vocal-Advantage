"""Esc throws the recording away, whatever the hotkey is.

Gate 3 of `docs/plans/2026-08-25-interface-design.md`.

There *is* a cancel today, and it is narrower than it looks. `_handle_down`
bins the audio only when all three of these hold: the state is RECORDING, the
mode is PENDING (the hotkey is physically down), and the hotkey contains a bare
modifier. That is the "Right Ctrl+C should still just copy" rule from SPEC.md,
and it is right for what it covers.

What it leaves uncovered is everything else:

* a dead-key hotkey such as ``f8`` has no modifier, so the rule is off by
  design -- typing while dictating is allowed, and nothing cancels;
* in toggle mode the hands are free and typing is normal, so cancelling there
  was deliberately excluded.

In both, a recording you have changed your mind about can only be finished, not
abandoned. Esc closes that: one key, always, no conditions.

Rig is self-contained and mirrors `test_controller_modes.py`, which says the
same thing about itself -- the fakes are cheap and a shared rig would couple
two files that cover different rules.
"""

from __future__ import annotations

import numpy as np
import pytest

from vocal_advantage.controller import DictationController, State
from vocal_advantage.hotkey_spec import parse_hotkey

RATE = 16000
ESC = "esc"


def loud(seconds: float) -> np.ndarray:
    n = int(seconds * RATE)
    t = np.arange(n, dtype=np.float32) / RATE
    return (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRecorder:
    def __init__(self) -> None:
        self.buffer = np.empty(0, dtype=np.float32)
        self.is_recording = False
        self.stops = 0

    def start(self) -> None:
        self.is_recording = True
        self.buffer = np.empty(0, dtype=np.float32)

    def stop(self):
        self.is_recording = False
        self.stops += 1
        return self.buffer

    def snapshot(self):
        return self.buffer

    def speak(self, seconds: float) -> None:
        self.buffer = np.concatenate([self.buffer, loud(seconds)])


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def transcribe(self, audio) -> str:
        self.calls.append(int(np.asarray(audio).size))
        return "words that should never appear"


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


def build(hotkey: str = "right ctrl", **kwargs):
    clock = FakeClock()
    recorder = FakeRecorder()
    transcriber = FakeTranscriber()
    paster = FakePaster()
    indicator = FakeIndicator()
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
        hotkey=parse_hotkey(hotkey),
        recorder=recorder,
        transcriber=transcriber,
        paster=paster,
        indicator=indicator,
        clock=clock,
        on_timings=lambda _report: None,
        **options,
    )
    parts = dict(
        clock=clock, recorder=recorder, transcriber=transcriber,
        paster=paster, indicator=indicator,
    )
    return ctl, parts


def record(ctl, parts, hotkey: str, seconds: float = 1.0, toggle: bool = False):
    """Get into RECORDING, by hold or by tap, with `seconds` of speech in."""
    ctl.on_key_event(hotkey, True)
    if toggle:
        parts["clock"].advance(0.05)      # under tap_threshold_s
        ctl.on_key_event(hotkey, False)
    parts["clock"].advance(seconds)
    parts["recorder"].speak(seconds)
    assert ctl.state is State.RECORDING
    return ctl


# --- 3a: unconditionally ----------------------------------------------------

def test_esc_cancels_a_held_recording():
    ctl, parts = build("right ctrl")
    record(ctl, parts, "right ctrl")

    ctl.on_key_event(ESC, True)

    assert ctl.state is State.IDLE


def test_esc_cancels_a_toggled_recording():
    """No cancel exists here today: the hands are free, so the
    cancel-on-other-key rule is deliberately off."""
    ctl, parts = build("right ctrl")
    record(ctl, parts, "right ctrl", toggle=True)

    ctl.on_key_event(ESC, True)

    assert ctl.state is State.IDLE


# --- 3b: even when the hotkey has no modifier ------------------------------

@pytest.mark.parametrize("toggle", [False, True])
def test_esc_cancels_with_a_dead_key_hotkey(toggle):
    """`f8` has no modifier, so cancel-on-other-key is off by design and a
    recording could previously only be finished, never abandoned."""
    ctl, parts = build("f8")
    record(ctl, parts, "f8", toggle=toggle)

    ctl.on_key_event(ESC, True)

    assert ctl.state is State.IDLE


# --- 3c: the audio is binned, not processed --------------------------------

def test_a_cancelled_recording_is_never_transcribed():
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.on_key_event(ESC, True)

    # The state assertion is load-bearing: without it this passes while the
    # recording is merely still running, which is what it looked like before
    # esc did anything at all.
    assert ctl.state is State.IDLE
    assert parts["transcriber"].calls == []


def test_a_cancelled_recording_is_never_pasted():
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.on_key_event(ESC, True)

    assert ctl.state is State.IDLE
    assert parts["paster"].pasted == []


def test_a_cancel_stops_the_microphone_capture():
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.on_key_event(ESC, True)

    assert parts["recorder"].is_recording is False


def test_the_bar_goes_quiet_after_a_cancel():
    """Quiet, not a message: you meant to do it, so there is nothing to report."""
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.on_key_event(ESC, True)

    assert parts["indicator"].events[-1] == "hide"


# --- it stays out of the way otherwise -------------------------------------

def test_esc_while_idle_does_nothing():
    ctl, parts = build("f8")

    ctl.on_key_event(ESC, True)

    assert ctl.state is State.IDLE
    assert parts["recorder"].stops == 0


def test_releasing_esc_does_not_start_anything():
    ctl, parts = build("f8")

    ctl.on_key_event(ESC, True)
    ctl.on_key_event(ESC, False)

    assert ctl.state is State.IDLE


def test_esc_is_not_stolen_when_it_is_the_hotkey():
    """Someone whose hotkey is Esc must still be able to dictate with it."""
    ctl, parts = build(ESC)

    ctl.on_key_event(ESC, True)

    assert ctl.state is State.RECORDING


# --- Task 8: the Flow Bar's Stop/Cancel controls -----------------------
#
# `request_stop`/`request_cancel` are the click channel's other end. Both
# are called from the UI thread (a mouse click on the strip); the controller
# is already driven from the hotkey thread and the tick thread, so a request
# is *recorded* rather than performed, and `tick` -- the pump every other
# transition already goes through -- is what actually does it. These tests
# use the same `build`/`record` rig as gate 3 above.


def test_request_cancel_discards_the_recording():
    """Gate 4c. A clicked Cancel must do exactly what Esc does."""
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.request_cancel()
    ctl.tick()

    assert ctl.state is State.IDLE
    assert parts["recorder"].is_recording is False
    assert parts["transcriber"].calls == []


def test_request_stop_transcribes_rather_than_discarding():
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.request_stop()
    ctl.tick()

    assert ctl.state is State.IDLE
    assert parts["transcriber"].calls != []


def test_a_request_is_performed_by_tick_not_by_the_caller():
    """The controller is already driven from the hotkey thread and the tick
    thread. A click arrives on a third -- the UI thread -- so the request is
    recorded and the existing pump performs it, rather than three threads
    mutating the state machine directly."""
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.request_cancel()
    assert ctl.state is State.RECORDING

    ctl.tick()
    assert ctl.state is State.IDLE


def test_a_request_while_idle_is_ignored():
    """Clicking Stop on a bar that is not recording must be harmless. The
    strip hides its controls outside RECORDING, but a click can still land in
    the frame between the state changing and the redraw."""
    ctl, parts = build("f8")

    ctl.request_stop()
    ctl.request_cancel()
    ctl.tick()

    assert ctl.state is State.IDLE


def test_only_the_latest_request_is_kept():
    """Two clicks in one frame are one action, not two: Cancel arriving after
    Stop must win outright, not queue behind it."""
    ctl, parts = build("f8")
    record(ctl, parts, "f8")

    ctl.request_stop()
    ctl.request_cancel()
    ctl.tick()

    assert ctl.state is State.IDLE
    assert parts["transcriber"].calls == []
