"""Per-stage stopwatch for one dictation, and the block it prints.

"It felt slow" is the most common complaint about a dictation app and the
least actionable, because the pipeline has five stages and any one of them can
be the problem. This makes each stage a separate number, printed after every
dictation, so a before/after comparison is a matter of reading two blocks
rather than guessing.

The stages, in the order they happen:

    keypress -> first audio   how long the mic took to hand over its first block
    chunk N                   each rolling ~2s window transcribed while speaking
    final chunk               the un-chunked tail, transcribed on stop
    cleanup                   the filler/AI pass over the stitched whole
    insertion                 clipboard write, paste chord, clipboard restore

Pure Python. The clock is injected, so the tests assert exact milliseconds
instead of sleeping. Nothing here formats anything until ``report()`` is
called, because this runs inside the dictation and must not cost anything
measurable itself.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable


def _ms(seconds: float) -> float:
    return seconds * 1000.0


class Timings:
    """One dictation's stage timings. Not thread-safe; one owner, one thread."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._t0 = 0.0
        self.first_audio_ms: float | None = None
        self.chunk_ms: list[float] = []
        self.final_chunk_ms: float | None = None
        self.cleanup_ms: float | None = None
        self.insertion_ms: float | None = None

    # -- marks ---------------------------------------------------------------

    def start(self) -> None:
        """The key-down edge. Everything else is measured against this."""
        self._t0 = self._clock()

    def first_audio(self) -> None:
        """The first block of audio reached us. Only the first call counts.

        Blocks keep arriving every 64ms for the whole recording; this stage
        ends at the first one, so later calls are ignored rather than
        overwriting it with the time of the last block.
        """
        if self.first_audio_ms is None:
            self.first_audio_ms = _ms(self._clock() - self._t0)

    # -- stages --------------------------------------------------------------

    @contextmanager
    def _stage(self, assign: Callable[[float], None]):
        """Time a block, recording it even if the block raises.

        The ``finally`` is the point. A chunk whose model call fell over is
        exactly the case where the timing block is most worth having, and
        losing the whole report to the same exception would be perverse.
        """
        started = self._clock()
        try:
            yield
        finally:
            assign(_ms(self._clock() - started))

    def chunk(self):
        """Time one rolling chunk. Appends -- each chunk keeps its own number."""
        return self._stage(self.chunk_ms.append)

    def final_chunk(self):
        """Time the tail transcribed on stop, kept apart from the rolling ones."""

        def assign(value: float) -> None:
            self.final_chunk_ms = value

        return self._stage(assign)

    def cleanup(self):
        def assign(value: float) -> None:
            self.cleanup_ms = value

        return self._stage(assign)

    def insertion(self):
        def assign(value: float) -> None:
            self.insertion_ms = value

        return self._stage(assign)

    # -- output --------------------------------------------------------------

    @property
    def total_ms(self) -> float:
        return _ms(self._clock() - self._t0)

    def report(self) -> str:
        """The block printed after every dictation.

        Every stage appears even when it did not run, shown as "-". A missing
        line would be read as "that stage was instant"; the dash says "that
        stage did not happen", which is a different and more useful fact --
        no final chunk at all means the tail was pure silence, for instance.
        """
        lines = ["  [timings]"]

        def row(label: str, value: float | None) -> str:
            shown = "-" if value is None else f"{value:8.1f} ms"
            return f"    {label:<28}{shown:>12}"

        lines.append(row("keypress -> first audio", self.first_audio_ms))

        if self.chunk_ms:
            for index, value in enumerate(self.chunk_ms, start=1):
                lines.append(row(f"chunk {index} transcription", value))
            total = sum(self.chunk_ms)
            lines.append(
                row(f"chunks total ({len(self.chunk_ms)})", total)
            )
        else:
            lines.append(row("chunk transcription", None))

        lines.append(row("final chunk", self.final_chunk_ms))
        lines.append(row("cleanup pass", self.cleanup_ms))
        lines.append(row("insertion", self.insertion_ms))
        lines.append(row("total (keypress -> done)", self.total_ms))
        return "\n".join(lines)
