"""The dictation state machine, and the chunk pump that runs inside it.

    IDLE --key down--> RECORDING --stop--> PROCESSING --paste done--> IDLE

**One key, two modes.** The same key both toggles and holds, split by how long
it was down:

    press ............ recording starts immediately, mode undecided
    release < 300ms .. it was a tap: keep recording, stop on the next tap
    release >= 300ms . it was a hold: stop now

The threshold is a setting because 300ms is a claim about a person's hands, not
a fact about the software. Nothing waits for it to elapse -- the decision is
made at the release, by looking at how long the key was down -- so a tap starts
recording just as fast as a hold does.

**Work happens while you speak.** While RECORDING, ``tick`` pumps a chunker:
each ~2s window of audio is trimmed of silence and transcribed as it becomes
available, and its text is kept internally. On stop only the un-chunked tail is
left to transcribe, so the wait after the key release is one short pass rather
than one pass over the whole utterance. The partial texts are never shown --
they are notes toward the final answer, and a word that appears and then
changes is worse than a word that appears half a second later.

Pure Python: no OS calls, no threads, no sleeps. Every collaborator is injected
and time arrives through ``clock``, so the 300-second watchdog and the
300-millisecond threshold are both testable in microseconds.

Threading contract: ``on_key_event`` and ``tick`` are called from ONE thread
(the controller thread that drains the hotkey queue). Nothing here is
thread-safe, and nothing here needs to be. The exception is the audio the
recorder is appending on PortAudio's thread, which is why ``snapshot`` exists.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from enum import Enum, auto
from typing import Any, Protocol

import numpy as np

from .chunker import RollingChunker
from .console import say
from .hotkey_spec import HotkeySpec
from .stitch import stitch_all
from .timings import Timings
from .vad import is_silent, trailing_silence_s, trim_silence

# SPEC.md: "Key-downs within 30ms of the last ... are ignored."  Applied per
# key: a single global window would swallow the second half of a combo, which a
# human presses ~10ms after the first.
DEBOUNCE_S = 0.030

#: Below this a release means "tap"; at or above it, "hold". See the module
#: docstring. Overridden by config.json's ``tap_threshold_s``.
TAP_THRESHOLD_S = 0.3

# SPEC.md: "Empty result -> no paste, pill flashes 'nothing heard'."
NOTHING_HEARD_MESSAGE = "nothing heard"
ERROR_MESSAGE = "error"
# SPEC.md's known-Windows-facts: a non-elevated process cannot paste into an
# elevated window, but the text is on the clipboard, so say so.
PASTE_FAILED_MESSAGE = "could not paste - press Ctrl+V"

#: The one key that always throws a recording away.
#:
#: Cancel-on-other-key (below, in `_handle_down`) is narrower than it looks: it
#: needs a bare-modifier hotkey AND the hotkey physically held, both for good
#: reasons. The gap that leaves is total -- with a dead key like `f8`, or in
#: toggle mode with any hotkey, a recording you have changed your mind about
#: can only be finished, never abandoned. This closes it with one key and no
#: conditions, which is also the only version worth putting on the Flow Bar:
#: a legend that has to explain when it applies is not a legend.
CANCEL_KEY = "esc"


class State(Enum):
    IDLE = auto()
    RECORDING = auto()
    PROCESSING = auto()


class Mode(Enum):
    """How the current recording will end."""

    NONE = auto()     # not recording
    PENDING = auto()  # key still down; tap-or-hold not yet decided
    TOGGLE = auto()   # it was a tap; the next press stops it


class RecorderLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> Any: ...
    def snapshot(self) -> Any: ...


class TranscriberLike(Protocol):
    def transcribe(self, audio: Any) -> str: ...


class PasterLike(Protocol):
    def paste_text(self, text: str) -> bool: ...


class IndicatorLike(Protocol):
    def show_recording(self) -> None: ...
    def show_processing(self) -> None: ...
    def hide(self) -> None: ...
    def flash(self, message: str) -> None: ...


def _identity(text: str) -> str:
    return text


class DictationController:
    """Turns key events into record / transcribe / clean / paste."""

    def __init__(
        self,
        *,
        hotkey: HotkeySpec,
        recorder: RecorderLike,
        transcriber: TranscriberLike,
        paster: PasterLike,
        indicator: IndicatorLike,
        min_duration_s: float,
        max_duration_s: float,
        tap_threshold_s: float = TAP_THRESHOLD_S,
        silence_timeout_s: float = 0.0,
        chunk_s: float = 2.0,
        overlap_s: float = 0.25,
        clean: Callable[[str], str] = _identity,
        clock: Callable[[], float] = time.monotonic,
        on_timings: Callable[[Timings], None] | None = None,
    ) -> None:
        self._hotkey = hotkey
        self._recorder = recorder
        self._transcriber = transcriber
        self._paster = paster
        self._indicator = indicator
        self._min_duration_s = float(min_duration_s)
        self._max_duration_s = float(max_duration_s)
        self._tap_threshold_s = float(tap_threshold_s)
        self._silence_timeout_s = float(silence_timeout_s)
        self._clean = clean
        self._clock = clock
        self._on_timings = on_timings if on_timings is not None else _print_timings

        self._chunker = RollingChunker(chunk_s=chunk_s, overlap_s=overlap_s)
        self._chunk_texts: list[str] = []
        self._heard_speech = False

        self.state: State = State.IDLE
        self.mode: Mode = Mode.NONE
        # Which of the hotkey's own keys are currently down.  Only hotkey keys
        # are tracked; we do not care what else the keyboard is doing.
        self._held: set[str] = set()
        # Per-key timestamp of the last *accepted* down, for the debounce.
        self._last_down: dict[str, float] = {}
        self._started_at = 0.0
        self._down_at = 0.0
        self._timings = Timings(clock=self._clock)

        #: A control clicked on the Flow Bar, waiting for the next tick.
        #: Written from the UI thread, read and cleared in `tick`.
        self._requested: str | None = None

    # -- public API ---------------------------------------------------------

    def on_key_event(self, key_name: str, is_down: bool) -> None:
        """Feed one key event in.  Called from one thread only."""
        # SPEC.md: "Key events during PROCESSING are ignored (no double-start,
        # no double-paste)."
        if self.state is State.PROCESSING:
            return
        key = key_name.strip().lower()
        if is_down:
            self._handle_down(key)
        else:
            self._handle_up(key)

    def request_stop(self) -> None:
        """Ask for the recording to be stopped and transcribed.

        Recorded rather than performed. This is called from the UI thread when
        the Flow Bar's Stop is clicked, and this object is already driven from
        the hotkey thread and the tick thread -- performing it here would put a
        third thread inside the state machine. `tick` does the work, which is
        the pump every other transition already goes through.
        """
        self._requested = "stop"

    def request_cancel(self) -> None:
        """Ask for the recording to be discarded. See `request_stop`."""
        self._requested = "cancel"

    def set_hotkey(self, hotkey: HotkeySpec) -> None:
        """Swap the hotkey without restarting the app.

        Called while the keyboard hook is **stopped**, from whichever thread is
        running the capture -- which is what makes it safe in a class that is
        otherwise explicitly not thread-safe. No key events can be in flight,
        so nothing else is touching this state.

        A recording in progress is cancelled rather than carried over. The keys
        that started it no longer mean anything, so there would be no event
        that could ever end it, and the microphone would stay open forever.
        """
        if self.state is State.RECORDING:
            self._cancel()
        self._hotkey = hotkey
        # Both cleared, or the new hotkey inherits the old one's physical
        # state: a key still held from before would count toward the new combo
        # and could start a recording nobody asked for.
        self._held = set()
        self._last_down = {}

    def tick(self) -> None:
        """Watchdogs and the chunk pump. Cheap and safe in any state.

        Callers set a tick interval short enough that chunks are picked up
        promptly -- 0.1s in the real app. An un-earned tick costs one snapshot
        and a comparison.
        """
        requested, self._requested = self._requested, None
        if requested is not None and self.state is State.RECORDING:
            # Ignored outside RECORDING on purpose: the strip hides its
            # controls in every other state, but a click can still land in the
            # frame between the state changing and the redraw, and a stray
            # click must never bin a transcription in flight.
            if requested == "cancel":
                self._cancel()
            else:
                self._stop_and_process()

        if self.state is not State.RECORDING:
            return

        self._pump_chunks()

        # SPEC.md: a 300s watchdog force-stops a forgotten recording *and
        # processes it* -- the audio is not thrown away.
        if self._clock() - self._started_at >= self._max_duration_s:
            self._stop_and_process()
            return

        if self._silence_exhausted():
            self._stop_and_process()

    # -- key handling -------------------------------------------------------

    def _handle_down(self, key: str) -> None:
        if key not in self._hotkey.keys:
            # Esc first, and unconditionally. Reached only when esc is not part
            # of the hotkey, so someone who dictates *with* Esc keeps it.
            if key == CANCEL_KEY and self.state is State.RECORDING:
                self._cancel()
                return

            # SPEC.md: cancel-on-other-key applies only to a bare-modifier
            # hotkey -- with Right Ctrl the user was typing Right Ctrl+C, not
            # dictating.
            #
            # And only while the hotkey is physically held. In toggle mode the
            # user's hands are free and typing mid-dictation is a normal thing
            # to do; cancelling there would bin a recording for no reason the
            # user could see.
            if (
                self.state is State.RECORDING
                and self.mode is Mode.PENDING
                and self._hotkey.is_modifier_only
            ):
                self._cancel()
            return

        if self.state is State.RECORDING:
            if self.mode is Mode.TOGGLE:
                # 4b: the second tap. Stop on the press rather than its release,
                # so the app reacts the instant the key goes down.
                self._stop_and_process()
            return  # otherwise: OS autorepeat while the hotkey is held

        now = self._clock()
        previous = self._last_down.get(key)
        if previous is not None and now - previous < DEBOUNCE_S:
            return  # contact bounce
        self._last_down[key] = now

        # Start only on the transition from "not all held" to "all held".  This
        # is what stops a recording restarting instantly after a cancel, while
        # the user is still holding the modifier and autorepeat keeps firing.
        was_complete = self._hotkey.keys <= self._held
        self._held.add(key)
        if not was_complete and self._hotkey.keys <= self._held:
            self._start(now)

    def _handle_up(self, key: str) -> None:
        if key not in self._hotkey.keys:
            return
        self._held.discard(key)
        if self.state is not State.RECORDING:
            return
        if self.mode is not Mode.PENDING:
            # 4e: the release belonging to a tap that already armed the toggle,
            # or to the tap that stopped one. Neither means anything.
            return
        if self._clock() - self._down_at < self._tap_threshold_s:
            self.mode = Mode.TOGGLE  # a tap: keep going until told to stop
            return
        self._stop_and_process()

    # -- transitions --------------------------------------------------------

    def _start(self, now: float) -> None:
        """Open capture, then -- and only then -- commit to RECORDING.

        The order matters. Recorder.start() raises when the microphone cannot
        be reached and leaves nothing half-open. Setting the state first would
        strand the machine in RECORDING with no stream and no pill, and the
        next key-up would "process" an empty recording.

        The error is swallowed on purpose: this runs on the thread draining the
        hotkey queue, so letting it escape would end dictation for the whole
        session over one unplugged microphone. The flash is what tells the
        user.
        """
        self._timings = Timings(clock=self._clock)
        self._timings.start()
        try:
            self._recorder.start()
        except Exception:  # noqa: BLE001 - collaborators are duck-typed here
            self._indicator.flash(ERROR_MESSAGE)
            return
        self._started_at = now
        self._down_at = now
        self.state = State.RECORDING
        self.mode = Mode.PENDING
        self._chunker.reset()
        self._chunk_texts = []
        self._heard_speech = False
        # Recorder first, pill second: the mic should be capturing before we
        # spend time drawing, so the first syllable is not clipped (5a, 5b).
        self._indicator.show_recording()

    def _cancel(self) -> None:
        # Bin the audio.  _held is deliberately left alone: the modifier is
        # still physically down, and the transition rule in _handle_down uses
        # that to refuse a restart until it is genuinely released and pressed
        # again.
        self._recorder.stop()
        self._indicator.hide()
        self.state = State.IDLE
        self.mode = Mode.NONE

    # -- the chunk pump -----------------------------------------------------

    def _snapshot(self) -> np.ndarray:
        snapshot = getattr(self._recorder, "snapshot", None)
        if snapshot is None:
            return np.empty(0, dtype=np.float32)
        return np.asarray(snapshot(), dtype=np.float32).reshape(-1)

    def _pump_chunks(self) -> None:
        """Transcribe whichever ~2s windows have completed since the last tick.

        Never raises. A chunk that fails is a chunk of text missing from a
        preview nobody sees; the alternative -- letting it escape onto the
        controller thread -- would take the hotkey down with it.
        """
        try:
            audio = self._snapshot()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return
        for window in self._chunker.ready(audio):
            try:
                self._chunk_texts.append(self._transcribe_window(window, final=False))
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def _transcribe_window(self, window: np.ndarray, *, final: bool) -> str:
        """Trim, skip if silent, transcribe. Timed into the right bucket.

        Trimming first is spec item 8 and is pure speed: a window captured
        during a pause can be most of the way silence, and Whisper's cost is
        proportional to what it is handed. An all-silent window skips the model
        entirely -- which is also the single most effective guard against it
        hallucinating "Thank you." out of room tone.
        """
        trimmed = trim_silence(window)
        if trimmed.size == 0 or is_silent(trimmed):
            return ""
        self._heard_speech = True
        bucket = self._timings.final_chunk() if final else self._timings.chunk()
        with bucket:
            return self._transcriber.transcribe(trimmed)

    def _silence_exhausted(self) -> bool:
        """Whether trailing silence has run past ``silence_timeout_s``.

        Zero disables it (6c). Silence before the user has said anything does
        not count: pressing the key and thinking for three seconds is normal,
        and ending the dictation there would be baffling.
        """
        if self._silence_timeout_s <= 0 or not self._heard_speech:
            return False
        try:
            audio = self._snapshot()
        except Exception:  # noqa: BLE001
            return False
        return trailing_silence_s(audio) >= self._silence_timeout_s

    # -- stopping -----------------------------------------------------------

    def _stop_and_process(self) -> None:
        duration = self._clock() - self._started_at
        audio = self._recorder.stop()
        self.mode = Mode.NONE
        if duration < self._min_duration_s:
            # SPEC.md hallucination guard 1: short taps never reach the model,
            # and the discard is silent -- no flash, no paste.
            self._indicator.hide()
            self.state = State.IDLE
            return

        self.state = State.PROCESSING
        try:
            self._indicator.show_processing()
            self._deliver(audio)
        finally:
            # Belt and braces: whatever went wrong, the machine is usable again.
            self.state = State.IDLE
            self._report_timings()

    def _final_text(self, audio: Any) -> str:
        """Everything the chunks heard, plus the tail, stitched into one string.

        Only the tail goes to the model here (spec 9a) -- the rest was
        transcribed while the user was still talking.
        """
        buffer = np.asarray(audio, dtype=np.float32).reshape(-1)
        tail = self._chunker.remainder(buffer)
        tail_text = ""
        if tail.size:
            tail_text = self._transcribe_window(tail, final=True)
        return stitch_all(self._chunk_texts + [tail_text])

    def _deliver(self, audio: Any) -> None:
        if self._recorder is not None:
            self._record_first_audio()
        try:
            text = self._final_text(audio)
        except Exception:
            self._indicator.flash(ERROR_MESSAGE)
            return

        if text:
            # 9c: one cleanup pass, over the stitched whole. Per-chunk cleaning
            # would strip a filler that spanned a boundary in one half only,
            # and the AI variant can only work on a finished sentence anyway.
            with self._timings.cleanup():
                try:
                    text = self._clean(text)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()  # raw text beats no text

        if not text or not text.strip():
            # "" is the honest "nothing heard" signal (SPEC.md guard 4).
            self._indicator.flash(NOTHING_HEARD_MESSAGE)
            return

        with self._timings.insertion():
            try:
                pasted = self._paster.paste_text(text)
            except Exception:
                pasted = False
        if pasted:
            self._indicator.hide()
        else:
            self._indicator.flash(PASTE_FAILED_MESSAGE)

    def _record_first_audio(self) -> None:
        """Copy the recorder's first-block stamp into the timing report (11a).

        The recorder stamps it on PortAudio's thread, because that is the only
        place that knows when audio actually arrived. Reading it here rather
        than subscribing to it keeps the audio callback free of our concerns.
        """
        stamped = getattr(self._recorder, "first_block_at", None)
        if stamped is None:
            return
        self._timings.first_audio_ms = max(
            0.0, (stamped - self._timings._t0) * 1000.0
        )

    def _report_timings(self) -> None:
        try:
            self._on_timings(self._timings)
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def _print_timings(timings: Timings) -> None:
    """The default sink: print the block to the console (spec 11)."""
    say(timings.report())
