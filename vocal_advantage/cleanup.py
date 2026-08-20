"""Removing filler words and stutters from a transcript before it is typed.

Whisper already punctuates and capitalises well -- Kevin's "um so I I think we
should uh ship it on friday" came back as "Um, so I think we should ship it on
Friday.", stutter already resolved. What it does not do is drop the filler.
That is the entire job of this module, and it is why there is no model here: a
0.5B local model was measured against this and added only the punctuation
Whisper already supplies, for 276 MB and 0.2s.

It also has to be this cheap. Cleaning runs on every live pass, before the
agreement logic sees the text, so that the cleaned words are the only words
that ever reach the document. Cleaning afterwards would put the streaming
module's idea of the document out of step with the real one.

Pure Python: no audio, no model, no OS. Portable to the PC unchanged.
"""

from __future__ import annotations

import re

#: Whole words only. Substring matching would eat "umbrella" and "another",
#: and near-misses like "hmmm" are usually a real noise of assent rather than
#: hesitation, so they are deliberately absent.
FILLERS: frozenset[str] = frozenset(
    {"um", "uh", "er", "erm", "ah", "hmm", "mm", "mhm", "umm", "uhh", "eh"}
)

#: Leading/trailing punctuation, stripped to compare a token to FILLERS.
#: Apostrophes stay, so "couldn't" compares as itself.
_EDGE_PUNCTUATION = re.compile(r"^[^\w']+|[^\w']+$")


def _bare(token: str) -> str:
    """A token reduced to its word, for comparison only."""
    return _EDGE_PUNCTUATION.sub("", token).lower()


def _pick_from_run(run: list[str]) -> str:
    """One survivor from a run of the same repeated word.

    Prefer a token carrying no punctuation, so "I, I think" becomes "I think"
    rather than "I, think". Failing that the last one wins, because it is the
    one the speaker carried on from.
    """
    for token in run:
        if _bare(token) == token.lower():
            return token
    return run[-1]


def clean_speech(text: str) -> str:
    """Filler words and stutters removed; nothing else touched.

    Removal and capitalisation only. No word is ever substituted or invented,
    which is what makes this safe to run unattended on someone's dictation.
    """
    tokens = text.split()
    if not tokens:
        return ""

    kept: list[str] = []
    run: list[str] = []

    def flush_run() -> None:
        if run:
            kept.append(_pick_from_run(run))
            run.clear()

    for token in tokens:
        bare = _bare(token)
        if bare in FILLERS:
            continue
        if run and _bare(run[0]) == bare:
            run.append(token)   # still inside a stutter
            continue
        flush_run()
        run.append(token)
    flush_run()

    if not kept:
        return ""

    # Only recapitalise when the sentence lost its opening word; otherwise a
    # mid-sentence removal would start shouting at the reader.
    if kept[0] != tokens[0] and kept[0][:1].islower():
        kept[0] = kept[0][:1].upper() + kept[0][1:]

    return " ".join(kept)
