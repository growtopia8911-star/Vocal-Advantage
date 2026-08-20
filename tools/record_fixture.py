"""Record tests/fixtures/testing_one_two_three.wav from the real microphone.

Run from the repo root and say "testing one two three" a couple of times:

    python tools/record_fixture.py [seconds]

The slow integration test (tests/test_transcriber.py, -m slow) skips until this
file exists. It wants 16kHz mono 16-bit PCM, which is exactly what
recorder.Recorder captures, so this is also a second real-hardware exercise of
Task 5.

The window is deliberately long and self-trimming: it keeps only the stretch
from the first to the last sample that is clearly above the room's noise floor,
plus a little padding. That way nobody has to hit a countdown precisely, and the
committed fixture is still short.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402 - must come after the sys.path fix above

from vocal_advantage.recorder import SAMPLE_RATE, Recorder, RecorderError  # noqa: E402

LEAD_IN_S = 6
DEFAULT_SECONDS = 14.0
PAD_S = 0.25
TARGET = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "testing_one_two_three.wav"
)


def trim_to_speech(audio: np.ndarray) -> np.ndarray:
    """Keep first-to-last loud sample, padded. Returns the input if all quiet."""
    if audio.size == 0:
        return audio
    # 20ms frames, so the threshold reacts to speech rather than single samples.
    frame = SAMPLE_RATE // 50
    usable = (audio.size // frame) * frame
    if usable == 0:
        return audio
    frames = np.abs(audio[:usable]).reshape(-1, frame).max(axis=1)
    floor = float(np.median(frames))
    threshold = max(floor * 4.0, 0.02)
    loud = np.flatnonzero(frames > threshold)
    if loud.size == 0:
        return audio
    pad = int(PAD_S * SAMPLE_RATE)
    start = max(0, loud[0] * frame - pad)
    end = min(audio.size, (loud[-1] + 1) * frame + pad)
    return audio[start:end]


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 58)
    print("  Say:  testing one two three")
    print("  Say it two or three times, with a pause between.")
    print("=" * 58)
    for count in range(LEAD_IN_S, 0, -1):
        print(f"  starting in {count}...", flush=True)
        time.sleep(1.0)

    recorder = Recorder()
    try:
        recorder.start()
    except RecorderError as error:
        print(f"\nFAIL: could not open the microphone.\n  {error}")
        return 1
    print(f"  RECORDING - speak now ({seconds:.0f}s)", flush=True)
    try:
        time.sleep(seconds)
    finally:
        audio = recorder.stop()
    print("  done.")

    if audio.size < SAMPLE_RATE * seconds * 0.8:
        print(f"\nFAIL: only captured {audio.size} samples; expected about "
              f"{int(SAMPLE_RATE * seconds)}.")
        return 1

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < 0.02:
        print(f"\nFAIL: that was near-silent (peak {peak:.4f}). Check "
              "System Settings > Sound > Input, then try again - the model "
              "cannot transcribe silence.")
        return 1

    clip = trim_to_speech(audio)

    # float32 [-1, 1] -> 16-bit PCM, which is what the test reads back and
    # divides by 32768.0.
    pcm = np.clip(clip * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(TARGET), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())

    print()
    print(f"Wrote {TARGET}")
    print(f"  captured {audio.size / SAMPLE_RATE:.1f}s, kept "
          f"{clip.size / SAMPLE_RATE:.1f}s, peak {peak:.4f}, 16kHz mono 16-bit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
