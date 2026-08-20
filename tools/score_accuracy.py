"""Score the clips from accuracy_session.py against what was meant to be said.

    uv run python tools/score_accuracy.py

Reports word error rate per clip at each stage of the pipeline, so a mistake
can be blamed on the right component: Whisper mishearing, the rules removing
too much, or the AI pass rewriting.

WER is edit distance over words, normalised for case and punctuation, which is
the standard measure and is not the same as "looks about right".
"""

from __future__ import annotations

import json
import re
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from vocal_advantage.cleanup import ai_clean, clean_speech, warm_up_model  # noqa: E402
from vocal_advantage.config import load_config  # noqa: E402
from vocal_advantage.transcriber import Transcriber  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "accuracy"


def normalise(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower()).split()


def wer(reference: str, hypothesis: str) -> tuple[float, int, int]:
    """Word error rate, plus the edit count and reference length."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return (0.0 if not hyp else 1.0), len(hyp), 0
    # Levenshtein over words.
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref), previous[-1], len(ref)


def main() -> int:
    manifest_path = FIXTURES / "manifest.json"
    if not manifest_path.exists():
        print("No clips yet. Run:  uv run python tools/accuracy_session.py")
        return 1
    manifest = json.loads(manifest_path.read_text())

    cfg = load_config()
    t = Transcriber(cfg["model"], cfg["device"], cfg["language"],
                    float(cfg["min_duration_s"]))
    t.warm_up()
    ai_on = bool(cfg.get("ai_cleanup", False))
    if ai_on and not warm_up_model():
        print("NOTE: Ollama did not answer; the AI column will be rules-only.")

    print(f"\nmodel={cfg['model']}  ai_cleanup={ai_on}\n")
    header = f"{'clip':12} {'WER raw':>8} {'WER final':>10}  what was typed"
    print(header)
    print("-" * 100)

    totals = [0, 0, 0]  # raw edits, final edits, reference words
    for item in manifest:
        path = FIXTURES / item["wav"]
        with wave.open(str(path)) as wav:
            audio = (np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
                     .astype(np.float32) / 32768.0)
        said = item["sentence"]
        raw = t.transcribe(audio)
        final = ai_clean(raw) if ai_on else clean_speech(raw)

        raw_wer, raw_edits, ref_len = wer(said, raw)
        fin_wer, fin_edits, _ = wer(said, final)
        totals[0] += raw_edits
        totals[1] += fin_edits
        totals[2] += ref_len
        print(f"{item['label']:12} {raw_wer:7.1%} {fin_wer:9.1%}  {final!r}")
        if raw_wer > 0:
            print(f"{'':12} {'':8} {'':10}  said: {said!r}")
            print(f"{'':12} {'':8} {'':10}  heard: {raw!r}")

    print("-" * 100)
    if totals[2]:
        print(f"{'OVERALL':12} {totals[0]/totals[2]:7.1%} {totals[1]/totals[2]:9.1%}"
              f"   ({totals[2]} reference words)")
    print("\nLower is better. Under 5% is good dictation; under 10% is usable.")
    print("A high 'raw' with a low 'final' means the cleanup is saving you.")
    print("A low 'raw' with a high 'final' means the cleanup is hurting you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
