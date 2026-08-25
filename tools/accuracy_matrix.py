"""Word error rate across engine, model and chunk length, on the same clips.

    .venv/bin/python tools/accuracy_matrix.py

Answers three questions that got tangled together:

1. Does the MLX/Metal backend transcribe worse than faster-whisper on the CPU?
   It is a different engine with different decoding defaults, so "the GPU made
   it faster" must not quietly mean "and less accurate".
2. Does chunking cost accuracy, and how much does the window length matter?
3. Is a bigger model now affordable? Metal made compute cheap; the point of
   cheap compute is to spend it on something.

Everything runs against tests/fixtures/accuracy and the expected transcripts in
tools/accuracy_session.py, so the numbers are comparable with what is already
in the project note.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.accuracy_session import EXPECTED  # noqa: E402
from tools.score_accuracy import wer  # noqa: E402
from vocal_advantage.chunker import RollingChunker  # noqa: E402
from vocal_advantage.stitch import stitch_all  # noqa: E402
from vocal_advantage.transcriber import Transcriber  # noqa: E402
from vocal_advantage.vad import is_silent, trim_silence  # noqa: E402

RATE = 16000
FIXTURES = REPO / "tests" / "fixtures" / "accuracy"


def load(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def whole(audio: np.ndarray, transcriber: Transcriber) -> str:
    return transcriber.transcribe(audio)


def chunked(audio, transcriber, chunk_s: float, overlap_s: float = 0.25) -> str:
    """Exactly what the controller does, minus the microphone."""
    cursor = RollingChunker(chunk_s=chunk_s, overlap_s=overlap_s)
    texts: list[str] = []
    step = int(0.1 * RATE)
    for end in range(step, audio.size + step, step):
        for window in cursor.ready(audio[:end]):
            trimmed = trim_silence(window)
            if trimmed.size and not is_silent(trimmed):
                texts.append(transcriber.transcribe(trimmed))
    tail = cursor.remainder(audio)
    if tail.size:
        trimmed = trim_silence(tail)
        if trimmed.size and not is_silent(trimmed):
            texts.append(transcriber.transcribe(trimmed))
    return stitch_all(texts)


def run(label: str, model: str, device: str, mode, clips) -> dict:
    transcriber = Transcriber(
        model_name=model, device=device, language="en", min_duration_s=0.0
    )
    transcriber.warm_up()
    total_wer = 0.0
    total_time = 0.0
    total_audio = 0.0
    worst: list[tuple[float, str, str]] = []
    for name, audio, want in clips:
        started = time.monotonic()
        got = mode(audio, transcriber)
        total_time += time.monotonic() - started
        total_audio += audio.size / RATE
        rate = wer(want, got)[0]
        total_wer += rate
        worst.append((rate, name, got))
    n = len(clips)
    worst.sort(reverse=True)
    return {
        "label": label,
        "wer": total_wer / n * 100,
        "rtf": total_time / total_audio,
        "worst": worst[:2],
    }


def main() -> int:
    clips = []
    for path in sorted(FIXTURES.glob("*.wav")):
        want = EXPECTED.get(path.stem)
        if want:
            clips.append((path.stem, load(path), want))
    if not clips:
        raise SystemExit("no fixtures with expected text")
    print(f"{len(clips)} clips\n")

    plans = [
        ("faster-whisper small CPU, whole", "small", "cpu", whole),
        ("mlx small Metal, whole", "small", "metal", whole),
        ("mlx small Metal, 2s chunks", "small", "metal",
         lambda a, t: chunked(a, t, 2.0)),
        ("mlx small Metal, 6s chunks", "small", "metal",
         lambda a, t: chunked(a, t, 6.0)),
        ("mlx small Metal, 10s chunks", "small", "metal",
         lambda a, t: chunked(a, t, 10.0)),
        ("mlx large-v3-turbo Metal, whole", "large-v3-turbo", "metal", whole),
        ("mlx large-v3-turbo Metal, 6s chunks", "large-v3-turbo", "metal",
         lambda a, t: chunked(a, t, 6.0)),
    ]

    results = []
    for label, model, device, mode in plans:
        try:
            results.append(run(label, model, device, mode, clips))
            r = results[-1]
            print(f"{r['label']:<40}{r['wer']:6.1f}%  rtf {r['rtf']:.2f}x")
        except Exception as error:  # noqa: BLE001 - a missing model is a result
            print(f"{label:<40}  FAILED: {type(error).__name__}: {error}")

    print("\n" + "=" * 78)
    print(f"{'configuration':<40}{'WER':>8}{'RTF':>8}")
    print("-" * 78)
    for r in sorted(results, key=lambda r: r["wer"]):
        print(f"{r['label']:<40}{r['wer']:7.1f}%{r['rtf']:7.2f}x")
    print("\nworst clip per configuration:")
    for r in sorted(results, key=lambda r: r["wer"]):
        rate, name, got = r["worst"][0]
        print(f"  {r['label']:<40}{name} {rate*100:.0f}%  {got[:44]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
