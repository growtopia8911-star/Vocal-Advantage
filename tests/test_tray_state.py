"""The menu-bar dot: what the app is doing, visible without opening anything.

Gate 2 of `docs/plans/2026-08-25-interface-design.md`.

Today the tray knows the state and will not say so. `status_text()` is read in
`menuNeedsUpdate_`, which fires when the menu opens -- so the answer to "is it
recording?" requires clicking to find out, and the Flow Bar, which does show
state, can be switched off entirely with `flow_bar: false`. That leaves the app
with no persistent state channel at all.

**The trap this gate was written around.** `tray_mac` calls
`setTemplate_(True)`, which hands the icon to macOS to recolour for whichever
menu-bar appearance is in force. That is why the icon is correct on a light bar
and a dark one with nothing to detect -- and it is also why a coloured dot
cannot simply be added: a template image is black-plus-alpha and macOS flattens
every colour in it. So an icon carrying a state colour must NOT be a template,
which in turn means it has to carry its own contrast, exactly as the Windows
icon already does.

**The second trap.** `_mirrored()` forces exact symmetry about the horizontal
centre line, for the good reason its docstring gives. Anything drawn before it
gets copied into both halves, so a badge in one corner would silently become
two. The dot goes on after.
"""

from __future__ import annotations

import sys

import pytest

from vocal_advantage.flowbar import (
    IDLE,
    MESSAGE,
    RECORDING,
    TRANSCRIBING,
    Indicator,
)
from vocal_advantage.tray_icon import ICON_SIZE, make_icon

STATES = [RECORDING, TRANSCRIBING, MESSAGE]


def pixels(image):
    """Every RGBA tuple, row by row.

    Via `load()` rather than `getdata()`, which Pillow deprecates and which
    would leave a warning in every run of this file.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    access = rgba.load()
    return [access[x, y] for y in range(height) for x in range(width)]


def dot_rows(image):
    """Which rows hold dot pixels -- hued, opaque, and not part of the glyph."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    access = rgba.load()
    return [
        y
        for y in range(height)
        for x in range(width)
        if access[x, y][3] > 40
        and max(access[x, y][:3]) - min(access[x, y][:3]) > 40
    ]


def coloured(image):
    """Pixels with real hue -- not black, not white, not transparent."""
    out = []
    for r, g, b, a in pixels(image):
        if a < 40:
            continue
        if max(r, g, b) - min(r, g, b) > 40:
            out.append((r, g, b))
    return out


def dominant(image):
    found = coloured(image)
    assert found, "no coloured pixels at all"
    return max(set(found), key=found.count)


# --- the resting icon is untouched ------------------------------------------

def test_idle_is_byte_for_byte_the_icon_that_shipped():
    """Gate 2: additive. The quiet state must not change at all."""
    assert pixels(make_icon(ICON_SIZE, template=True, state=IDLE)) == pixels(
        make_icon(ICON_SIZE, template=True)
    )


def test_idle_carries_no_dot():
    assert coloured(make_icon(ICON_SIZE, template=True, state=IDLE)) == []


# --- every working state says which one it is -------------------------------

@pytest.mark.parametrize("state", STATES)
def test_a_working_state_carries_a_coloured_dot(state):
    assert coloured(make_icon(ICON_SIZE, template=True, state=state))


def test_the_states_are_told_apart_by_colour():
    """Gate 2a. Three distinct dots, not three shades of one."""
    seen = {state: dominant(make_icon(ICON_SIZE, state=state)) for state in STATES}
    assert len(set(seen.values())) == len(STATES), seen


def test_recording_is_the_red_one():
    """The one state where being wrong matters: the microphone is open."""
    red, green, blue = dominant(make_icon(ICON_SIZE, state=RECORDING))
    assert red > 150 and green < 110 and blue < 110


# --- the two traps ----------------------------------------------------------

@pytest.mark.parametrize("state", STATES)
def test_a_state_icon_ignores_template_and_keeps_its_own_contrast(state):
    """Gate 2c. macOS flattens a template image, so a coloured icon cannot be
    one -- and a non-template icon has to survive a light bar and a dark bar on
    its own, the way the Windows glyph already does."""
    asked_for_template = make_icon(ICON_SIZE, template=True, state=state)
    asked_for_neither = make_icon(ICON_SIZE, template=False, state=state)
    assert pixels(asked_for_template) == pixels(asked_for_neither)

    # Both a near-black and a near-white are present: that pairing is what
    # makes the glyph readable whichever way the menu bar is painted.
    visible = [(r, g, b) for r, g, b, a in pixels(asked_for_template) if a > 180]
    assert any(max(p) < 90 for p in visible), "no dark ink to stand on a light bar"
    assert any(min(p) > 170 for p in visible), "no light ink to stand on a dark bar"


@pytest.mark.parametrize("state", STATES)
def test_the_dot_is_not_mirrored_into_both_halves(state):
    """`_mirrored()` copies the top half over the bottom. A dot drawn before it
    would come out as two, which is why it is drawn after."""
    image = make_icon(ICON_SIZE, state=state)
    height = image.size[1]
    rows = dot_rows(image)
    assert rows, "no dot found"
    # Entirely in one half, so it cannot be a mirrored pair.
    assert min(rows) > height / 2 or max(rows) < height / 2


# --- what the tray asks the Indicator for -----------------------------------
#
# `status_text()` cannot answer this: it maps MESSAGE onto "Idle", because the
# menu's status line should not say "Message". The dot does need to tell them
# apart, so the tray reads the raw state instead.

def test_a_fresh_indicator_is_idle():
    assert Indicator().state_name() == IDLE


@pytest.mark.parametrize(
    "call,expected",
    [
        ("show_recording", RECORDING),
        ("show_processing", TRANSCRIBING),
    ],
)
def test_the_state_name_follows_the_controllers_calls(call, expected):
    indicator = Indicator()
    getattr(indicator, call)()
    assert indicator.state_name() == expected


def test_a_flash_is_its_own_state_even_though_the_status_line_says_idle():
    indicator = Indicator()
    indicator.flash("nothing heard")
    assert indicator.state_name() == MESSAGE
    assert indicator.status_text() == "Idle"


def test_hiding_returns_to_idle():
    indicator = Indicator()
    indicator.show_recording()
    indicator.hide()
    assert indicator.state_name() == IDLE


def test_the_state_is_readable_without_a_render_loop():
    """`flow_bar: false` means nothing ever calls next_frame(). The tray is
    then the only state channel there is, so it must not depend on one --
    which is the same reason `_status` is set by the calling thread."""
    indicator = Indicator()
    indicator.show_recording()
    assert indicator.state_name() == RECORDING  # no next_frame() anywhere


# --- the swap itself --------------------------------------------------------
#
# The NSStatusItem cannot be driven from a test, but the decision it makes can:
# the timer fires eight times a second and almost every tick must do nothing.

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="AppKit, and the target is an NSObject"
)


class FakeButton:
    def __init__(self) -> None:
        self.images = []

    def setImage_(self, image) -> None:
        self.images.append(image)


def target_for(indicator):
    from vocal_advantage.tray_mac import _TrayTarget

    target = _TrayTarget.alloc().initWithIndicator_onQuit_(indicator, lambda: None)
    button = FakeButton()
    target.setButton_(button)
    return target, button


@darwin_only
def test_a_tick_with_no_state_change_repaints_nothing():
    """Eight times a second forever, so the common case has to be free."""
    target, button = target_for(Indicator())
    for _ in range(20):
        target.tick_(None)
    assert button.images == []


@darwin_only
def test_a_tick_after_a_state_change_repaints_once():
    indicator = Indicator()
    target, button = target_for(indicator)
    target.tick_(None)

    indicator.show_recording()
    target.tick_(None)
    target.tick_(None)
    target.tick_(None)

    assert len(button.images) == 1


@darwin_only
def test_every_state_change_gets_its_own_repaint():
    indicator = Indicator()
    target, button = target_for(indicator)
    for call in ("show_recording", "show_processing", "hide"):
        getattr(indicator, call)()
        target.tick_(None)
    assert len(button.images) == 3


@darwin_only
def test_only_the_idle_icon_is_a_template():
    """The trap, asserted where it would actually bite: macOS flattens a
    template image, so the coloured states must not ship as one."""
    from vocal_advantage.tray_mac import _ns_image

    assert _ns_image(state=IDLE).isTemplate()
    for state in STATES:
        assert not _ns_image(state=state).isTemplate(), state


@darwin_only
def test_an_indicator_that_raises_does_not_take_the_menu_bar_down():
    class Broken:
        def state_name(self):
            raise RuntimeError("gone")

    target, button = target_for(Broken())
    target.tick_(None)          # must not raise
    assert button.images == []


# --- it stays legible at the size it is actually drawn ----------------------

@pytest.mark.parametrize("size", [16, 18, 22])
def test_the_dot_survives_being_shrunk_to_menu_bar_size(size):
    """18pt is what `tray_mac` asks for; 16 and 22 bracket it."""
    assert coloured(make_icon(size, state=RECORDING))
