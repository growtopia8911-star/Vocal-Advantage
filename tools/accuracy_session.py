"""Record a set of known sentences so accuracy can be scored, not guessed.

    uv run python tools/accuracy_session.py

Shows one sentence at a time. Press Enter, read it out, press Enter again.
Each clip is saved next to the exact words that were meant, so
``tools/score_accuracy.py`` can report a word error rate instead of an
impression.

The sentences are chosen to stress the specific things that have broken on
this project: fillers, stutters, a self-correction, digits, technical words,
and one long run-on that the AI pass should break into sentences.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from vocal_advantage.recorder import SAMPLE_RATE, Recorder, RecorderError  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "accuracy"

#: (label, what to read aloud, what should end up in the document).
#: The third column differs from the second wherever cleanup is meant to act.
#: Scoring the cleaned text against the literal utterance would count the app
#: doing its job as a mistake -- removing "Um," was showing up as a 10% error.
SENTENCES: tuple[tuple[str, str, str], ...] = (
    ("plain",
     "The build passed and the tests are green.",
     "The build passed and the tests are green."),
    ("fillers",
     "Um, so I think we should ship it on Friday.",
     "So I think we should ship it on Friday."),
    ("stutter",
     "Can you send me the the file when you get a sec?",
     "Can you send me the file when you get a sec?"),
    ("correction",
     "Let's meet Tuesday, no, Wednesday.",
     # The AI collapse is deliberately rejected on short sentences, so both
     # days survive and Kevin decides. See the speech-cleanup plan.
     "Let's meet Tuesday, no, Wednesday."),
    ("digits",
     "There were forty two failures in the suite.",
     "There were forty two failures in the suite."),
    ("technical",
     "The migration timed out, so I rolled back the deployment.",
     "The migration timed out, so I rolled back the deployment."),
    ("question",
     "What's the status on the pull request?",
     "What's the status on the pull request?"),
    ("rambling",
     "So the deploy went out and then the alerts started firing and I had to "
     "roll it back and then I spent the rest of the morning working out why "
     "the migration hadn't run.",
     "So the deploy went out and then the alerts started firing and I had to "
     "roll it back and then I spent the rest of the morning working out why "
     "the migration hadn't run."),
)

EXPECTED: dict[str, str] = {label: want for label, _said, want in SENTENCES}


def record_one(label: str, sentence: str) -> np.ndarray | None:
    print("\n" + "=" * 70)
    print(f"  {label}")
    print()
    print(f"    {sentence}")
    print()
    input("  Press Enter, read it out loud, then press Enter again... ")
    recorder = Recorder()
    try:
        recorder.start()
    except RecorderError as error:
        print(f"  FAIL: could not open the microphone: {error}")
        return None
    input("  RECORDING - press Enter when you have finished the sentence... ")
    return recorder.stop()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print()
    print("Read each sentence exactly as written -- the words on screen are")
    print("what your speech gets scored against. Speak normally; do not")
    print("over-enunciate, or the score will flatter the app.")

    manifest = []
    for label, sentence, _want in SENTENCES:
        audio = record_one(label, sentence)
        if audio is None:
            return 1
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if audio.size == 0 or peak < 0.02:
            print(f"  WARNING: {label} was near-silent (peak {peak:.4f}); "
                  "it will be skipped in scoring.")
        path = OUT / f"{label}.wav"
        pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm.tobytes())
        manifest.append({"label": label, "sentence": sentence,
                         "wav": path.name,
                         "seconds": round(audio.size / SAMPLE_RATE, 2),
                         "peak": round(peak, 4)})
        print(f"  saved {audio.size / SAMPLE_RATE:.1f}s, peak {peak:.3f}")

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nDone. {len(manifest)} clips in {OUT}")
    print("Now tell Claude, and it will score them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
