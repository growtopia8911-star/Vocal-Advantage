"""The dictation state machine.

    IDLE --keydown--> RECORDING --keyup--> PROCESSING --paste done--> IDLE

Pure Python: no Windows APIs, no threads, no sleeps.  Everything that touches
the OS arrives as an injected collaborator, and time arrives through the
injected ``clock``, so the whole machine is testable at full speed.

SPEC.md puts this logic inside ``main.py``; it lives here instead so it has its
own test file.  ``main.py`` is wiring only.

Threading contract: ``on_key_event`` is called from ONE thread (the controller
thread that drains the hotkey queue).  Nothing here is thread-safe, and nothing
here needs to be.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from enum import Enum, auto
from typing import Any, Protocol

from .hotkey_spec import HotkeySpec

# SPEC.md: "Key-downs within 30ms of the last ... are ignored."  Applied per
# key: a single global window would swallow the second half of a combo, which a
# human presses ~10ms after the first.
DEBOUNCE_S = 0.030

# SPEC.md: "Empty result -> no paste, pill flashes 'nothing heard'."
NOTHING_HEARD_MESSAGE = "nothing heard"
ERROR_MESSAGE = "error"
# SPEC.md's known-Windows-facts: a non-elevated process cannot paste into an
# elevated window, but the text is on the clipboard, so say so.
PASTE_FAILED_MESSAGE = "could not paste - press Ctrl+V"


class State(Enum):
    IDLE = auto()
    RECORDING = auto()
    PROCESSING = auto()


class RecorderLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> Any: ...


class TranscriberLike(Protocol):
    def transcribe(self, audio: Any) -> str: ...


class PasterLike(Protocol):
    def paste_text(self, text: str) -> bool: ...


class IndicatorLike(Protocol):
    def show_recording(self) -> None: ...
    def show_processing(self) -> None: ...
    def hide(self) -> None: ...
    def flash(self, message: str) -> None: ...


class DictationController:
    """Turns key events into record / transcribe / paste, and nothing else."""

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
        clock: Callable[[], float] = time.monotonic,
        on_partial: Callable[[], None] | None = None,
    ) -> None:
        self._hotkey = hotkey
        self._recorder = recorder
        self._transcriber = transcriber
        self._paster = paster
        self._indicator = indicator
        self._min_duration_s = float(min_duration_s)
        self._max_duration_s = float(max_duration_s)
        self._clock = clock
        self._on_partial = on_partial

        self.state: State = State.IDLE
        # Which of the hotkey's own keys are currently down.  Only hotkey keys
        # are tracked; we do not care what else the keyboard is doing.
        self._held: set[str] = set()
        # Per-key timestamp of the last *accepted* down, for the debounce.
        self._last_down: dict[str, float] = {}
        self._started_at = 0.0

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
        """Watchdog, called regularly.  Cheap and safe in any state.

        Also drives live dictation: while RECORDING, ``on_partial`` runs on each
        tick so a caller can transcribe the audio so far and type the words that
        have settled. Callers wanting that set a shorter tick interval.
        """
        if self.state is not State.RECORDING:
            return

        if self._on_partial is not None:
            try:
                self._on_partial()
            except Exception:  # noqa: BLE001
                # A partial pass is a nicety; the real transcript arrives on
                # release. Losing what someone just said because a preview
                # failed would be unforgivable, so this never propagates.
                traceback.print_exc()

        # SPEC.md: a 300s watchdog force-stops a forgotten recording *and
        # processes it* -- the audio is not thrown away.
        if self._clock() - self._started_at >= self._max_duration_s:
            self._stop_and_process()

    # -- key handling -------------------------------------------------------

    def _handle_down(self, key: str) -> None:
        if key not in self._hotkey.keys:
            # SPEC.md: cancel-on-other-key applies only to a bare-modifier
            # hotkey -- with Right Ctrl the user was typing Right Ctrl+C, not
            # dictating.  With F8 typing while dictating is legitimate.
            if self.state is State.RECORDING and self._hotkey.is_modifier_only:
                self._cancel()
            return

        if self.state is State.RECORDING:
            return  # OS autorepeat while the hotkey is held

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
        # SPEC.md: "keyup means any key of the combo went up."
        if self.state is State.RECORDING:
            self._stop_and_process()

    # -- transitions --------------------------------------------------------

    def _start(self, now: float) -> None:
        """Open the mic, then -- and only then -- commit to RECORDING.

        The order matters. Recorder.start() raises when the microphone cannot
        be opened (unplugged, blocked by Windows privacy settings, sounddevice
        missing) and leaves nothing half-open. Setting the state first would
        strand the machine in RECORDING with no stream and no pill, and the
        next key-up would "process" an empty recording. Failing before the
        assignment means a dead mic simply leaves us IDLE, ready to try again
        on the next hold.

        The error is swallowed on purpose: this runs on the thread draining the
        hotkey queue, so letting it escape would end dictation for the whole
        session over one unplugged microphone. The flash is what tells the
        user.
        """
        try:
            self._recorder.start()
        except Exception:  # noqa: BLE001 - collaborators are duck-typed here
            self._indicator.flash(ERROR_MESSAGE)
            return
        self._started_at = now
        self.state = State.RECORDING
        # Recorder first, pill second: the mic should be open before we spend
        # time drawing, so the first syllable is not clipped.
        self._indicator.show_recording()

    def _cancel(self) -> None:
        # Close the stream (the Windows mic indicator must go off) and bin the
        # audio.  _held is deliberately left alone: the modifier is still
        # physically down, and the transition rule in _handle_down uses that to
        # refuse a restart until it is genuinely released and pressed again.
        self._recorder.stop()
        self._indicator.hide()
        self.state = State.IDLE

    def _stop_and_process(self) -> None:
        duration = self._clock() - self._started_at
        audio = self._recorder.stop()
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

    def _deliver(self, audio: Any) -> None:
        try:
            text = self._transcriber.transcribe(audio)
        except Exception:
            self._indicator.flash(ERROR_MESSAGE)
            return
        if not text:
            # The transcriber already strips and joins, so "" is the honest
            # "nothing heard" signal (SPEC.md hallucination guard 4).
            self._indicator.flash(NOTHING_HEARD_MESSAGE)
            return
        try:
            pasted = self._paster.paste_text(text)
        except Exception:
            pasted = False
        if pasted:
            self._indicator.hide()
        else:
            self._indicator.flash(PASTE_FAILED_MESSAGE)
