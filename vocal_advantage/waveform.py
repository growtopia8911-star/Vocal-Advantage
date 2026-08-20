"""Audio level in, bar heights out. The motion of the Flow Bar lives here.

Pure maths, no screen, no numpy in the public contract except `block_rms`.
Both renderers -- the macOS `NSPanel` and the Windows layered window -- share
this file, so the pill moves identically on the two machines and the part that
is easy to get subtly wrong is the part that is tested.

**Heights are normalised**: 0.0 is a flat line and 1.0 is the tallest a bar may
draw. Each renderer multiplies by its own half-height, because the bars mirror
about the pill's centre line -- every bar grows *both* up and down by the same
amount. That is the detail that makes it read as a soundwave rather than a bar
chart standing on a baseline, and it is why nothing here returns a y position.

The chain, once per frame:

    recorder.level  ->  level_from_rms  ->  Envelope.update
                    ->  bar_targets     ->  ease_bars  ->  pixels

Two separate smoothings, on purpose, because they fix different problems. The
`Envelope` smooths the *audio*: it has a fast attack so the start of a syllable
is not missed and a slow release so the gaps between words do not make the pill
flicker. `ease_bars` smooths the *drawing*: no bar height is ever assigned
directly, so every visible change is a glide rather than a step.
"""

from __future__ import annotations

import math

import numpy as np

# --- the pill ---------------------------------------------------------------
# Shared by both renderers so the two machines cannot drift apart.
PILL_WIDTH = 84
PILL_HEIGHT = 24
#: Half the height, so the ends are fully round -- a lozenge, not a rounded box.
PILL_RADIUS = PILL_HEIGHT / 2

#: Odd, so there is a true centre bar for the weighting below to peak on.
BAR_COUNT = 9
BAR_WIDTH = 2
BAR_GAP = 4
#: Clearance between the tallest bar and the pill's edge. 9 bars of 2px with
#: 4px gaps is 50px of content, which leaves 17px margins inside an 84px pill.
BAR_MARGIN_Y = 3
#: Pixels above the bottom of the screen (macOS Dock, Windows taskbar).
SCREEN_MARGIN = 64

# --- level mapping ----------------------------------------------------------
#: Ordinary speech sits near -30 dBFS. On a linear scale that is rms 0.03, which
#: would move a 16px bar by half a pixel -- the pill would look broken while you
#: were talking normally into it. Hence dB, and hence a ceiling well below 0.
FLOOR_DB = -60.0
CEIL_DB = -15.0

# --- motion -----------------------------------------------------------------
# Tuned for a 60fps render loop. All three are the feel of the thing; they are
# the first place to reach when it looks wrong.
#: Per-frame easing coefficient. 0.25 is a ~66ms time constant: rest to full
#: scale takes 9 frames, which reads as a glide. Raise it toward 1.0 and the
#: bars snap; drop it toward 0.05 and they wallow.
EASE_ALPHA = 0.25
#: Fast attack, slow release -- see the module docstring.
ATTACK_ALPHA = 0.5
RELEASE_ALPHA = 0.08

#: How much shorter than the centre bar the end bars are at the same level.
#: Not decoration: a flat profile reads as a row of independent meters rather
#: than one wave.
EDGE_WEIGHT = 0.45

#: A small per-bar ripple, so the row is never a perfect arch. Amplitude scales
#: with level, so silence is genuinely still. Subtractive (see `bar_targets`),
#: which keeps the weighted target as the ceiling and means loud speech never
#: clips against the top of the pill.
WOBBLE = 0.22
WOBBLE_SPEED = 0.11     # radians per frame
WOBBLE_PHASE = 1.7      # radians between neighbouring bars

#: Resting height, as a fraction of the 9px half-height: ~6px of visible line.
#: This has a hard floor, not just a taste range. A round-capped bar whose drawn
#: height falls to its own width is a circle, and the resting row reads as a row
#: of dots rather than the short lines the design asks for. Anything below about
#: 0.23 here crosses that line at the current geometry.
IDLE_HEIGHT = 0.32

# --- the transcribing sweep -------------------------------------------------
#: Deliberately capped well below what speech reaches, so this state can never
#: be mistaken for "still listening".
SWEEP_AMPLITUDE = 0.42
#: One pass takes ~1.2s at 60fps, which is about how long a transcribe runs --
#: so you see a whole sweep, not an arbitrary slice of one. Slower than this and
#: the bump spends most of a short transcribe still off the end of the pill,
#: which is indistinguishable from nothing happening.
SWEEP_SPEED = 0.24      # bars per frame
SWEEP_WIDTH = 2.2       # bars, the gaussian's sigma
SWEEP_PAD = 2           # bars of run-off past each end, so it enters and leaves


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def block_rms(block: np.ndarray) -> float:
    """RMS of one block of float32 samples. Never raises, never returns nan.

    The nan guard is not theoretical: a stream whose device was unplugged
    mid-block can deliver them, and a single nan reaching `ease_bars` would
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


class Envelope:
    """Smooths the audio level: quick to rise, slow to fall.

    Deliberately not a plain rolling average. A symmetric filter either misses
    the attack of a syllable (too slow) or flickers in the gaps between words
    (too fast); there is no setting that does both. Asymmetric coefficients do.
    """

    def __init__(
        self, attack: float = ATTACK_ALPHA, release: float = RELEASE_ALPHA
    ) -> None:
        self._attack = attack
        self._release = release
        self._value = 0.0

    @property
    def value(self) -> float:
        return self._value

    def update(self, level: float) -> float:
        if not math.isfinite(level):
            level = 0.0
        alpha = self._attack if level > self._value else self._release
        self._value += (level - self._value) * alpha
        return self._value


def centre_weights(n: int, edge: float = EDGE_WEIGHT) -> tuple[float, ...]:
    """A symmetric hump: 1.0 at the centre bar, `edge` at the two ends.

    A quarter-cosine rather than a triangle, so the falloff has no corner in it
    at the centre -- a corner is visible as a kink in the row when the level is
    high.
    """
    if n <= 1:
        return (1.0,)
    centre = (n - 1) / 2.0
    return tuple(
        edge + (1.0 - edge) * math.cos(math.pi / 2 * abs(i - centre) / centre)
        for i in range(n)
    )


def bar_targets(
    level: float,
    n: int,
    tick: int,
    *,
    wobble: float = WOBBLE,
    edge: float = EDGE_WEIGHT,
) -> tuple[float, ...]:
    """Where each bar wants to be, for one audio level at one frame.

    Targets only -- `ease_bars` is what actually moves anything.

    At `level` 0 this returns exactly `idle_heights(n)`, whatever the tick, so
    the pauses between sentences are genuinely still and recording-at-silence
    looks identical to idle. That is what stops the pill twitching at you while
    you think of the next word.
    """
    level = _clamp(level)
    weights = centre_weights(n, edge)
    heights = []
    for i, weight in enumerate(weights):
        # Subtractive, spanning [1 - wobble, 1.0]: the weighted height stays the
        # ceiling, so a shout never clips flat against the top of the pill.
        ripple = 1.0 - wobble * (
            0.5 + 0.5 * math.sin(tick * WOBBLE_SPEED + i * WOBBLE_PHASE)
        )
        height = IDLE_HEIGHT + (1.0 - IDLE_HEIGHT) * level * weight * ripple
        heights.append(_clamp(height, IDLE_HEIGHT, 1.0))
    return tuple(heights)


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
