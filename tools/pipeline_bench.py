"""Run one real recording through the real pipeline and print the timings.

    .venv/bin/python tools/pipeline_bench.py [wav ...]

No microphone and no hotkey: a wav file stands in for the capture, and the
chunker is fed the growing buffer exactly as the controller feeds it on each
tick. Everything else is the real thing -- the real backend choice, the real
model, the real silence trimming, the real stitcher.

This is what the before/after comparison is actually made with. The numbers the
app prints after a live dictation come from the same Timings object.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from vocal_advantage import backends  # noqa: E402
from vocal_advantage.chunker import RollingChunker  # noqa: E402
from vocal_advantage.cleanup import clean_speech  # noqa: E402
from vocal_advantage.config import DEFAULTS, load_config  # noqa: E402
from vocal_advantage.stitch import stitch_all  # noqa: E402
from vocal_advantage.timings import Timings  # noqa: E402
from vocal_advantage.transcriber import Transcriber  # noqa: E402
from vocal_advantage.vad import is_silent, trim_silence  # noqa: E402

SAMPLE_RATE = 16000


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if handle.getframerate() != SAMPLE_RATE:
            raise SystemExit(
                f"{path.name} is {handle.getframerate()}Hz; this expects "
                f"{SAMPLE_RATE}Hz mono."
            )
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def run_one(path: Path, transcriber: Transcriber, cfg: dict) -> None:
    audio = read_wav(path)
    seconds = audio.size / SAMPLE_RATE
    print(f"\n=== {path.name} ({seconds:.1f}s of audio) ===")

    timings = Timings()
    timings.start()
    # The capture is already in hand, so "first audio" is immediate. In the
    # live app this is the gap between the keypress and PortAudio's first
    # block, which is the number the always-open stream exists to shrink.
    timings.first_audio()

    chunker = RollingChunker(
        chunk_s=float(cfg["chunk_s"]), overlap_s=float(cfg["overlap_s"])
    )
    texts: list[str] = []

    # Feed the buffer the way the controller's tick does: a little more audio
    # each time, asking what is ready.
    step = int(0.1 * SAMPLE_RATE)
    for end in range(step, audio.size + step, step):
        for window in chunker.ready(audio[:end]):
            trimmed = trim_silence(window)
            if trimmed.size == 0 or is_silent(trimmed):
                texts.append("")
                continue
            with timings.chunk():
                texts.append(transcriber.transcribe(trimmed))

    tail = chunker.remainder(audio)
    if tail.size:
        trimmed = trim_silence(tail)
        if trimmed.size and not is_silent(trimmed):
            with timings.final_chunk():
                texts.append(transcriber.transcribe(trimmed))

    stitched = stitch_all(texts)
    with timings.cleanup():
        cleaned = clean_speech(stitched)

    print(f"  stitched: {stitched!r}")
    print(f"  cleaned : {cleaned!r}")
    print(timings.report())
    spoken = sum(timings.chunk_ms) + (timings.final_chunk_ms or 0.0)
    print(f"    {'model time / audio time':<28}{spoken / (seconds * 1000):>11.2f}x")


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]]
    if not paths:
        fixtures = REPO / "tests" / "fixtures"
        paths = sorted(fixtures.rglob("*.wav"))
    if not paths:
        raise SystemExit("no wav files found")

    cfg = dict(DEFAULTS)
    try:
        cfg.update(load_config())
    except Exception:  # noqa: BLE001 - a missing config is fine here
        pass

    choice = backends.choose_backend(
        device_setting=cfg["device"],
        platform=sys.platform,
        machine=__import__("platform").machine(),
        cuda=backends.has_cuda(),
        mlx=backends.has_mlx(),
    )
    print(backends.describe_choice(choice))
    print(f"model={cfg['model']} chunk_s={cfg['chunk_s']} overlap_s={cfg['overlap_s']}")

    transcriber = Transcriber(
        model_name=cfg["model"],
        device=cfg["device"],
        language=cfg["language"],
        min_duration_s=0.0,  # the chunker's windows are the unit here
    )
    started = time.monotonic()
    transcriber.warm_up()
    print(f"warm-up: {(time.monotonic() - started) * 1000:.0f} ms")

    for path in paths:
        run_one(path, transcriber, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
