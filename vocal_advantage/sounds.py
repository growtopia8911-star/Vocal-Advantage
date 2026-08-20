"""Short generated tones, so you know what happened without looking.

Generated with numpy rather than shipped as .wav files, for the same reason the
tray icon is drawn with Pillow: no binaries in the repository.

**The start tone is off by default, and that is a real decision, not caution.**
It would play while the microphone is open. On speakers it goes straight back
into the recording and Whisper transcribes something for it -- a hallucinated
word at the front of every dictation, appearing only for people not wearing
headphones, which is a horrible bug to be told about second-hand. The done and
error tones are safe: the stream is closed by then, so they are on by default.

Never raises and never blocks. A machine with no output device, or one whose
audio is already busy, loses its tones and keeps its dictation.
"""

from __future__ import annotations

import threading

import numpy as np

from .console import warn

SAMPLE_RATE = 44100

#: Quiet. This is a confirmation, not an alarm, and it fires many times a day.
VOLUME = 0.18

#: (frequency Hz, duration ms) pairs, played in order.
TONES: dict[str, tuple[tuple[float, float], ...]] = {
    # Rising: something began.
    "start": ((660.0, 70.0), (880.0, 70.0)),
    # A single softer note: it worked, nothing needs your attention.
    "done": ((520.0, 90.0),),
    # Falling, and lower. Distinguishable from "done" without being startling.
    "error": ((400.0, 90.0), (300.0, 130.0)),
}

#: Fraction of each tone spent fading in and out. Without it the waveform
#: starts and stops at a non-zero sample and you hear a click, which sounds
#: like a fault rather than a chime.
FADE = 0.35


def tone(frequency: float, milliseconds: float, samplerate: int = SAMPLE_RATE,
         volume: float = VOLUME) -> np.ndarray:
    """One enveloped sine wave, as float32 in -1..1."""
    count = max(1, int(samplerate * milliseconds / 1000.0))
    t = np.arange(count, dtype=np.float32) / samplerate
    wave = np.sin(2.0 * np.pi * frequency * t, dtype=np.float32) * volume

    # Raised-cosine fade at both ends, so every tone begins and ends at zero.
    fade = max(1, int(count * FADE))
    ramp = (1.0 - np.cos(np.linspace(0.0, np.pi, fade, dtype=np.float32))) / 2.0
    wave[:fade] *= ramp
    wave[-fade:] *= ramp[::-1]
    return wave


def chime(kind: str, samplerate: int = SAMPLE_RATE) -> np.ndarray:
    """The samples for one named sound. Empty array for an unknown name."""
    parts = TONES.get(kind)
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(
        [tone(freq, ms, samplerate) for freq, ms in parts]
    ).astype(np.float32, copy=False)


class Player:
    """Plays the tones, on a background thread, never raising.

    The thread matters. Even a 200ms tone played inline would sit on the
    controller thread, and the controller thread is what starts the recording:
    the delay lands between pressing the hotkey and the microphone opening,
    which is precisely where a clipped first syllable comes from.
    """

    def __init__(self, enabled: bool = True, on_start: bool = False) -> None:
        self.enabled = enabled
        #: Separate from `enabled` because it carries a risk the others do not
        #: -- see the module docstring.
        self.on_start = on_start
        self._warned = False

    def play(self, kind: str) -> None:
        if not self.enabled:
            return
        if kind == "start" and not self.on_start:
            return
        threading.Thread(
            target=self._play_now, args=(kind,),
            name="vocal-advantage-sound", daemon=True,
        ).start()

    def _play_now(self, kind: str) -> None:
        try:
            # Imported here, not at module scope: this file is imported by the
            # tests, and binding sounddevice at import would drag PortAudio and
            # a real audio device into every one of them.
            import sounddevice as sd

            samples = chime(kind)
            if samples.size:
                sd.play(samples, SAMPLE_RATE)
        except Exception:  # noqa: BLE001 - a missing speaker is not an outage
            # Once only. This runs on every dictation, and a machine with no
            # output device would otherwise fill the console with the same line.
            if not self._warned:
                self._warned = True
                warn("Sounds are unavailable on this machine; carrying on without them.")
