"""Silence detection, in the two shapes the pipeline needs.

Two jobs, one energy measure:

* ``trim_silence`` cuts the quiet off both ends of a chunk before it reaches
  the model. Whisper's cost is proportional to the audio it is given, and a 2s
  window captured mid-pause can be most of the way silence -- so this is speed,
  not accuracy.
* ``trailing_silence_s`` measures how long it has been quiet at the end of the
  recording, which is what auto-stops a toggle someone forgot about.

Deliberately NOT a neural VAD. faster-whisper already runs Silero inside
``transcribe``; a second model here would cost more than the audio it saves and
would have to be loaded, warmed and fed on the controller thread. This is a
frame-energy gate, which is crude, cheap, and exactly right for "is this end of
the buffer worth sending".

**The threshold is deliberately low.** It has to sit below quiet speech and
above room tone, and getting that wrong in the tight direction clips the start
of words -- so it errs loose and lets a bit of hiss through. The model's own
VAD is the second line of defence.
"""

from __future__ import annotations

import numpy as np

# Must match recorder.SAMPLE_RATE. Duplicated for the same reason transcriber.py
# duplicates it: importing recorder drags sounddevice into every test run.
SAMPLE_RATE: int = 16000

#: Energy below this is "not speech". -46 dBFS. Ordinary speech sits near
#: -30 dBFS and a quiet room near -60, so this splits them with room to spare
#: on the room-tone side.
SILENCE_RMS: float = 0.005

#: Frame length for the energy measure. 32ms at 16kHz -- long enough that one
#: glottal closure does not read as silence, short enough to place a word
#: boundary to within a syllable.
FRAME_S: float = 0.032

#: Kept either side of the speech when trimming. A cut flush against the first
#: loud frame removes the attack of the word -- the plosive at the front of
#: "party" is quieter than the vowel behind it, and losing it turns the word
#: into "arty".
PAD_S: float = 0.12


def _frames(audio: np.ndarray, frame_len: int) -> np.ndarray:
    """Non-overlapping frames as a 2D view, trailing partial frame dropped."""
    usable = (audio.size // frame_len) * frame_len
    if usable == 0:
        return np.empty((0, frame_len), dtype=np.float32)
    return audio[:usable].reshape(-1, frame_len)


def frame_energy(audio: np.ndarray, frame_s: float = FRAME_S) -> np.ndarray:
    """RMS per frame. Empty array when there is not even one full frame."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    frame_len = max(1, int(frame_s * SAMPLE_RATE))
    frames = _frames(audio, frame_len)
    if frames.size == 0:
        return np.empty(0, dtype=np.float32)
    return np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1)).astype(
        np.float32
    )


def _loud_frame_indices(audio: np.ndarray, threshold: float) -> np.ndarray:
    return np.flatnonzero(frame_energy(audio) > threshold)


def is_silent(audio: np.ndarray, threshold: float = SILENCE_RMS) -> bool:
    """True when no frame anywhere in ``audio`` is above the threshold.

    The caller uses this to skip the model entirely (spec 8b). A chunk of pure
    room tone is the common case during a pause, and sending it to Whisper is
    both slow and the single most reliable way to make it hallucinate
    "Thank you."
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return True
    loud = _loud_frame_indices(audio, threshold)
    if loud.size:
        return False
    # Shorter than one frame: fall back to the raw peak rather than calling a
    # 20ms blip silent because it did not fill a frame.
    if audio.size < int(FRAME_S * SAMPLE_RATE):
        return bool(float(np.max(np.abs(audio))) <= threshold)
    return True


def trim_silence(
    audio: np.ndarray,
    threshold: float = SILENCE_RMS,
    pad_s: float = PAD_S,
) -> np.ndarray:
    """Cut the silence off both ends. Never touches the middle.

    Returns an empty array when the whole clip is silence, which is the signal
    the caller uses to skip the model call altogether.

    Only the ends, on purpose: a pause between two words is part of the
    sentence, and cutting it out would splice the words together and change
    where Whisper thinks the sentence breaks are.
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return audio
    loud = _loud_frame_indices(audio, threshold)
    if loud.size == 0:
        if audio.size < int(FRAME_S * SAMPLE_RATE) and not is_silent(audio, threshold):
            return audio  # a sub-frame blip that is genuinely loud
        return np.empty(0, dtype=np.float32)

    frame_len = max(1, int(FRAME_S * SAMPLE_RATE))
    pad = int(pad_s * SAMPLE_RATE)
    start = max(0, int(loud[0]) * frame_len - pad)
    end = min(audio.size, (int(loud[-1]) + 1) * frame_len + pad)
    return audio[start:end]


def trailing_silence_s(
    audio: np.ndarray, threshold: float = SILENCE_RMS
) -> float:
    """Seconds of silence at the end of ``audio``.

    Zero for an empty buffer, which matters: the watchdog runs from the moment
    recording starts, and "no audio yet" must not read as "silent for ages" and
    stop the recording before the user has said anything.
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return 0.0
    loud = _loud_frame_indices(audio, threshold)
    frame_len = max(1, int(FRAME_S * SAMPLE_RATE))
    if loud.size == 0:
        return audio.size / SAMPLE_RATE
    last_loud_end = (int(loud[-1]) + 1) * frame_len
    return max(0.0, (audio.size - last_loud_end) / SAMPLE_RATE)
