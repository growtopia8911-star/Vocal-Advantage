"""Audio level in, bar heights out. The motion of the Flow Bar lives here.

Pure maths, no screen, no numpy in the public contract except `block_rms`.
Both renderers -- the macOS `NSPanel` and the Windows layered window -- share
this file, so the pill moves identically on the two machines and the part that
is easy to get subtly wrong is the part that is tested.

**The bars are a scrolling history, not a row of meters.** New audio enters at
the left-hand edge; every bar then shifts one place right and the oldest drops
off. What you see is the last second and a half of your voice travelling across
the pill, like a seismograph trace. A bar's height is simply the level at the
moment it was captured -- once captured it never changes again, it only moves.

**Heights are normalised**: 0.0 is a flat line and 1.0 is the tallest a bar may
draw. Each renderer multiplies by its own half-height, because the bars mirror
about the pill's centre line -- every bar grows *both* up and down by the same
amount. That is what makes it read as a soundwave rather than a bar chart
standing on a baseline, and it is why nothing here returns a y position.

The chain, once per frame:

    recorder.level  ->  level_from_rms  ->  ScrollingWave.update  ->  pixels

There is no centre-weighting and no synthetic wobble any more. Both existed to
fake the shape of a voice back when every bar showed the same instant; a real
history of real levels supplies that shape for free, and simulating it on top
would only fight it.
"""

from __future__ import annotations

import math

import numpy as np

# --- the pill ---------------------------------------------------------------
# Shared by both renderers so the two machines cannot drift apart.
PILL_WIDTH = 78
PILL_HEIGHT = 30
#: Half the height, so the ends are fully round -- a lozenge, not a rounded box.
PILL_RADIUS = PILL_HEIGHT / 2

#: How many bars the *resting pill* shows. The buffer is longer; see
#: BUFFER_BARS.
BAR_COUNT = 15
#: Bar and gap are equal, which is what makes a bar-style waveform read as a
#: hi-fi VU meter rather than a chart. Measured off superwhisper at 2.0/2.0
#: (4px each in a 2x capture); ours were 1.5/2.2, a 1.47:1 ratio that looked
#: airier and less like the thing being copied.
BAR_WIDTH = 2.0
BAR_GAP = 2.0
#: The full trace history, in bars. The panel draws all of them; the pill draws
#: a window onto the newest few, so growing the panel *reveals* history rather
#: than resetting the trace.
#:
#: 69 bars at a 4.0 pitch is 274pt of content, 65% of the 420pt panel -- against
#: superwhisper's measured ~66%. At SCROLL_FRAMES = 6 and 60fps that is 6.9
#: seconds of visible history, up from 1.5.
BUFFER_BARS = 69
#: Clearance between the tallest bar and the pill's edge. 15 bars of 2.0 with
#: 2.0 gaps is 58pt of content, which leaves 10pt margins inside a 78pt pill.
#: Those margins are load-bearing, not slack: the ends are fully round, so a bar
#: pushed much closer to the edge sits under the curve of the cap and clips
#: against it when the level is high.
BAR_MARGIN_Y = 3.5
#: Pixels above the bottom of the screen (macOS Dock, Windows taskbar).
SCREEN_MARGIN = 64

# --- level mapping ----------------------------------------------------------
#: Ordinary speech sits near -30 dBFS. On a linear scale that is rms 0.03, which
#: would move an 11px bar by half a pixel -- the pill would look broken while you
#: were talking normally into it. Hence dB, and hence a ceiling well below 0.
FLOOR_DB = -60.0
CEIL_DB = -15.0

# --- motion -----------------------------------------------------------------
#: Per-frame easing coefficient, for a 60fps render loop. 0.25 is a ~66ms time
#: constant: a bar entering at the left reaches full height in 9 frames, which
#: reads as a glide. Raise it toward 1.0 and new bars pop in at full height;
#: drop it toward 0.05 and the trace arrives blurred.
EASE_ALPHA = 0.25

#: Frames between shifts, and the one number that sets how much history the pill
#: holds: BAR_COUNT * SCROLL_FRAMES / fps seconds, so 15 * 6 / 60 = 1.5s.
#:
#: Deliberately NOT one shift per rendered frame. At 60fps that scrolls the
#: whole pill in a quarter of a second, which is a blur rather than a trace --
#: "the last second or two" is the half of the brief that governs here.
SCROLL_FRAMES = 6

#: Resting height, as a fraction of the band's half-height.
#:
#: **This used to be 0.32, chosen so the resting row drew as short LINES, and
#: the comment here warned against exactly what it now does.** Wispr Flow rests
#: as a row of dots and Kevin prefers it, so the old floor is deliberately
#: crossed rather than accidentally: a round-capped bar drawn no taller than it
#: is wide IS a circle, which is the whole mechanism.
#:
#: 0.10 against the compact pill's 9.66pt half-height gives a drawn height of
#: ~1.9pt against a 2.0pt bar width -- just under, so every bar the microphone
#: is not driving renders as a dot. Speech lifts them back into lines, so the
#: trace reads as dots at rest and a waveform while you talk.
IDLE_HEIGHT = 0.10

# --- the transcribing sweep -------------------------------------------------
#: Deliberately capped well below what speech reaches, so this state can never
#: be mistaken for "still listening".
SWEEP_AMPLITUDE = 0.42
#: One pass takes ~1.2s at 60fps, which is about how long a transcribe runs --
#: so you see a whole sweep, not an arbitrary slice of one.
SWEEP_SPEED = 0.24      # bars per frame
SWEEP_WIDTH = 2.2       # bars, the gaussian's sigma
SWEEP_PAD = 2           # bars of run-off past each end, so it enters and leaves


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def block_rms(block: np.ndarray) -> float:
    """RMS of one block of float32 samples. Never raises, never returns nan.

    The nan guard is not theoretical: a stream whose device was unplugged
    mid-block can deliver them, and a single nan reaching the easing would
    propagate into every bar height and stay there for the life of the process,
    because nan never converges back out of an exponential ease.
    """
    if block.size == 0:
        return 0.0
    # float64 for the square: a float32 mean of squares near full scale loses
    # precision, and this is cheap on 1024 samples.
    mean_square = float(np.mean(np.square(np.asarray(block, dtype=np.float64))))
    if not math.isfinite(mean_square) or mean_square <= 0.0:
        return 0.0
    return math.sqrt(mean_square)


def level_from_rms(
    rms: float, floor_db: float = FLOOR_DB, ceil_db: float = CEIL_DB
) -> float:
    """RMS -> 0..1, mapped in dB and clamped at both ends."""
    if not math.isfinite(rms) or rms <= 0.0:
        return 0.0
    decibels = 20.0 * math.log10(rms)
    return _clamp((decibels - floor_db) / (ceil_db - floor_db))


def ease_bars(
    current: tuple[float, ...], targets: tuple[float, ...], alpha: float
) -> tuple[float, ...]:
    """One easing step of every bar toward its target.

    The length check is not pedantry: `zip` would silently truncate to the
    shorter sequence, bars would vanish off the end of the pill, and it would
    look like a rendering fault rather than the wiring fault it is.
    """
    if len(current) != len(targets):
        raise ValueError(
            f"cannot ease {len(current)} bars toward {len(targets)} targets"
        )
    return tuple(c + (t - c) * alpha for c, t in zip(current, targets))


class ScrollingWave:
    """A fixed-length history of levels travelling rightward across the pill.

    Two arrays that shift together, which is the whole design:

    * ``_targets`` -- the level each bar captured. Fixed at capture: a bar's
      height is a fact about a past moment, and nothing later may revise it.
    * ``_heights`` -- what is actually drawn, easing toward its own target.

    Because both shift, the eased value travels *with* its bar. One entering at
    the left starts at zero and glides up to what it captured, so nothing pops
    in at full height, while every older bar has already arrived and simply
    moves. Easing per fixed screen position instead would pull each bar's value
    toward its neighbour's every frame and smear the trace into mush.

    Between shifts the incoming level is peak-held rather than sampled. At 60fps
    a six-frame gap spans only about one and a half audio blocks, so taking
    whichever frame happened to land on the boundary would drop the loudest part
    of a short consonant entirely.
    """

    def __init__(
        self,
        n: int = BAR_COUNT,
        scroll_frames: int = SCROLL_FRAMES,
        ease: float = EASE_ALPHA,
        floor: float = IDLE_HEIGHT,
    ) -> None:
        self._n = n
        self._scroll_frames = max(1, int(scroll_frames))
        self._ease = ease
        self._floor = floor
        self._targets = (0.0,) * n
        self._heights = (0.0,) * n
        self._frames = 0
        self._pending = 0.0

    def update(self, level: float) -> tuple[float, ...]:
        """Advance one rendered frame. Returns heights to draw, left to right.

        Index 0 is the newest audio and the last index is the oldest, because
        the trace enters on the left and ages toward the right.
        """
        if not math.isfinite(level):
            level = 0.0
        level = _clamp(level)

        self._pending = max(self._pending, level)
        self._frames += 1
        if self._frames >= self._scroll_frames:
            self._frames = 0
            self._shift(self._pending)
            self._pending = 0.0

        self._heights = ease_bars(self._heights, self._targets, self._ease)
        # Floored at output rather than in the stored heights: the resting row
        # of dashes is a drawing decision, and baking it into the history would
        # make silence indistinguishable from a genuinely quiet syllable, and
        # would stop the wave ever draining fully back to rest.
        return tuple(max(self._floor, height) for height in self._heights)

    def _shift(self, level: float) -> None:
        """Admit a new bar at the left; drop the oldest off the right."""
        self._targets = (level,) + self._targets[:-1]
        self._heights = (0.0,) + self._heights[:-1]


def idle_heights(n: int) -> tuple[float, ...]:
    """The resting row: even, short, and completely still."""
    return (IDLE_HEIGHT,) * n


def transcribing_heights(n: int, tick: int) -> tuple[float, ...]:
    """A single soft bump travelling along the row. Deterministic in `tick`.

    The third state has one job: say "heard you, working on it" without looking
    like it is still listening. So it moves (idle does not) but stays capped
    around half height (speech does not), and only one bump is ever in flight.
    """
    period = n + 2 * SWEEP_PAD
    position = (tick * SWEEP_SPEED) % period - SWEEP_PAD
    return tuple(
        _clamp(
            IDLE_HEIGHT
            + SWEEP_AMPLITUDE * math.exp(-(((i - position) / SWEEP_WIDTH) ** 2)),
            IDLE_HEIGHT,
            1.0,
        )
        for i in range(n)
    )


def bar_layout(
    width: float, n: int, bar_width: float = BAR_WIDTH, gap: float = BAR_GAP
) -> tuple[float, ...]:
    """The x centre of each bar, centred as a group inside `width`."""
    if n <= 0:
        return ()
    content = n * bar_width + (n - 1) * gap
    left = (width - content) / 2.0
    return tuple(
        left + bar_width / 2.0 + i * (bar_width + gap) for i in range(n)
    )
