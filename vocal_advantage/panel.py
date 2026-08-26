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

from vocal_advantage import waveform as wf


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


def bands(width: float, height: float, open_: float) -> tuple[Rect, Rect, Rect]:
    """The waveform band, the hairline, and the control strip.

    The strip and hairline scale with `open_` rather than holding a fixed
    height. The panel grows up from a 30pt pill that is *all* waveform --
    fixed-height bands starved that pill of a band entirely (0pt of it left
    once a 38pt strip and a 1pt hairline were subtracted from a 30pt height),
    which drew the resting pill as an empty lozenge, and it held the strip at
    full height from the first frame of every grow, when the design calls for
    the strip to fade in as the panel opens rather than arrive pre-built.

    `open_` is clamped to 0..1. At 0 the band takes the whole height and the
    strip and hairline vanish -- the pill is nothing but its waveform. At 1
    this reproduces the fixed 57/1/38 split exactly, so a fully open panel is
    unchanged from before this fix.
    """
    t = 0.0 if open_ < 0.0 else 1.0 if open_ > 1.0 else open_
    strip_h = STRIP_HEIGHT * t
    hairline_h = HAIRLINE * t
    band_h = max(0.0, height - strip_h - hairline_h)
    return (
        Rect(0.0, 0.0, width, band_h),
        Rect(0.0, band_h, width, hairline_h),
        Rect(0.0, band_h + hairline_h, width, strip_h),
    )


# --- strip metrics ----------------------------------------------------------
#: From each end of the strip. Comfortably inside the panel's corner radius.
STRIP_PAD_X = 14.0
DOT_DIAMETER = 8.0
#: Between the dot and the state word.
DOT_GAP = 8.0

LABEL_FONT_SIZE = 13.0
CAP_FONT_SIZE = 11.0
CAP_RADIUS = 6.0
CAP_HEIGHT = 20.0
#: Left and right of a key cap's text, inside the chip.
CAP_PAD_X = 6.0
#: Between a label and its own cap. Small: they are one control.
ITEM_GAP = 6.0
#: Around the divider, between the two controls.
GROUP_GAP = 10.0
DIVIDER_WIDTH = 1.0
DIVIDER_HEIGHT = 14.0
#: Padding around a label+cap pair, which is what the hover pill fills.
HOVER_PAD_X = 8.0
HOVER_HEIGHT = 26.0

#: Rough advance width per character, in points. This module cannot measure
#: text -- it has no font and no drawing context, which is the whole point of
#: it -- so the widths are estimated, exactly as MESSAGE_CHAR_WIDTH already is.
#:
#: **Deliberately over-estimates.** `flowbar.LEGEND_CHAR_WIDTH` learned this
#: the expensive way: an under-estimate silently clipped "F8 stops - esc
#: cancels" to "F8 stops -" at the pill's edge, because a renderer cannot widen
#: anything at draw time. A few points of slack shows as a slightly wider gap,
#: which nobody can see.
LABEL_CHAR_WIDTH = 7.6
CAP_CHAR_WIDTH = 7.0


@dataclass(frozen=True)
class StripItem:
    """One control in the strip: a label beside its own keyboard shortcut.

    `cap` may be empty, for an item that is not bound to a key.
    """

    id: str
    label: str
    cap: str


@dataclass(frozen=True)
class Placed:
    """A StripItem with its rects worked out."""

    id: str
    #: What the hover pill fills, and what hit_test matches against. Wraps the
    #: label and the cap together, because they are one control.
    hover_rect: Rect
    label: str
    label_rect: Rect
    cap: str
    cap_rect: Rect | None


@dataclass(frozen=True)
class Layout:
    width: float
    height: float
    radius: float
    band: Rect
    hairline: Rect
    strip: Rect
    dot: Rect | None
    state_label: str
    state_rect: Rect | None
    items: tuple[Placed, ...]
    divider: Rect | None


def bars_for_open(open_: float) -> int:
    """How many of the buffer's newest bars to draw at this openness.

    Interpolated rather than switched, so the trace gains bars smoothly as the
    panel widens instead of jumping from 15 to 69 in one frame. Because
    `bar_layout` centres its bars as a group, a linear count also produces
    linearly growing side margins -- 10pt in the pill, 73pt in the panel --
    without either number being written down anywhere.
    """
    t = 0.0 if open_ < 0.0 else 1.0 if open_ > 1.0 else open_
    return int(round(lerp(wf.BAR_COUNT, wf.BUFFER_BARS, t)))


def _label_width(text: str, char_width: float) -> float:
    return len(text) * char_width


def layout(
    width: float,
    height: float,
    radius: float,
    open_: float,
    state_label: str,
    items: tuple[StripItem, ...] = (),
) -> Layout:
    """Everything a renderer needs to draw one panel, and nothing else.

    `open_` is threaded straight through to `bands`, which is what makes the
    band hold the whole panel at rest instead of losing its fixed-height
    strip and hairline off the top of a 30pt pill. Grouped with `width`,
    `height` and `radius` because, like them, it describes the outer shape
    rather than the strip's contents.
    """
    band, hairline, strip = bands(width, height, open_)

    dot = None
    state_rect = None
    placed: list[Placed] = []
    divider = None

    if strip.h > 0.0:
        centre_y = strip.y + strip.h / 2.0

        dot = Rect(
            STRIP_PAD_X,
            centre_y - DOT_DIAMETER / 2.0,
            DOT_DIAMETER,
            DOT_DIAMETER,
        )
        state_w = _label_width(state_label, LABEL_CHAR_WIDTH)
        state_rect = Rect(
            dot.right + DOT_GAP,
            centre_y - LABEL_FONT_SIZE / 2.0,
            state_w,
            LABEL_FONT_SIZE,
        )

        # The right group is laid out right-to-left from the far edge, so the
        # gap between the two groups absorbs every difference in width. Laying
        # it out left-to-right would make the panel's right margin depend on
        # how long the hotkey's name happens to be.
        cursor = width - STRIP_PAD_X
        for index, item in enumerate(reversed(items)):
            label_w = _label_width(item.label, LABEL_CHAR_WIDTH)
            cap_w = (
                _label_width(item.cap, CAP_CHAR_WIDTH) + 2.0 * CAP_PAD_X
                if item.cap
                else 0.0
            )
            inner = label_w + (ITEM_GAP + cap_w if item.cap else 0.0)
            hover_w = inner + 2.0 * HOVER_PAD_X
            hover_rect = Rect(
                cursor - hover_w,
                centre_y - HOVER_HEIGHT / 2.0,
                hover_w,
                HOVER_HEIGHT,
            )
            label_rect = Rect(
                hover_rect.x + HOVER_PAD_X,
                centre_y - LABEL_FONT_SIZE / 2.0,
                label_w,
                LABEL_FONT_SIZE,
            )
            cap_rect = (
                Rect(
                    label_rect.right + ITEM_GAP,
                    centre_y - CAP_HEIGHT / 2.0,
                    cap_w,
                    CAP_HEIGHT,
                )
                if item.cap
                else None
            )
            placed.append(
                Placed(
                    id=item.id,
                    hover_rect=hover_rect,
                    label=item.label,
                    label_rect=label_rect,
                    cap=item.cap,
                    cap_rect=cap_rect,
                )
            )
            cursor = hover_rect.x
            if index < len(items) - 1:
                cursor -= GROUP_GAP
                divider = Rect(
                    cursor - DIVIDER_WIDTH,
                    centre_y - DIVIDER_HEIGHT / 2.0,
                    DIVIDER_WIDTH,
                    DIVIDER_HEIGHT,
                )
                cursor -= DIVIDER_WIDTH + GROUP_GAP

        placed.reverse()

    return Layout(
        width=width,
        height=height,
        radius=radius,
        band=band,
        hairline=hairline,
        strip=strip,
        dot=dot,
        state_label=state_label,
        state_rect=state_rect,
        items=tuple(placed),
        divider=divider,
    )


def hit_test(placed_layout: Layout, x: float, y: float) -> str | None:
    """Which strip item is at (x, y), if any. Panel-space coordinates.

    Only the items are hit-testable. The band, the dot and the state word are
    not controls today; when gate 6 makes the profile name a button it becomes
    an item like any other and needs no change here.
    """
    for item in placed_layout.items:
        if item.hover_rect.contains(x, y):
            return item.id
    return None
