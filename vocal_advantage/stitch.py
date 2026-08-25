"""Joining the transcripts of overlapping windows back into one sentence.

``chunker`` re-transcribes a short seam between windows so no word is lost at a
boundary. That means the words in the seam come back twice, and this removes
the second copy by finding the longest suffix of what we have that matches a
prefix of what just arrived.

**Comparison is loose, output is not.** Whisper repunctuates and recapitalises
between passes -- the same audio yields "brown fox." at the end of one window
and "Fox jumps" at the start of the next -- so the *match* ignores case and
punctuation. What gets kept is the new chunk's own spelling, because it had
more audio after those words and is the better-informed guess.

The longest match wins. A shorter one is always available (a single "the" will
match almost anywhere) and taking it would leave a stutter in the text.

Pure strings. No audio, no model, no clock.
"""

from __future__ import annotations

import re

#: How far back to look for the seam. The overlap is a fraction of a second, so
#: a dozen words is generous; searching the whole transcript would let a phrase
#: repeated legitimately a paragraph earlier swallow everything in between.
MAX_OVERLAP_WORDS: int = 12

_STRIP = re.compile(r"[^\w']+", re.UNICODE)


def _key(word: str) -> str:
    """The form two words are compared in: lowercase, no surrounding punctuation."""
    return _STRIP.sub("", word.lower())


def _keys(words: list[str]) -> list[str]:
    return [_key(w) for w in words]


def join_overlapping(
    previous: str, addition: str, max_overlap_words: int = MAX_OVERLAP_WORDS
) -> str:
    """Append ``addition`` to ``previous``, dropping the duplicated seam.

    Both sides are whitespace-normalised, so no join can produce a double
    space no matter how the model spaced its output.
    """
    previous_words = previous.split()
    addition_words = addition.split()
    if not addition_words:
        return " ".join(previous_words)
    if not previous_words:
        return " ".join(addition_words)

    previous_keys = _keys(previous_words)
    addition_keys = _keys(addition_words)

    limit = min(max_overlap_words, len(previous_words), len(addition_words))
    for length in range(limit, 0, -1):  # longest match first
        if previous_keys[-length:] == addition_keys[:length]:
            # Keep the previous side's copy of the seam and drop the new one.
            # Either would read correctly; this way the text already counted as
            # settled never changes retrospectively.
            return " ".join(previous_words + addition_words[length:])

    return " ".join(previous_words + addition_words)


def stitch_all(parts, max_overlap_words: int = MAX_OVERLAP_WORDS) -> str:
    """Fold every chunk transcript together, in order.

    Empty and whitespace-only parts are skipped rather than joined: a silent
    window is a normal event (a pause mid-sentence) and must not leave a gap in
    the text.
    """
    out = ""
    for part in parts:
        if not part or not part.strip():
            continue
        out = join_overlapping(out, part, max_overlap_words)
    return out
