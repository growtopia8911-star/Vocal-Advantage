"""Manual smoke test: record 2 seconds from the real microphone.

There is no honest automated version of this - it needs a real device and a
real voice, so it is a script you run by hand rather than a pytest test.

    python tools/smoke_recorder.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402 - must come after the sys.path fix above

from vocal_advantage.recorder import (  # noqa: E402
    SAMPLE_RATE,
    Recorder,
    RecorderError,
)

SECONDS = 2.0


def main() -> int:
    recorder = Recorder()
    print("Recording 2 seconds - say 'testing one two three' now...")
    print("(watch the Windows microphone icon in the system tray: it should")
    print(" light up for these 2 seconds and go out afterwards)")

    try:
        recorder.start()
    except RecorderError as error:
        # The same error Task 9's controller has to handle. Printing it plainly
        # here is how you check the message is actually readable.
        print()
        print("FAIL: the microphone could not be opened.")
        print(f"  {error}")
        return 1

    try:
        time.sleep(SECONDS)
    finally:
        audio = recorder.stop()  # never raises, so the mic always closes

    expected = int(SAMPLE_RATE * SECONDS)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    print()
    print(f"samples : {audio.size} (expected roughly {expected})")
    print(f"dtype   : {audio.dtype}")
    print(f"shape   : {audio.shape}")
    print(f"peak    : {peak:.4f}")
    print(f"rms     : {rms:.5f}")

    problems = []
    if audio.dtype != np.float32:
        problems.append(f"dtype is {audio.dtype}, expected float32")
    if audio.ndim != 1:
        problems.append(f"array has {audio.ndim} dimensions, expected 1 (mono)")
    if audio.size < expected * 0.8:
        problems.append("captured far less audio than the 2 seconds asked for")
    if peak < 0.01:
        # A dead-silent array means the wrong input device is selected or
        # Windows is blocking microphone access - not a bug in this code.
        problems.append(
            "the array is silent: check Settings > System > Sound > Input, and "
            "Settings > Privacy & security > Microphone"
        )

    print()
    if problems:
        print("FAIL:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASS - the microphone captures at 16kHz mono float32.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
