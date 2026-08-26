"""Measure what this microphone and this voice actually produce, in dB.

`waveform.FLOOR_DB` / `CEIL_DB` decide how a room's loudness maps onto bar
height, and shipped as -60 / -15 without anyone measuring a real voice through
a real microphone. Guessing them is how the trace ends up compressed into the
top of its range, where every syllable looks equally loud.

Run it, talk normally for the duration, and it prints the percentiles to put in
those two constants.

    .venv/bin/python tools/calibrate_levels.py [seconds]

Stop the app first: the recorder is documented as the only microphone stream in
this project, and PortAudio keeps global state that a second one can disturb.
"""

from __future__ import annotations

import math
import sys
import time

from vocal_advantage.recorder import Recorder
from vocal_advantage import waveform as wf


def db(rms: float) -> float:
    return 20.0 * math.log10(rms) if rms > 0.0 else -120.0


def main(seconds: float = 12.0) -> int:
    recorder = Recorder()
    recorder.open()
    recorder.start()  # `level` only tracks while capturing -- see Recorder.level
    print("Talk normally for %.0f seconds. Go." % seconds)

    samples: list[float] = []
    ended = time.monotonic() + seconds
    while time.monotonic() < ended:
        samples.append(recorder.level)
        time.sleep(1.0 / 60.0)      # the renderer's own rate

    recorder.stop()
    recorder.close()

    loud = sorted(db(s) for s in samples if s > 0.0)
    if not loud:
        print("No audio at all. Is the microphone permitted and unmuted?")
        return 1

    def pct(p: float) -> float:
        return loud[min(len(loud) - 1, int(len(loud) * p))]

    print("\n%d samples, %d with audio" % (len(samples), len(loud)))
    for p in (0.05, 0.25, 0.50, 0.75, 0.95, 0.99):
        print("  p%-3d %7.1f dB" % (p * 100, pct(p)))

    # The floor wants to sit just under the quiet end of real speech, and the
    # ceiling just over the loud end -- so ordinary talking uses the whole bar
    # rather than the top of it. p25/p99 rather than min/max: the extremes are
    # room noise and the odd plosive, and letting either set the range is what
    # squashes everything else.
    floor, ceil = round(pct(0.25)), round(pct(0.99))
    print("\nSuggested, from this voice on this microphone:")
    print("  FLOOR_DB = %.1f   (currently %.1f)" % (floor, wf.FLOOR_DB))
    print("  CEIL_DB  = %.1f   (currently %.1f)" % (ceil, wf.CEIL_DB))

    print("\nWhat the current constants do to that voice:")
    for name, p in (("quiet", 0.25), ("normal", 0.50), ("loud", 0.95)):
        rms = 10 ** (pct(p) / 20.0)
        now = wf.level_from_rms(rms)
        then = wf.level_from_rms(rms, floor_db=floor, ceil_db=ceil)
        print("  %-6s %6.1f dB -> %3.0f%% tall now, %3.0f%% after"
              % (name, pct(p), now * 100, then * 100))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 12.0))
