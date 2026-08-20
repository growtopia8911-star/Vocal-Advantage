"""Record a FAST-speech fixture, for comparing models on connected speech.

Run from the repo root and read the sentence on screen twice, quickly:

    uv run python tools/record_fast_speech.py

Fast connected speech is where small models fail: they need clear word
boundaries, and rapid speech removes them. tests/fixtures/fast_speech.wav
therefore exercises a failure mode the normal-paced fixture cannot reach.

The reference sentence is committed alongside the audio so accuracy can be
scored as a word error rate rather than eyeballed.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402 - must come after the sys.path fix above

from tools.record_fixture import trim_to_speech  # noqa: E402
from vocal_advantage.recorder import SAMPLE_RATE, Recorder, RecorderError  # noqa: E402

SENTENCE = (
    "I talk pretty fast so the transcription doesn't always keep up "
    "with what I'm actually saying"
)
LEAD_IN_S = 5
SECONDS = 14.0
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
TARGET = FIXTURES / "fast_speech.wav"
REFERENCE = FIXTURES / "fast_speech.txt"


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 66)
    print("  Read this OUT LOUD, TWICE, as fast as you normally rush:")
    print()
    print(f"    {SENTENCE}")
    print()
    print("  Do not slow down or over-enunciate -- the whole point is to")
    print("  capture the speed that is currently breaking it.")
    print("=" * 66)
    for count in range(LEAD_IN_S, 0, -1):
        print(f"  starting in {count}...", flush=True)
        time.sleep(1.0)

    recorder = Recorder()
    try:
        recorder.start()
    except RecorderError as error:
        print(f"\nFAIL: could not open the microphone.\n  {error}")
        return 1
    print(f"  RECORDING - go ({SECONDS:.0f}s)", flush=True)
    try:
        time.sleep(SECONDS)
    finally:
        audio = recorder.stop()
    print("  done.")

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < 0.02:
        print(f"\nFAIL: near-silent (peak {peak:.4f}). Check System Settings > "
              "Sound > Input and try again.")
        return 1

    clip = trim_to_speech(audio)
    pcm = np.clip(clip * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(TARGET), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    REFERENCE.write_text(SENTENCE + "\n", encoding="utf-8")

    spoken = clip.size / SAMPLE_RATE
    words = len(SENTENCE.split()) * 2
    print()
    print(f"Wrote {TARGET}")
    print(f"  kept {spoken:.1f}s, peak {peak:.4f}, 16kHz mono 16-bit")
    print(f"  about {words / spoken * 60:.0f} words per minute "
          "(conversational is 150; 200+ is fast)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
