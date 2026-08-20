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
from tools.accuracy_session import EXPECTED  # noqa: E402
from vocal_advantage.transcriber import Transcriber  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "accuracy"


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}


def _fold_numbers(words: list[str]) -> list[str]:
    """"forty two" -> "42", so writing digits is not counted as three errors.

    Whisper turning spoken numbers into digits is desirable behaviour. Scoring
    it as a mistake made the digits clip read 62% wrong when its only real
    errors were "are" for "were" and "suit" for "suite".
    """
    out: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word in _TENS and i + 1 < len(words) and words[i + 1] in _UNITS:
            out.append(str(_TENS[word] + _UNITS[words[i + 1]]))
            i += 2
            continue
        if word in _TENS:
            out.append(str(_TENS[word]))
        elif word in _UNITS:
            out.append(str(_UNITS[word]))
        elif word == "hundred" and out and out[-1].isdigit():
            out[-1] = str(int(out[-1]) * 100)
        else:
            out.append(word)
        i += 1
    return out


def normalise(text: str) -> list[str]:
    return _fold_numbers(re.sub(r"[^\w\s']", " ", text.lower()).split())


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

        # raw is judged against what was spoken; final against what should
        # end up in the document, which is not the same string wherever the
        # cleanup is supposed to act.
        want = EXPECTED.get(item["label"], said)
        raw_wer, raw_edits, ref_len = wer(said, raw)
        fin_wer, fin_edits, _ = wer(want, final)
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
