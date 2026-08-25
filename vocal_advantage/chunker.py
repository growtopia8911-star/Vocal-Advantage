"""A cursor over a growing recording, handing out overlapped windows.

The recording buffer only ever grows while someone is speaking. This tracks how
much of it has already been sent to the model and answers two questions:

* ``ready(buffer)`` -- which complete windows have appeared since last time?
* ``remainder(buffer)`` -- what is left over at the end, for the final pass?

**The overlap is the whole point.** Cut a recording into flush 2s pieces and
every boundary lands mid-word about as often as not; Whisper then hears half a
word at the end of one window and half at the start of the next, and invents
something plausible for both. So each window reaches back ``overlap_s`` into
the one before it, the seam gets transcribed twice, and ``stitch`` throws the
second copy away. Cheap insurance: a quarter-second seam on a two-second window
is an eighth more audio.

Geometry, with chunk_s=2.0 and overlap_s=0.25:

    window 0   [0.00 .. 2.00)      2.00s   (nothing before it to reach into)
    window 1   [1.75 .. 4.00)      2.25s
    window 2   [3.75 .. 6.00)      2.25s
    remainder  [5.75 .. end)

The cursor advances by ``chunk_s`` each time, never by the window length --
otherwise the overlap would compound and the windows would drift backwards.

Pure numpy slicing. No audio device, no model, no clock.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE: int = 16000


class RollingChunker:
    """Hands out fixed-step overlapping windows of a growing buffer."""

    def __init__(
        self,
        chunk_s: float = 2.0,
        overlap_s: float = 0.25,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.step = max(1, int(float(chunk_s) * self.sample_rate))
        # Clamped below the step: an overlap as long as the window would mean
        # every window contained the previous one whole and the cursor would
        # never make progress through the audio.
        self.overlap = max(0, min(int(float(overlap_s) * self.sample_rate),
                                  self.step - 1))
        self._cursor = 0  # samples consumed by windows handed out so far

    def reset(self) -> None:
        """Start the next dictation from the beginning of a fresh buffer."""
        self._cursor = 0

    @property
    def chunks_emitted(self) -> int:
        return self._cursor // self.step

    def _window_start(self) -> int:
        """Where the next window begins: back by the overlap, never below zero."""
        return max(0, self._cursor - self.overlap)

    def ready(self, buffer: np.ndarray) -> list[np.ndarray]:
        """Every complete window that has appeared since the last call.

        Returns a list rather than one window because the caller can fall
        behind -- a slow model pass, a busy controller thread -- and silently
        skipping the audio it slept through would lose words with no error
        anywhere.
        """
        buffer = np.asarray(buffer, dtype=np.float32).reshape(-1)
        # A buffer that shrank means a new recording started under us. Rewind
        # rather than slicing past the end and handing back nothing forever.
        if buffer.size < self._cursor:
            self.reset()

        out: list[np.ndarray] = []
        while self._cursor + self.step <= buffer.size:
            start = self._window_start()
            self._cursor += self.step
            out.append(buffer[start : self._cursor])
        return out

    def remainder(self, buffer: np.ndarray) -> np.ndarray:
        """The tail no window has covered, with the usual overlap at its front.

        This is what spec item 9a sends to the model on stop -- not the whole
        recording. Empty when the windows happened to land exactly on the end.
        """
        buffer = np.asarray(buffer, dtype=np.float32).reshape(-1)
        if buffer.size <= self._cursor:
            return np.empty(0, dtype=np.float32)
        return buffer[self._window_start() :]
