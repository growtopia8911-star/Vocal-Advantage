"""Tests for the dictation state machine (vocal_advantage/controller.py).

Every collaborator is faked here, in this file, on purpose: a reader should be
able to see the whole rig without opening conftest.py.  Time is faked too, so
the suite runs at full speed and SPEC.md's 300s watchdog is testable without
waiting 300 seconds.
"""

from __future__ import annotations

import numpy as np
import pytest

from vocal_advantage.controller import (
    DEBOUNCE_S,
    ERROR_MESSAGE,
    NOTHING_HEARD_MESSAGE,
    PASTE_FAILED_MESSAGE,
    DictationController,
    State,
)
from vocal_advantage.hotkey_spec import parse_hotkey
from vocal_advantage.recorder import RecorderError

# ---------------------------------------------------------------------------
# Fakes.  Every call lands in one shared `log` list, so a test can assert the
# exact order of calls *across* all four collaborators, not just within one.
# ---------------------------------------------------------------------------


class FakeClock:
    """Stand-in for time.monotonic: seconds as a float, advanced by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRecorder:
    """Stands in for recorder.Recorder: start(), stop() -> audio.

    `start_error` makes start() raise the way the real Recorder does when the
    microphone cannot be opened, and `on_start` is a hook that runs *during*
    start() -- that is how "the state is not RECORDING until the mic is
    actually open" is observed from the inside.
    """

    def __init__(self, log: list[str], audio, start_error=None, on_start=None) -> None:
        self.log = log
        self.audio = audio
        self.is_recording = False
        self.start_error = start_error
        self.on_start = on_start

    def start(self) -> None:
        self.log.append("recorder.start")
        if self.on_start is not None:
            self.on_start()
        if self.start_error is not None:
            # The real Recorder leaves nothing half-open on this path.
            raise self.start_error
        self.is_recording = True

    def stop(self):
        self.log.append("recorder.stop")
        self.is_recording = False
        return self.audio


class FakeTranscriber:
    """Stands in for transcriber.Transcriber.

    `text` is what it returns, `error` (if set) is raised instead, and
    `on_call` is a hook that runs *while the controller is in PROCESSING* --
    that is how the "events during PROCESSING are ignored" rule is exercised.
    """

    def __init__(self, log: list[str], text: str = "", error=None, on_call=None) -> None:
        self.log = log
        self.text = text
        self.error = error
        self.on_call = on_call
        self.received: list = []

    def transcribe(self, audio) -> str:
        self.log.append("transcriber.transcribe")
        self.received.append(audio)
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return self.text


class FakePaster:
    """Stands in for paste_win.paste_text: returns True when the paste landed."""

    def __init__(self, log: list[str], ok: bool = True) -> None:
        self.log = log
        self.ok = ok

    def paste_text(self, text: str) -> bool:
        self.log.append(f"paster.paste_text:{text}")
        return self.ok


class FakeIndicator:
    """Stands in for indicator_win.Indicator (the pill overlay)."""

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def show_recording(self) -> None:
        self.log.append("indicator.show_recording")

    def show_processing(self) -> None:
        self.log.append("indicator.show_processing")

    def hide(self) -> None:
        self.log.append("indicator.hide")

    def flash(self, message: str) -> None:
        self.log.append(f"indicator.flash:{message}")


class Rig:
    """A controller wired to the fakes above, plus a tiny script runner."""

    def __init__(
        self,
        hotkey_text: str,
        *,
        transcript: str = "hello world",
        paste_ok: bool = True,
        error: BaseException | None = None,
        start_error: BaseException | None = None,
        min_duration_s: float = 0.4,
        max_duration_s: float = 300.0,
    ) -> None:
        self.log: list[str] = []
        self.clock = FakeClock()
        # 0.5s of silence at 16kHz.  The controller never looks inside it; it
        # only hands it from the recorder to the transcriber.
        self.audio = np.zeros(8000, dtype=np.float32)
        self.recorder = FakeRecorder(self.log, self.audio, start_error=start_error)
        self.transcriber = FakeTranscriber(self.log, text=transcript, error=error)
        self.paster = FakePaster(self.log, ok=paste_ok)
        self.indicator = FakeIndicator(self.log)
        self.controller = DictationController(
            hotkey=parse_hotkey(hotkey_text),
            recorder=self.recorder,
            transcriber=self.transcriber,
            paster=self.paster,
            indicator=self.indicator,
            min_duration_s=min_duration_s,
            max_duration_s=max_duration_s,
            clock=self.clock,
        )

    def drive(self, script) -> None:
        """Replay a script of ("down", key) / ("up", key) / ("wait", secs) / ("tick",)."""
        for step in script:
            action = step[0]
            if action == "wait":
                self.clock.advance(step[1])
            elif action == "down":
                self.controller.on_key_event(step[1], True)
            elif action == "up":
                self.controller.on_key_event(step[1], False)
            elif action == "tick":
                self.controller.tick()
            else:
                raise AssertionError(f"unknown script step: {step!r}")


# Shorthands so the expectation tables below stay readable.
STARTED = ["recorder.start", "indicator.show_recording"]
PASTED = [
    "recorder.stop",
    "indicator.show_processing",
    "transcriber.transcribe",
    "paster.paste_text:hello world",
    "indicator.hide",
]
DROPPED = ["recorder.stop", "indicator.hide"]


def test_hotkey_fixtures_use_the_key_names_this_module_sends():
    """Guard: if hotkey_spec ever normalises differently, fail here, not below."""
    assert parse_hotkey("right ctrl").keys == frozenset({"right ctrl"})
    assert parse_hotkey("right ctrl").is_modifier_only is True
    assert parse_hotkey("f8").keys == frozenset({"f8"})
    assert parse_hotkey("f8").is_modifier_only is False
    assert parse_hotkey("ctrl+win").keys == frozenset({"ctrl", "windows"})
    assert parse_hotkey("ctrl+win").is_modifier_only is True


def test_constants_match_the_spec():
    # SPEC.md: "Key-downs within 30ms of the last ... are ignored."
    assert DEBOUNCE_S == pytest.approx(0.030)
    # SPEC.md: "Empty result -> no paste, pill flashes 'nothing heard'."
    assert NOTHING_HEARD_MESSAGE == "nothing heard"
    assert ERROR_MESSAGE == "error"
    assert PASTE_FAILED_MESSAGE == "could not paste - press Ctrl+V"


@pytest.mark.parametrize(
    ("hotkey_text", "script", "expected_log", "expected_state"),
    [
        pytest.param(
            "right ctrl",
            [("down", "right ctrl"), ("wait", 1.2), ("up", "right ctrl")],
            STARTED + PASTED,
            State.IDLE,
            id="hold-then-release-records-and-pastes",
        ),
        pytest.param(
            "right ctrl",
            [("down", "Right Ctrl"), ("wait", 1.2), ("up", "RIGHT CTRL")],
            STARTED + PASTED,
            State.IDLE,
            id="key-names-are-case-insensitive",
        ),
        pytest.param(
            # OS autorepeat fires key-downs while the key is held; they must not
            # restart or double-start the recording.
            "right ctrl",
            [
                ("down", "right ctrl"),
                ("wait", 0.5),
                ("down", "right ctrl"),
                ("wait", 0.5),
                ("down", "right ctrl"),
                ("wait", 0.2),
                ("up", "right ctrl"),
            ],
            STARTED + PASTED,
            State.IDLE,
            id="autorepeat-downs-are-ignored",
        ),
        pytest.param(
            "ctrl+win",
            [
                ("down", "ctrl"),
                ("wait", 0.01),
                ("down", "windows"),
                ("wait", 1.2),
                ("up", "windows"),
                ("wait", 0.1),
                ("up", "ctrl"),
            ],
            STARTED + PASTED,
            State.IDLE,
            id="combo-starts-only-when-the-last-key-goes-down",
        ),
        pytest.param(
            "ctrl+win",
            [("down", "ctrl"), ("wait", 0.01), ("down", "windows"), ("wait", 1.2), ("up", "ctrl")],
            STARTED + PASTED,
            State.IDLE,
            id="combo-ends-when-either-key-is-released",
        ),
        pytest.param(
            "ctrl+win",
            [("down", "ctrl"), ("wait", 1.2), ("up", "ctrl")],
            [],
            State.IDLE,
            id="combo-half-held-never-records",
        ),
        pytest.param(
            # SPEC.md: with a bare-modifier hotkey the user was typing
            # Right Ctrl+C, not dictating -- so cancel, and paste nothing.
            "right ctrl",
            [
                ("down", "right ctrl"),
                ("wait", 1.0),
                ("down", "c"),
                ("wait", 0.1),
                ("up", "c"),
                ("up", "right ctrl"),
            ],
            STARTED + DROPPED,
            State.IDLE,
            id="modifier-only-hotkey-cancels-on-another-key",
        ),
        pytest.param(
            # SPEC.md: "With a dead key (F8) the rule is off, so typing while
            # dictating is allowed."
            "f8",
            [
                ("down", "f8"),
                ("wait", 1.0),
                ("down", "c"),
                ("wait", 0.1),
                ("up", "c"),
                ("wait", 0.2),
                ("up", "f8"),
            ],
            STARTED + PASTED,
            State.IDLE,
            id="f8-hotkey-does-not-cancel-on-another-key",
        ),
        pytest.param(
            # SPEC.md: shorter than min_duration_s (0.4) is "discarded
            # silently" -- no transcribe, no paste, no flash.
            "right ctrl",
            [("down", "right ctrl"), ("wait", 0.3), ("up", "right ctrl")],
            STARTED + DROPPED,
            State.IDLE,
            id="hold-shorter-than-min-duration-is-discarded-silently",
        ),
        pytest.param(
            # A second down 20ms after the accepted one is contact bounce and
            # must not start anything; 45ms later is a real press.
            "f8",
            [
                ("down", "f8"),
                ("wait", 0.01),
                ("up", "f8"),
                ("wait", 0.01),
                ("down", "f8"),
                ("wait", 0.025),
                ("down", "f8"),
            ],
            STARTED + DROPPED + STARTED,
            State.RECORDING,
            id="repeat-down-within-30ms-is-debounced",
        ),
        pytest.param(
            # The debounce window is per key: a human presses the two halves of
            # a combo ~10ms apart, and a global window would swallow the second.
            "ctrl+win",
            [("down", "ctrl"), ("wait", 0.005), ("down", "ctrl"), ("wait", 0.005), ("down", "windows")],
            STARTED,
            State.RECORDING,
            id="debounce-is-per-key-so-combos-still-fire",
        ),
        pytest.param(
            # SPEC.md: "A 300s watchdog force-stops a forgotten recording and
            # processes it."  tick() at 299s does nothing; at 300.5s it fires.
            "f8",
            [("down", "f8"), ("wait", 299.0), ("tick",), ("wait", 1.5), ("tick",), ("tick",)],
            STARTED + PASTED,
            State.IDLE,
            id="watchdog-force-stops-and-processes-at-max-duration",
        ),
        pytest.param(
            "right ctrl",
            [("up", "right ctrl"), ("tick",), ("up", "c"), ("down", "c")],
            [],
            State.IDLE,
            id="stray-events-while-idle-do-nothing",
        ),
    ],
)
def test_event_sequences(hotkey_text, script, expected_log, expected_state):
    rig = Rig(hotkey_text)
    rig.drive(script)
    assert rig.log == expected_log
    assert rig.controller.state is expected_state


def test_state_is_recording_while_the_key_is_held():
    rig = Rig("right ctrl")
    assert rig.controller.state is State.IDLE
    rig.drive([("down", "right ctrl")])
    assert rig.controller.state is State.RECORDING
    assert rig.recorder.is_recording is True


def test_the_recorders_audio_reaches_the_transcriber_untouched():
    rig = Rig("f8")
    rig.drive([("down", "f8"), ("wait", 1.0), ("up", "f8")])
    assert rig.transcriber.received == [rig.recorder.audio]


def test_empty_transcript_flashes_and_never_pastes():
    # SPEC.md hallucination guard 4: empty result -> no paste, pill flashes.
    rig = Rig("right ctrl", transcript="")
    rig.drive([("down", "right ctrl"), ("wait", 1.0), ("up", "right ctrl")])
    assert rig.log == STARTED + [
        "recorder.stop",
        "indicator.show_processing",
        "transcriber.transcribe",
        f"indicator.flash:{NOTHING_HEARD_MESSAGE}",
    ]
    assert rig.controller.state is State.IDLE


def test_failed_paste_flashes_so_the_user_knows_to_press_ctrl_v():
    # SPEC.md: a non-elevated process cannot paste into an elevated window;
    # the text is still on the clipboard, so tell the user.
    rig = Rig("right ctrl", paste_ok=False)
    rig.drive([("down", "right ctrl"), ("wait", 1.0), ("up", "right ctrl")])
    assert rig.log == STARTED + [
        "recorder.stop",
        "indicator.show_processing",
        "transcriber.transcribe",
        "paster.paste_text:hello world",
        f"indicator.flash:{PASTE_FAILED_MESSAGE}",
    ]
    assert rig.controller.state is State.IDLE


def test_transcriber_failure_flashes_and_leaves_the_machine_usable():
    rig = Rig("f8", error=RuntimeError("CUDA out of memory"))
    rig.drive([("down", "f8"), ("wait", 1.0), ("up", "f8")])
    assert rig.log == STARTED + [
        "recorder.stop",
        "indicator.show_processing",
        "transcriber.transcribe",
        f"indicator.flash:{ERROR_MESSAGE}",
    ]
    assert rig.controller.state is State.IDLE

    # Never stuck: the very next hold records again.
    rig.drive([("down", "f8"), ("wait", 1.0), ("up", "f8")])
    assert rig.log.count("recorder.start") == 2
    assert rig.controller.state is State.IDLE


def test_key_events_arriving_during_processing_are_ignored():
    # SPEC.md: "Key events during PROCESSING are ignored (no double-start, no
    # double-paste)."  The fake transcriber re-enters the controller mid-call,
    # which is the only moment the machine is really in PROCESSING.
    rig = Rig("right ctrl")
    seen_states: list[State] = []

    def while_transcribing() -> None:
        seen_states.append(rig.controller.state)
        rig.controller.on_key_event("right ctrl", True)
        rig.controller.on_key_event("right ctrl", False)

    rig.transcriber.on_call = while_transcribing
    rig.drive([("down", "right ctrl"), ("wait", 1.0), ("up", "right ctrl")])

    assert seen_states == [State.PROCESSING]
    assert rig.log == STARTED + PASTED  # exactly one start, exactly one paste
    assert rig.controller.state is State.IDLE


# ---------------------------------------------------------------------------
# A microphone that will not open (plan correction: Task 9, `_start` ordering)
#
# Task 5's contract: Recorder.start() raises RecorderError -- mic unplugged,
# Windows privacy settings, no sounddevice -- and leaves is_recording False
# with no stream leaked.  The controller must therefore not commit to
# RECORDING until start() has actually returned, or a failed open strands the
# machine in a state with no stream and no pill, and the next key-up produces
# an empty recording.
# ---------------------------------------------------------------------------


def test_the_state_is_not_recording_until_the_recorder_has_opened():
    """Observed from inside start(): the machine must still read IDLE."""
    rig = Rig("f8")
    seen_states: list[State] = []
    rig.recorder.on_start = lambda: seen_states.append(rig.controller.state)

    rig.drive([("down", "f8")])

    assert seen_states == [State.IDLE]
    assert rig.controller.state is State.RECORDING  # ... and RECORDING after


def test_a_microphone_that_will_not_open_leaves_the_controller_idle():
    rig = Rig("f8", start_error=RecorderError("Could not open the microphone."))

    # Must not propagate: this runs on the thread draining the hotkey queue,
    # and an escaping exception would kill dictation for the whole session.
    rig.drive([("down", "f8")])

    assert rig.controller.state is State.IDLE
    # No pill, because there is nothing being recorded to advertise.
    assert rig.log == ["recorder.start", f"indicator.flash:{ERROR_MESSAGE}"]


def test_releasing_the_key_after_a_failed_start_pastes_nothing():
    rig = Rig("f8", start_error=RecorderError("Could not open the microphone."))
    rig.drive([("down", "f8"), ("wait", 1.2), ("up", "f8")])

    assert rig.log == ["recorder.start", f"indicator.flash:{ERROR_MESSAGE}"]
    assert rig.controller.state is State.IDLE


def test_the_next_hold_after_a_failed_start_records_normally():
    # A mic that comes back (USB replugged) must not need an app restart.
    rig = Rig("f8", start_error=RecorderError("Could not open the microphone."))
    rig.drive([("down", "f8"), ("wait", 0.1), ("up", "f8")])
    rig.recorder.start_error = None
    rig.log.clear()

    rig.drive([("wait", 0.1), ("down", "f8"), ("wait", 1.2), ("up", "f8")])

    assert rig.log == STARTED + PASTED
    assert rig.controller.state is State.IDLE
