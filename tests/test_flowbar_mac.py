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

import sys

import pytest

from vocal_advantage import flowbar_mac
from vocal_advantage import waveform as wf
from vocal_advantage.flowbar_mac import (
    SIDE_MARGIN,
    pill_origin,
    point_origin,
)

#: Matches the pattern in test_tray_state.py: skips a case that needs a real
#: NSObject instance rather than the plain-arithmetic functions everything
#: else in this file exercises.
darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="AppKit, and the target is an NSObject"
)


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


def _a_visible_frame(width: float, height: float) -> FakeFrame:
    """A fake `visibleFrame` sized like a real display, origin at zero.

    A thin factory over `FakeFrame` rather than a second fake: the panel-growth
    tests only care about screen size, not origin quirks -- those are already
    covered above.
    """
    return FakeFrame(width=width, height=height)


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


# --- a dragged position -----------------------------------------------------
#
# `point_origin` takes (centre_x, bottom_y) rather than a corner, because the
# pill widens to show a message: anchoring the centre keeps it growing evenly
# in both directions instead of walking sideways every time one appears.


def test_a_dragged_point_is_used_as_the_centre():
    x, y = point_origin([700.0, 300.0], 78.0, 30.0, LAPTOP)
    assert x + 78.0 / 2 == pytest.approx(700.0)
    assert y == pytest.approx(300.0)


def test_a_widened_pill_stays_centred_on_its_dragged_point():
    narrow, _ = point_origin([700.0, 300.0], 78.0, 30.0, LAPTOP)
    wide, _ = point_origin([700.0, 300.0], 260.0, 30.0, LAPTOP)
    assert narrow + 78.0 / 2 == pytest.approx(wide + 260.0 / 2)


def test_a_point_off_the_right_of_the_screen_is_clamped_back_on():
    # The failure this prevents: a saved position naming a monitor that has
    # since been unplugged leaves the bar invisible, and there is then nothing
    # on screen to drag it back with.
    x, _ = point_origin([99999.0, 300.0], 78.0, 30.0, LAPTOP)
    assert x + 78.0 <= LAPTOP.origin.x + LAPTOP.size.width


def test_a_point_off_the_left_is_clamped_back_on():
    x, _ = point_origin([-99999.0, 300.0], 78.0, 30.0, LAPTOP)
    assert x >= LAPTOP.origin.x


def test_a_point_below_the_dock_is_clamped_up():
    _, y = point_origin([700.0, -500.0], 78.0, 30.0, LAPTOP)
    assert y >= LAPTOP.origin.y


def test_a_point_above_the_menu_bar_is_clamped_down():
    _, y = point_origin([700.0, 99999.0], 78.0, 30.0, LAPTOP)
    assert y + 30.0 <= LAPTOP.origin.y + LAPTOP.size.height


def test_a_clamped_point_is_fully_on_screen_from_anywhere():
    for point in ([-9e9, -9e9], [9e9, 9e9], [0, 0], [1512, 912]):
        x, y = point_origin(point, 78.0, 30.0, LAPTOP)
        assert LAPTOP.origin.x <= x
        assert x + 78.0 <= LAPTOP.origin.x + LAPTOP.size.width
        assert LAPTOP.origin.y <= y
        assert y + 30.0 <= LAPTOP.origin.y + LAPTOP.size.height


def test_a_point_on_a_second_monitor_is_left_alone():
    # Clamping must not drag a legitimately-placed bar back to the main screen.
    x, y = point_origin([2800.0, 400.0], 78.0, 30.0, SECOND_MONITOR)
    assert x + 78.0 / 2 == pytest.approx(2800.0)
    assert y == pytest.approx(400.0)


def test_a_pill_wider_than_the_screen_still_lands_somewhere_sane():
    # Degenerate, but max()/min() ordering bugs here produce a NaN-ish origin
    # rather than an error.
    x, y = point_origin([700.0, 300.0], 9999.0, 30.0, LAPTOP)
    assert x == pytest.approx(LAPTOP.origin.x)
    assert LAPTOP.origin.y <= y


# --- drawing (task 5) --------------------------------------------------------
#
# The panel itself still is not tested by pixel -- see the module docstring --
# but a handful of things about *how* it draws are cheap to get backwards and
# expensive to notice by eye, so they are pinned here instead.


@darwin_only
def test_the_view_is_flipped():
    """panel.py returns top-left-origin rects, which is Pillow's convention.
    A flipped NSView adopts it, so one set of rects serves both renderers.

    Called on a real instance, not the unbound class method: `isFlipped` is a
    genuine AppKit override point (unlike this file's other private helpers),
    so pyobjc bridges it to a real Objective-C selector -- one that, correctly,
    refuses to run with a bare `None` standing in for `self`.
    """
    view = flowbar_mac._PillView.alloc().init()
    assert view.isFlipped() is True


def test_panel_height_is_taken_from_the_frame_not_the_constant():
    """The window must resize as it grows. Reading PILL_HEIGHT here is the
    bug that would draw a full-width panel one pill tall."""
    import inspect
    source = inspect.getsource(flowbar_mac.FlowBar._resize)
    assert "PILL_HEIGHT" not in source


def test_the_bottom_edge_does_not_move_as_the_panel_grows():
    """Gate 3c. The panel opens upward. If it grew about its centre it would
    walk down over the Dock, and at the bottom-left/right positions it would
    walk off the screen entirely.
    """
    visible = _a_visible_frame(width=1440.0, height=900.0)
    bottoms = [
        flowbar_mac.pill_origin("bottom-centre", width, visible)[1]
        for width in (78.0, 200.0, 420.0)
    ]
    assert len(set(bottoms)) == 1


def test_a_dragged_panel_also_grows_upward():
    """Gate 3c again, for the dragged position -- `point_origin` takes its
    anchor as (centre_x, bottom_y), which is what makes this free."""
    visible = _a_visible_frame(width=1440.0, height=900.0)
    point = (700.0, 120.0)
    bottoms = [
        flowbar_mac.point_origin(point, width, height, visible)[1]
        for width, height in ((78.0, 30.0), (200.0, 55.0), (420.0, 96.0))
    ]
    assert len(set(bottoms)) == 1


# --- hover and click-through (task 7) ----------------------------------------
#
# `_hover_for` and `_contains` are pure given `_last_layout`/`_last_origin`, so
# they are tested here with no cursor, no screen and no run loop -- exactly as
# `_tick` (which reads the real cursor) is not: see the module docstring's
# testing note. `_tick` itself is hand-checked, same as the click-through and
# focus guarantees it drives.


def test_hover_is_empty_when_the_cursor_is_elsewhere():
    bar = flowbar_mac.FlowBar.__new__(flowbar_mac.FlowBar)
    bar._last_layout = None
    assert bar._hover_for(0.0, 0.0) == ""


def test_hover_names_the_item_under_the_cursor():
    from vocal_advantage import panel
    bar = flowbar_mac.FlowBar.__new__(flowbar_mac.FlowBar)
    bar._last_layout = panel.layout(
        420.0, 96.0, 12.0, 1.0, "Recording",
        (panel.StripItem("stop", "Stop", "F8"),
         panel.StripItem("cancel", "Cancel", "Esc")),
    )
    bar._last_origin = (100.0, 200.0)
    item = bar._last_layout.items[1]
    # Panel space -> screen space. The panel is flipped, so y counts down from
    # the top edge, and the top edge is origin_y + height on a Mac.
    x = 100.0 + item.hover_rect.x + item.hover_rect.w / 2.0
    y = 200.0 + 96.0 - (item.hover_rect.y + item.hover_rect.h / 2.0)
    assert bar._hover_for(x, y) == "cancel"


def test_hover_is_empty_just_outside_the_panel():
    from vocal_advantage import panel
    bar = flowbar_mac.FlowBar.__new__(flowbar_mac.FlowBar)
    bar._last_layout = panel.layout(
        420.0, 96.0, 12.0, 1.0, "Recording",
        (panel.StripItem("stop", "Stop", "F8"),),
    )
    bar._last_origin = (100.0, 200.0)
    # Just above the top edge of the panel in screen space.
    assert bar._hover_for(300.0, 200.0 + 96.0 + 1.0) == ""


def test_contains_is_false_when_there_is_no_layout_yet():
    bar = flowbar_mac.FlowBar.__new__(flowbar_mac.FlowBar)
    bar._last_layout = None
    assert bar._contains(0.0, 0.0) is False


def test_contains_is_true_inside_the_last_drawn_rect_and_false_outside_it():
    from vocal_advantage import panel
    bar = flowbar_mac.FlowBar.__new__(flowbar_mac.FlowBar)
    bar._last_layout = panel.layout(420.0, 96.0, 12.0, 1.0, "Recording", ())
    bar._last_origin = (100.0, 200.0)
    assert bar._contains(300.0, 240.0) is True
    assert bar._contains(50.0, 240.0) is False


def test_click_through_is_the_default():
    """Gate 4a. The three guards in this file's docstring are why."""
    import inspect
    source = inspect.getsource(flowbar_mac.FlowBar)
    assert "setIgnoresMouseEvents_(True)" in source
