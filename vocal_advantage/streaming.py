"""Deciding which words are settled enough to type while someone is still talking.

Whisper is not a streaming model. Re-run it on a growing recording and it
revises what it previously said: "i scream" becomes "ice cream" once it hears
another syllable. That is fine when you transcribe once at the end, and a real
problem when you are typing into someone's document as they speak, because
correcting a word means deleting text they did not type.

So: a word is typed only once two consecutive passes agree on it. This is the
LocalAgreement-2 rule. It costs about half a second of lag behind the voice and
buys never having to un-type anything.

Pure Python. No audio, no model, no OS -- it takes strings and returns the
string to type next, which is what makes it exhaustively testable.
"""

from __future__ import annotations


def _words(text: str) -> list[str]:
    """Split on any whitespace, so spacing can never double up."""
    return text.split()


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    out: list[str] = []
    for left, right in zip(a, b):
        if left != right:
            break
        out.append(left)
    return out


class StreamingTranscript:
    """Tracks what has been typed and what is safe to type next.

    One instance per dictation; call ``reset()`` between them.
    """

    def __init__(self) -> None:
        self._committed: list[str] = []  # words already in the user's document
        self._previous: list[str] = []   # the last hypothesis we were shown

    @property
    def typed_so_far(self) -> str:
        """What the document should contain from this dictation."""
        return " ".join(self._committed)

    def reset(self) -> None:
        self._committed = []
        self._previous = []

    def commit(self, text: str) -> str:
        """Feed a fresh partial transcript; return the text to type now.

        Returns "" when nothing new is settled, which is the common case: most
        passes agree only about words already typed.
        """
        words = _words(text)
        agreed = _common_prefix(self._previous, words)
        self._previous = words
        return self._advance_to(agreed)

    def finish(self, text: str) -> str:
        """Feed the final full transcript; return whatever is still owed.

        Agreement no longer applies -- this is the last word on the matter, so
        anything not yet typed is typed now.
        """
        words = _words(text)
        self._previous = words
        return self._advance_to(words)

    def _advance_to(self, words: list[str]) -> str:
        # Never shrink. If the model's newest answer is shorter than what we
        # already typed, we cannot take those words back: un-typing means
        # sending backspaces into a document we do not own, and getting the
        # count wrong there is far worse than one stale word. Documented cost.
        if len(words) <= len(self._committed):
            return ""
        fresh = words[len(self._committed) :]
        leading = " " if self._committed else ""
        self._committed = words
        return leading + " ".join(fresh)
