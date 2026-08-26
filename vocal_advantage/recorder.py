"""Microphone capture: raw mic audio -> a 16kHz mono float32 numpy array.

**The stream is opened once, at startup, and stays open.** Opening a PortAudio
input stream is not free -- device negotiation, format conversion setup and the
first callback take a good fraction of a second, and paying that on the
keypress meant the beginning of the first word was routinely clipped. So the
stream now runs for the life of the process and the hotkey only flips a flag.

The cost of that decision, stated plainly because it reverses an earlier one:
the OS "microphone in use" indicator stays lit for as long as the app is
running. It used to go out between dictations, and someone glancing at the
menu bar could see the app was not listening. It now looks like it always is.
The audio is still discarded -- see the rolling buffer below -- but that is a
promise the user has to take on trust rather than read off the screen.

While nobody is dictating, incoming blocks go into a small ring buffer that is
capped and dropped. It exists so the stream has somewhere to put audio, not so
anything can read it: ``start()`` throws it away. Without the cap, idling for
an afternoon would accumulate an afternoon of audio in memory.

Test seam: the sounddevice library is bound once, at import time, to the
module-level name ``sd``, and every call below goes through that name. Unit
tests replace ``vocal_advantage.recorder.sd`` with a fake module, so the real
logic runs with no PortAudio and no microphone. Never do
``from sounddevice import InputStream`` here - it would bypass the seam.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from vocal_advantage.waveform import block_rms

try:  # pragma: no cover - the failure path is checked with sd patched to None
    import sounddevice as sd
except Exception:  # noqa: BLE001 - any import failure means "no audio backend"
    sd = None


SAMPLE_RATE: int = 16000
BLOCKSIZE: int = 1024
CHANNELS: int = 1
DTYPE: str = "float32"

#: How much idle audio the ring buffer holds before dropping the oldest block.
#: Nothing reads it, so this is purely a bound on memory: two seconds is small
#: enough to be irrelevant and large enough that the buffer is never the reason
#: a block is lost during the flag flip into recording.
IDLE_BUFFER_S: float = 2.0


class RecorderError(RuntimeError):
    """The microphone could not be opened."""


def _close_quietly(stream) -> None:
    """Shut a stream down without ever raising.

    A stream whose device was unplugged raises on stop() and close(); we still
    want the audio captured before the unplug, and we must not leave the
    recorder wedged in a half-open state.
    """
    for method_name in ("stop", "close"):
        try:
            getattr(stream, method_name)()
        except Exception:  # noqa: BLE001
            pass


def _wasapi_settings():
    """WASAPI's auto-convert, or None if this build/host API has no such thing.

    Nearly every Windows mic runs at 44.1 or 48kHz.  auto_convert makes the
    WASAPI driver resample down to our 16kHz instead of PortAudio refusing the
    rate outright (SPEC, "Audio").  Returns None when sounddevice has no
    WasapiSettings at all - that is the seam the Mac port needs.
    """
    settings_cls = getattr(sd, "WasapiSettings", None)
    if settings_cls is None:
        return None
    try:
        return settings_cls(auto_convert=True)
    except Exception:  # noqa: BLE001
        return None


class Recorder:
    """One long-lived microphone stream; captures on demand."""

    def __init__(
        self,
        samplerate: int = SAMPLE_RATE,
        clock: Callable[[], float] = time.monotonic,
        idle_buffer_s: float = IDLE_BUFFER_S,
    ) -> None:
        self.samplerate = samplerate
        self._clock = clock
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._stream = None
        #: Set while a dictation is in progress. Read by the audio callback on
        #: PortAudio's thread; a plain bool, because assignment and read are
        #: atomic under the GIL and the callback must never wait on a lock it
        #: does not have to.
        self._capturing = False
        self.idle_cap_samples = int(idle_buffer_s * samplerate)
        #: Where audio goes when nobody is dictating. Bounded, never read.
        self._idle: deque[np.ndarray] = deque()
        self._idle_samples = 0
        #: When the first block of the current capture arrived, for the
        #: "keypress -> first audio" timing. None until one does.
        self.first_block_at: float | None = None
        #: Whether the last finished capture heard anything at all. A capture
        #: that received not one callback means the stream is dead, however
        #: healthy it claims to be -- see _check_stream.
        self._last_capture_had_audio = True
        # The Flow Bar's waveform, and the reason there is no second microphone
        # stream in this project. Written by PortAudio's thread, read by the
        # renderer 60 times a second. A plain float, deliberately: assignment
        # and read are atomic under the GIL, so the read needs no lock and a
        # renderer that stalls can never stall the audio callback.
        self._level = 0.0
        #: Which input device to open, as a sounddevice index, or None for
        #: the system default. Changed by the tray's Microphone item, which
        #: refuses while a dictation is in flight -- see set_device.
        self.device = None

    # -- state ---------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether the microphone stream is running."""
        return self._stream is not None

    @property
    def is_recording(self) -> bool:
        """Whether audio is being kept. Open and recording are not the same."""
        return self._capturing

    @property
    def idle_samples(self) -> int:
        """How much idle audio is buffered. For the test that it stays capped."""
        return self._idle_samples

    @property
    def level(self) -> float:
        """RMS of the most recent block, or 0.0 when not recording.

        Lock-free on purpose -- see ``_level``. Zero between dictations even
        though the stream is open, because the Flow Bar must rest flat when
        nobody is dictating rather than twitch at the room.
        """
        return self._level

    # -- stream lifecycle ----------------------------------------------------

    def set_device(self, device) -> None:
        """Switch input device, reopening the stream if one is running.

        Refuses mid-dictation rather than switching underneath it: `stop()`
        returns the audio captured so far, and swapping the device in the
        middle would hand back a recording spliced from two microphones.

        The stream is closed before the new device is opened, not after. Two
        streams briefly sharing one machine is the case PortAudio is least
        reliable about, and this is exactly the "device negotiation" the
        open-once-hold-forever design exists to avoid paying per dictation --
        so it is paid here, once, when a person asks for it.
        """
        if self._capturing:
            raise RecorderError(
                "Cannot switch microphone while a dictation is in progress."
            )
        if device == self.device:
            return
        was_open = self._stream is not None
        if was_open:
            self.close()
        self.device = device
        if was_open:
            self.open()  # raises RecorderError if the new device is unusable

    def open(self) -> None:
        """Open the microphone and leave it open. Called once, at startup.

        Raises RecorderError if it cannot be opened. On that path nothing is
        left half-done: is_open stays False and no stream is leaked.
        """
        if self._stream is not None:
            return
        self._stream = self._open_stream()

    def close(self) -> None:
        """Shut the stream down. Never raises. Safe when already closed."""
        stream, self._stream = self._stream, None
        self._capturing = False
        self._level = 0.0
        if stream is not None:
            _close_quietly(stream)
        with self._lock:
            self._chunks = []
        self._idle.clear()
        self._idle_samples = 0

    # -- capture -------------------------------------------------------------

    def start(self) -> None:
        """Begin keeping audio. Calling it again while recording does nothing.

        Opens the stream first if it somehow is not open. That fallback should
        never fire in the real app -- startup opens it -- but a failed open at
        launch must not mean dictation is dead for the rest of the session.
        """
        if self._capturing:
            return
        if self._stream is None:
            self.open()
        else:
            self._check_stream()
        with self._lock:
            self._chunks = []
        # The idle buffer is dropped rather than prepended. Audio from before
        # the keypress is not part of what the user asked to transcribe.
        self._idle.clear()
        self._idle_samples = 0
        self.first_block_at = None
        self._level = 0.0
        self._capturing = True

    def stop(self) -> np.ndarray:
        """Stop keeping audio and return everything captured, oldest first.

        Never raises. Safe to call when not recording: returns an empty float32
        array. **Leaves the stream open** -- that is the point of this rework.
        """
        self._capturing = False
        self._level = 0.0
        with self._lock:
            chunks, self._chunks = self._chunks, []
        self._last_capture_had_audio = bool(chunks)
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def _check_stream(self) -> None:
        """Recycle the stream before a capture if it looks dead.

        The always-open stream costs us the error path the old open-on-keypress
        design got for free: a microphone unplugged between dictations used to
        surface as a clear RecorderError on the next press, because the next
        press opened a stream. Now nothing opens anything, PortAudio simply
        stops calling us back, and the symptom would be dictation silently
        producing "nothing heard" forever.

        Two signals, because neither alone is reliable:

        * ``stream.active`` -- some host APIs drop a stream to inactive when
          its device disappears, and some do not, so a False here is
          trustworthy and a True proves little.
        * the previous capture receiving not one callback -- proof after the
          fact that the stream is not delivering.

        The second can misfire on a tap shorter than one 64ms block, costing an
        unnecessary reopen. That is the right way round: a wasted reopen is
        invisible, a mic that records nothing forever is not.
        """
        active = getattr(self._stream, "active", None)
        looks_dead = active is False or not self._last_capture_had_audio
        if not looks_dead:
            return
        _close_quietly(self._stream)
        self._stream = None
        self._last_capture_had_audio = True  # do not recycle twice for one fault
        self.open()  # raises RecorderError if the device is genuinely gone

    def snapshot(self) -> np.ndarray:
        """Everything captured so far, without stopping the recording.

        The chunk pump calls this on every tick while someone is speaking, so
        it must not disturb the recording. Non-destructive: stop() still
        returns the whole thing.

        Runs on the controller thread while PortAudio's thread is appending, so
        it takes the same lock the callback does.
        """
        with self._lock:
            chunks = list(self._chunks)
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    # -- the audio thread ----------------------------------------------------

    def _callback(self, indata, frames, time_info, status) -> None:
        """Called on PortAudio's own thread, once per 1024-frame block.

        ``status`` (overflow flags) is ignored on purpose: dropping a block is
        better than killing the stream mid-sentence.
        """
        block = np.asarray(indata, dtype=np.float32).reshape(-1).copy()

        if not self._capturing:
            # Idle: keep it briefly, bounded, and let it go. Nothing reads
            # this; the deque exists so the branch is cheap and uniform.
            self._idle.append(block)
            self._idle_samples += block.size
            while self._idle_samples > self.idle_cap_samples and self._idle:
                self._idle_samples -= self._idle.popleft().size
            return

        if self.first_block_at is None:
            self.first_block_at = self._clock()
        # Measured before the lock is taken, never inside it: this runs on
        # PortAudio's thread, and holding the lock for a microsecond longer
        # than the append needs is a microsecond the recording can drop in.
        self._level = block_rms(block)
        with self._lock:
            self._chunks.append(block)

    # -- opening -------------------------------------------------------------

    def _open_stream(self):
        if sd is None:
            raise RecorderError(
                "sounddevice is not installed, so there is no way to reach the "
                "microphone. Re-run the install step from the README: "
                'pip install -e ".[dev]"'
            )
        try:
            return self._open_once()
        except Exception as first_error:  # noqa: BLE001
            # PortAudio holds global state. Once a device has been yanked, or an
            # open has failed, that state can stay stale until the library is
            # torn down and brought back up - so do exactly that, once.
            # (SPEC: "On audio stream error, re-initialize PortAudio and reopen
            # on next recording.")
            del first_error
            try:
                sd._terminate()
                sd._initialize()
            except Exception:  # noqa: BLE001
                pass
            try:
                return self._open_once()
            except Exception as second_error:  # noqa: BLE001
                raise RecorderError(
                    "Could not open the microphone. Check that a recording "
                    "device is plugged in and that the OS lets this app use it "
                    "(on Windows: Settings > Privacy & security > Microphone; "
                    "on macOS: System Settings > Privacy & Security > "
                    f"Microphone). PortAudio said: {second_error}"
                ) from second_error

    def _open_once(self):
        extra = _wasapi_settings()
        if extra is not None:
            try:
                return self._start_stream(extra)
            except Exception:  # noqa: BLE001
                pass  # host API is not WASAPI - reopen without the extra info
        return self._start_stream(None)

    def _start_stream(self, extra_settings):
        stream = sd.InputStream(
            device=self.device,
            samplerate=self.samplerate,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=self._callback,
            extra_settings=extra_settings,
        )
        try:
            stream.start()
        except Exception:  # noqa: BLE001
            _close_quietly(stream)  # a leaked stream keeps the mic light on
            raise
        return stream
