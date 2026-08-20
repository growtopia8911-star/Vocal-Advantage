"""The plain arithmetic inside the macOS Flow Bar.

The panel itself is not tested -- there is no useful assertion to make about a
window, and the focus and click-through guarantees are read back from AppKit by
hand (see tools/flowbar_preview.py). What *is* tested is `pill_origin`, which is
ordinary arithmetic that decides where on screen the bar sits, and which is
wrong in a way you would not notice on the machine you developed it on: a second
monitor, or a Dock on the left, moves `visibleFrame`'s origin away from zero.

`flowbar_mac` guards its AppKit imports the same way `hotkey_mac` guards Quartz,
so this file collects and runs on Windows too.
"""

from __future__ import annotations

import pytest

from vocal_advantage import waveform as wf
from vocal_advantage.flowbar_mac import SIDE_MARGIN, pill_origin


class FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class FakeSize:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height


class FakeFrame:
    """Stands in for the NSRect that ``NSScreen.visibleFrame()`` returns."""

    def __init__(self, x=0.0, y=0.0, width=1920.0, height=1080.0) -> None:
        self.origin = FakePoint(x, y)
        self.size = FakeSize(width, height)


#: The Dock takes a strip off the bottom, so visibleFrame starts above y=0.
LAPTOP = FakeFrame(x=0.0, y=70.0, width=1512.0, height=912.0)
#: A monitor to the right of the built-in display: origin.x is not zero, which
#: is the case that silently puts the bar on the wrong screen.
SECOND_MONITOR = FakeFrame(x=1512.0, y=0.0, width=2560.0, height=1440.0)


def test_bottom_centre_is_horizontally_centred():
    x, _ = pill_origin("bottom-centre", 150.0, LAPTOP)
    assert x + 150.0 / 2 == pytest.approx(1512.0 / 2)


def test_bottom_left_sits_a_margin_in_from_the_left():
    x, _ = pill_origin("bottom-left", 150.0, LAPTOP)
    assert x == pytest.approx(SIDE_MARGIN)


def test_bottom_right_sits_a_margin_in_from_the_right():
    x, _ = pill_origin("bottom-right", 150.0, LAPTOP)
    assert x + 150.0 == pytest.approx(1512.0 - SIDE_MARGIN)


def test_every_position_clears_the_dock():
    # visibleFrame excludes the Dock, so the margin is measured from its top
    # edge. Measuring from the screen edge instead would tuck the bar behind it.
    for position in ("bottom-centre", "bottom-left", "bottom-right"):
        _, y = pill_origin(position, 150.0, LAPTOP)
        assert y == pytest.approx(70.0 + wf.SCREEN_MARGIN)


def test_positions_respect_a_screen_whose_origin_is_not_zero():
    # The bug this guards: arithmetic that assumes origin.x == 0 puts the pill
    # on the built-in display no matter which screen is actually the main one.
    x, y = pill_origin("bottom-centre", 150.0, SECOND_MONITOR)
    assert x + 150.0 / 2 == pytest.approx(1512.0 + 2560.0 / 2)
    assert y == pytest.approx(wf.SCREEN_MARGIN)

    left, _ = pill_origin("bottom-left", 150.0, SECOND_MONITOR)
    assert left == pytest.approx(1512.0 + SIDE_MARGIN)

    right, _ = pill_origin("bottom-right", 150.0, SECOND_MONITOR)
    assert right + 150.0 == pytest.approx(1512.0 + 2560.0 - SIDE_MARGIN)


def test_an_unknown_position_falls_back_to_centre():
    # config.json is hand-edited, so this has to be safe rather than clever.
    unknown, _ = pill_origin("middle-of-nowhere", 150.0, LAPTOP)
    centre, _ = pill_origin("bottom-centre", 150.0, LAPTOP)
    assert unknown == pytest.approx(centre)


def test_a_widened_pill_stays_centred():
    # The message state grows the pill; it must not drift sideways as it grows.
    narrow, _ = pill_origin("bottom-centre", 150.0, LAPTOP)
    wide, _ = pill_origin("bottom-centre", 250.0, LAPTOP)
    assert narrow + 150.0 / 2 == pytest.approx(wide + 250.0 / 2)


def test_a_widened_pill_stays_anchored_to_the_right_edge():
    narrow, _ = pill_origin("bottom-right", 150.0, LAPTOP)
    wide, _ = pill_origin("bottom-right", 250.0, LAPTOP)
    assert narrow + 150.0 == pytest.approx(wide + 250.0)
