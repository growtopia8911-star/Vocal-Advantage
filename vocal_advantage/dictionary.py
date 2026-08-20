"""The personal dictionary: names and jargon Whisper keeps getting wrong.

Two halves, working at opposite ends of the pipeline, because neither is enough
on its own:

* **words** go to Whisper as *hotwords* before it transcribes, so it leans
  toward hearing them in the first place. This is the better fix when it lands,
  because nothing is rewritten afterwards and a word you really said can never
  be corrupted. But it is a nudge, not a guarantee.
* **fixes** are applied to the finished text, catching whatever still slipped
  through. Applied **last** in the pipeline -- after filler removal and after
  the optional AI pass -- so nothing downstream can quietly undo a correction.
  An AI cleanup pass rewriting "Kevin" back to "Kelvin" is exactly the failure
  this ordering rules out.

`dictionary.json` lives beside `config.json` and is its own file on purpose: it
is a list that grows, not a setting, and letting it grow inside `config.json`
would bury the twelve lines of settings that are actually read there.

**A broken dictionary must never stop dictation.** It is an accuracy aid, so
every failure here warns and falls back to doing nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .console import warn

#: Beside config.json, and gitignored for the same reason: it is personal.
DICTIONARY_PATH: Path = Path(__file__).resolve().parent.parent / "dictionary.json"

#: JSON cannot carry comments and this file is meant to be hand-edited, so the
#: explanation ships as a key. Ignored on load.
_HELP = (
    "words: terms to nudge Whisper toward hearing, e.g. names and jargon. "
    "fixes: wrong -> right, applied to the finished text as a last resort. "
    "Matching ignores case and respects word boundaries."
)

_EMPTY = {"_help": _HELP, "words": [], "fixes": {}}


@dataclass(frozen=True)
class Dictionary:
    """What the app knows about your vocabulary."""

    words: list[str] = field(default_factory=list)
    fixes: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """False when there is nothing to do, so callers can skip the work."""
        return bool(self.words or self.fixes)

    @property
    def hotwords(self) -> str:
        return hotword_text(self.words)

    def apply(self, text: str) -> str:
        return apply_fixes(text, self.fixes)


def hotword_text(words) -> str:
    """The words as the single string faster-whisper wants.

    Empty rather than None when there is nothing: "" is the documented way to
    say "no hotwords", and it goes straight into the transcribe call.
    """
    seen: dict[str, None] = {}
    for word in words:
        cleaned = str(word).strip()
        # dict preserves insertion order, so this de-duplicates without
        # disturbing the order the file was written in.
        if cleaned:
            seen.setdefault(cleaned, None)
    return ", ".join(seen)


def _case_matched(matched: str, replacement: str) -> str:
    """Give the replacement the capitalisation the mistake was wearing.

    Whisper capitalises after a full stop, so a mistake at the start of a
    sentence arrives capitalised; substituting a lower-case correction there
    would break the sentence it begins.
    """
    if len(matched) > 1 and matched.isupper():
        return replacement.upper()
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    # Otherwise the replacement keeps the capitals it was written with, which
    # is what makes a name in the dictionary come out as a name.
    return replacement


def _boundary_pattern(key: str) -> str:
    r"""`key`, escaped, with word boundaries only where they can apply.

    A plain ``\b`` on both ends is wrong for entries like "c++": ``\b`` needs a
    word character on the inside, and "+" is not one, so the pattern could
    never match. Anchoring only the ends that begin or end with a word
    character keeps "kelvin" from matching inside "kelvinator" while leaving
    punctuation-heavy entries usable.
    """
    escaped = re.escape(key)
    start = r"\b" if key[:1].isalnum() or key[:1] == "_" else ""
    end = r"\b" if key[-1:].isalnum() or key[-1:] == "_" else ""
    return f"{start}{escaped}{end}"


def apply_fixes(text: str, fixes: dict) -> str:
    """Correct every known mistake, in one pass over the original.

    One pass matters. Chaining -- letting a replacement be re-matched by another
    rule -- would make the result depend on dictionary ordering and could loop
    forever on a cycle like a->b, b->a.

    Longest key first, so a multi-word entry wins over a single word inside it;
    otherwise "vocal" fires inside "vocal advantaged" and the longer rule can
    never match.
    """
    usable = {k: v for k, v in fixes.items() if isinstance(k, str) and k}
    if not text or not usable:
        return text

    ordered = sorted(usable, key=len, reverse=True)
    pattern = re.compile(
        "|".join(_boundary_pattern(key) for key in ordered), re.IGNORECASE
    )
    # Keyed by lower-case, because the match came back case-insensitively.
    lookup = {key.lower(): str(usable[key]) for key in ordered}

    def substitute(match: re.Match) -> str:
        matched = match.group(0)
        replacement = lookup.get(matched.lower())
        if replacement is None:  # pragma: no cover - defensive
            return matched
        return _case_matched(matched, replacement)

    return pattern.sub(substitute, text)


def _checked_words(value, path: Path) -> list[str]:
    if isinstance(value, list) and all(isinstance(w, str) for w in value):
        return list(value)
    warn(
        f"WARNING: {path}: 'words' should be a list of text, not {value!r}. "
        f"Ignoring it for this run."
    )
    return []


def _checked_fixes(value, path: Path) -> dict[str, str]:
    if isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        return dict(value)
    warn(
        f"WARNING: {path}: 'fixes' should be a mapping of text to text, not "
        f"{value!r}. Ignoring it for this run."
    )
    return {}


def load_dictionary(path: Path = DICTIONARY_PATH) -> Dictionary:
    """Read the dictionary, creating an empty one on first run.

    Never raises. A dictionary that cannot be read warns and comes back empty,
    and the file is left exactly as typed so the mistake is still visible --
    the same contract `config.py` keeps for a bad hotkey.
    """
    if not path.exists():
        try:
            path.write_text(json.dumps(_EMPTY, indent=2) + "\n", encoding="utf-8")
        except Exception:  # noqa: BLE001 - a read-only folder is not fatal
            warn(f"Could not create {path}; the personal dictionary is off.")
        return Dictionary()

    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        warn(
            f"WARNING: {path} could not be read ({error}). Dictation will work; "
            f"the personal dictionary is off for this run."
        )
        return Dictionary()

    if not isinstance(stored, dict):
        warn(
            f"WARNING: {path} should contain an object with 'words' and "
            f"'fixes'. The personal dictionary is off for this run."
        )
        return Dictionary()

    words = _checked_words(stored["words"], path) if "words" in stored else []
    fixes = _checked_fixes(stored["fixes"], path) if "fixes" in stored else {}
    return Dictionary(words=words, fixes=fixes)
