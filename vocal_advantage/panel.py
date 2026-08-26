"""The Flow Bar panel's layout arithmetic, and nothing else.

This module draws nothing and imports no GUI library. That is deliberate and
load-bearing. The Flow Bar is rendered twice -- AppKit on macOS, Pillow and
Win32 on Windows -- and the two implementations drifted apart once already:
`PILL_FILL_RGB` is a float triple in one file and an int triple in the other,
and nothing stops them disagreeing. Every rect and every colour lives here, so
the platforms cannot say different things about the same panel, and so the
layout can be tested on whichever machine happens to be in front of you.

Coordinates are **top-left origin, y increasing downward**. That is Pillow's
convention already; the AppKit view sets `isFlipped` to adopt it.

Every measurement is taken from the native-resolution captures in
`design-research/superwhisper/assets/`, halved from a 2x capture. They are
observations, not preferences -- see docs/plans/2026-08-25-flow-bar-panel.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """A rectangle in panel space. Top-left origin, y down."""

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


# --- geometry ---------------------------------------------------------------
#: Superwhisper's is 600 x 126, 42% of a 1440pt screen. 420 is 29%: the same
#: object with less of your desk. See the spec's "Two departures from exact".
PANEL_WIDTH = 420.0
PANEL_HEIGHT = 96.0
#: A rounded rectangle, NOT a lozenge. The pill is full-round at 15; the two
#: eased together are what make the grow read as one object changing shape.
PANEL_RADIUS = 12.0
PILL_RADIUS = 15.0

#: 57 + 1 + 38 = 96, and the assertion in the tests is not pedantry: a half
#: point of slack here draws as a seam between the bands.
#:
#: Superwhisper's proportions are 80 / 1 / 46, a 1.76:1 band-to-strip ratio.
#: Ours is 1.5:1 -- the strip keeps a legible height for 13pt text instead of
#: scaling down with the band, and the band takes the loss. A proportionally
#: faithful 420pt panel would need 9pt strip text.
BAND_HEIGHT = 57.0
HAIRLINE = 1.0
STRIP_HEIGHT = 38.0

# --- palette ----------------------------------------------------------------
# 0-255 integer triples. AppKit callers divide by 255. Kept in one
# representation so the two renderers cannot hold different values.
#
# NOTHING HERE IS A FLAT FILL. Both bands are vertical gradients, and that is
# most of why the real panel does not look like a rectangle of paint.
BAND_TOP_RGB = (24, 24, 24)
BAND_BOTTOM_RGB = (1, 1, 1)
STRIP_TOP_RGB = (46, 47, 47)
STRIP_BOTTOM_RGB = (35, 36, 36)

#: Brighter than either band it separates, so it reads as a drawn line rather
#: than as the seam between two fills.
HAIRLINE_RGB = (99, 100, 100)
#: Measured at (83,83,83) along the top and (116,118,118) down the sides. One
#: value is used for all four edges: the difference is the desktop showing
#: through an antialiased curve, not a deliberate gradient.
BORDER_RGB = (83, 83, 83)

BAR_RGB = (213, 213, 213)
TEXT_RGB = (175, 176, 176)
#: Darker than the strip, so a key cap recedes into it. Every instinct says a
#: chip should be lighter than its ground; this one is not, and copying it
#: faithfully is most of why their strip looks calm.
CAP_FILL_RGB = (31, 32, 32)
#: The resting pill. Near-black, replacing the warm paper ground.
PILL_FILL_RGB = (11, 11, 11)
#: Filled behind a strip item under the cursor. The only affordance there is:
#: nothing has a border at rest.
HOVER_FILL_RGB = (58, 59, 59)

#: Apple's system red, which is what they use. Recording is the one state that
#: gets a colour everyone already reads as "live".
DOT_RECORDING_RGB = (255, 69, 58)
DOT_TRANSCRIBING_RGB = (50, 121, 192)


def lerp(a: float, b: float, t: float) -> float:
    """Linear blend. `t` is not clamped: callers clamp before calling."""
    return a + (b - a) * t


def bands(width: float, height: float) -> tuple[Rect, Rect, Rect]:
    """The waveform band, the hairline, and the control strip.

    The strip and hairline keep their fixed heights and the band absorbs the
    remainder, so a part-grown panel still has its strip sitting on the bottom
    edge instead of floating in the middle of the animation.
    """
    strip_h = min(STRIP_HEIGHT, max(0.0, height - HAIRLINE))
    hairline_h = min(HAIRLINE, max(0.0, height - strip_h))
    band_h = max(0.0, height - strip_h - hairline_h)
    return (
        Rect(0.0, 0.0, width, band_h),
        Rect(0.0, band_h, width, hairline_h),
        Rect(0.0, band_h + hairline_h, width, strip_h),
    )
