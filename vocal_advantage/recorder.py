"""Microphone capture: raw mic audio -> a 16kHz mono float32 numpy array.

The stream is opened only while a recording is in progress.  Windows lights its
"microphone in use" indicator for as long as a stream is open, so keeping one
open between dictations would leave the light on and make the app look like it
is always listening (SPEC, "Audio" decision).

Test seam: the sounddevice library is bound once, at import time, to the
module-level name ``sd``, and every call below goes through that name.  Unit
tests replace ``vocal_advantage.recorder.sd`` with a fake module, so the real
logic runs with no PortAudio and no microphone.  Never do
``from sounddevice import InputStream`` here - it would bypass the seam.
"""

from __future__ import annotations

import threading

import numpy as np

try:  # pragma: no cover - the failure path is checked with sd patched to None
    import sounddevice as sd
except Exception:  # noqa: BLE001 - any import failure means "no audio backend"
    sd = None


SAMPLE_RATE: int = 16000
BLOCKSIZE: int = 1024
CHANNELS: int = 1
DTYPE: str = "float32"


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
    WasapiSettings at all - that is the seam the Mac port needs in v1.0.
    """
    settings_cls = getattr(sd, "WasapiSettings", None)
    if settings_cls is None:
        return None
    try:
        return settings_cls(auto_convert=True)
    except Exception:  # noqa: BLE001
        return None


class Recorder:
    """Captures the microphone into memory for the length of one dictation."""

    def __init__(self, samplerate: int = SAMPLE_RATE) -> None:
        self.samplerate = samplerate
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._stream = None

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        """Open the mic. Calling it again while recording does nothing.

        Raises RecorderError if the microphone cannot be opened.  On that path
        nothing is left half-done: is_recording stays False and no stream is
        leaked, so the caller can simply report the error and stay IDLE.
        """
        if self._stream is not None:
            return
        with self._lock:
            self._chunks = []
        self._stream = self._open_stream()

    def stop(self) -> np.ndarray:
        """Close the mic and return everything captured, oldest sample first.

        Never raises.  Safe to call when not recording: returns an empty
        float32 array.
        """
        stream, self._stream = self._stream, None
        if stream is not None:
            _close_quietly(stream)
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def _callback(self, indata, frames, time_info, status) -> None:
        """Called on PortAudio's own thread, once per 1024-frame block.

        ``status`` (overflow flags) is ignored on purpose: dropping a block is
        better than killing the stream mid-sentence.
        """
        block = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
        with self._lock:
            self._chunks.append(block)

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
                    "device is plugged in and that Windows lets this app use it "
                    "(Settings > Privacy & security > Microphone). PortAudio "
                    f"said: {second_error}"
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
