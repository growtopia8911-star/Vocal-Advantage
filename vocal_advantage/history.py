"""A record of what was dictated, so a lost transcript is recoverable.

The failure this exists for: you dictate a long paragraph, the paste lands in
the wrong window or the window closes, and the words are gone. The transcript
existed for a moment and nothing kept it.

Appended as JSON Lines to ``logs/history.jsonl`` -- one self-contained object
per line, which is what makes appending safe. A JSON array would have to be
read, parsed, extended and rewritten every time, so an interrupted write could
corrupt every previous entry rather than just the last line.

**This file contains everything you have ever dictated.** It is gitignored,
never leaves the machine, and can be switched off with ``"history": false``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .console import warn

HISTORY_PATH: Path = (
    Path(__file__).resolve().parent.parent / "logs" / "history.jsonl"
)

#: Lines kept. About a month of heavy use, and small enough that the trim below
#: stays cheap.
DEFAULT_KEEP = 2000


def entry(text: str, when: datetime, app: str | None = None,
          seconds: float | None = None) -> str:
    """One history line, without a trailing newline.

    The timestamp goes in as ISO 8601 local time with an offset. Not a Unix
    epoch: this file is meant to be readable by a person scrolling it looking
    for something they lost, and a bare number is not.
    """
    record = {"at": when.astimezone().isoformat(timespec="seconds"), "text": text}
    if app:
        record["app"] = app
    if seconds is not None:
        record["seconds"] = round(float(seconds), 2)
    # ensure_ascii=False so dictated accents stay readable in the file rather
    # than becoming \u escapes.
    return json.dumps(record, ensure_ascii=False)


def trimmed(lines, keep: int = DEFAULT_KEEP) -> list[str]:
    """The last `keep` lines. Newest are kept; oldest fall off."""
    if keep <= 0:
        return []
    return list(lines)[-keep:]


class History:
    """Appends dictations to disk. Never raises; never blocks on failure."""

    def __init__(self, path: Path = HISTORY_PATH, enabled: bool = True,
                 keep: int = DEFAULT_KEEP) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.keep = keep
        self._since_trim = 0
        self._warned = False

    def record(self, text: str, app: str | None = None,
               seconds: float | None = None, when: datetime | None = None) -> None:
        """Append one dictation. Silent no-op when switched off or empty."""
        if not self.enabled or not text or not text.strip():
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = entry(text, when or datetime.now(), app, seconds)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._maybe_trim()
        except Exception:  # noqa: BLE001 - a full disk is not a lost dictation
            # Once only: this runs on every dictation, and a read-only folder
            # would otherwise fill the console with the same line forever.
            if not self._warned:
                self._warned = True
                warn(f"Could not write {self.path}; history is off for this run.")
                self.enabled = False

    def _maybe_trim(self) -> None:
        """Rewrite the file occasionally, not on every line.

        Trimming reads the whole file, so doing it per dictation would make
        every dictation slower as the history grew -- the cost would creep up
        for weeks before anyone connected it to this.
        """
        self._since_trim += 1
        if self._since_trim < 200:
            return
        self._since_trim = 0
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= self.keep:
            return
        # Written to a neighbour and moved into place: an interrupted rewrite
        # of the real file would take the whole history with it.
        temporary = self.path.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "\n".join(trimmed(lines, self.keep)) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)
