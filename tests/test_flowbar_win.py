"""The parts of the Windows Flow Bar that can be checked from a Mac.

The window itself cannot: no `UpdateLayeredWindow`, no HWND, no test. But three
pieces are plain data, and they are where the silent bugs live -- each of the
following produces a *plausible looking* wrong picture rather than a crash, so
none of them would be caught by "it ran".

* `pill_origin` -- Windows measures y downward and macOS measures it upward.
  Same picture on screen, opposite arithmetic.
* `render_frame` -- pure Pillow, no Win32 at all.
* `premultiplied_bgra` -- channel order, premultiplication and row order, all
  three of which are invisible on a black-and-cream design that is symmetric
  about its own centre line.

`flowbar_win` guards its Win32 imports so this file collects on macOS.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest
from PIL import Image

from vocal_advantage import flowbar
from vocal_advantage import panel
from vocal_advantage import waveform as wf
from vocal_advantage.flowbar import Frame
from vocal_advantage.flowbar_win import (
    SIDE_MARGIN,
    FlowBar,
    pill_origin,
    point_origin,
    premultiplied_bgra,
    render_frame,
)

SCREEN_W, SCREEN_H = 1920, 1080


def a_frame(**overrides):
    defaults = dict(
        state="recording",
        heights=wf.idle_heights(wf.BAR_COUNT),
        text="",
        width=float(wf.PILL_WIDTH),
        pill_alpha=0.9,
        bar_alpha=1.0,
        text_alpha=0.0,
    )
    defaults.update(overrides)
    return Frame(**defaults)


# --- pill_origin ------------------------------------------------------------

def test_bottom_centre_is_horizontally_centred():
    x, _ = pill_origin("bottom-centre", 78, SCREEN_W, SCREEN_H)
    assert x + 78 / 2 == pytest.approx(SCREEN_W / 2, abs=1)


def test_bottom_left_and_right_sit_a_margin_in():
    left, _ = pill_origin("bottom-left", 78, SCREEN_W, SCREEN_H)
    right, _ = pill_origin("bottom-right", 78, SCREEN_W, SCREEN_H)
    assert left == SIDE_MARGIN
    assert right + 78 == SCREEN_W - SIDE_MARGIN


def test_y_is_measured_downward_from_the_top():
    # The macOS twin measures upward from the bottom. Getting this backwards
    # puts the pill off the top of the screen, and only on Windows.
    _, y = pill_origin("bottom-centre", 78, SCREEN_W, SCREEN_H)
    assert y == SCREEN_H - wf.SCREEN_MARGIN - wf.PILL_HEIGHT
    assert 0 < y < SCREEN_H


def test_the_whole_pill_is_on_screen():
    for position in ("bottom-centre", "bottom-left", "bottom-right"):
        x, y = pill_origin(position, 78, SCREEN_W, SCREEN_H)
        assert 0 <= x and x + 78 <= SCREEN_W
        assert 0 <= y and y + wf.PILL_HEIGHT <= SCREEN_H


def test_an_unknown_position_falls_back_to_centre():
    unknown, _ = pill_origin("nowhere", 78, SCREEN_W, SCREEN_H)
    centre, _ = pill_origin("bottom-centre", 78, SCREEN_W, SCREEN_H)
    assert unknown == centre


def test_a_widened_pill_stays_centred():
    narrow, _ = pill_origin("bottom-centre", 78, SCREEN_W, SCREEN_H)
    wide, _ = pill_origin("bottom-centre", 240, SCREEN_W, SCREEN_H)
    assert narrow + 78 / 2 == pytest.approx(wide + 240 / 2, abs=1)


# --- render_frame -----------------------------------------------------------

def test_render_returns_an_rgba_image_of_the_right_size():
    image = render_frame(a_frame(), 78, 30)
    assert image.mode == "RGBA"
    assert image.size == (78, 30)


def test_the_corners_are_transparent():
    # Rounded ends over the desktop, not a rectangle with a pill drawn in it.
    image = render_frame(a_frame(), 78, 30)
    for corner in ((0, 0), (77, 0), (0, 29), (77, 29)):
        assert image.getpixel(corner)[3] < 40


def test_the_middle_is_opaque_enough_to_read():
    image = render_frame(a_frame(pill_alpha=0.9), 78, 30)
    assert image.getpixel((39, 15))[3] > 150


def test_the_rounded_ends_are_antialiased():
    # The entire reason this file exists rather than a tkinter one: a colour
    # key gives hard edges, and per-pixel alpha gives these.
    image = render_frame(a_frame(), 78, 30)
    alphas = {image.getpixel((x, y))[3] for x in range(78) for y in range(30)}
    assert any(20 < a < 235 for a in alphas), "no partial alpha: edges are hard"


def test_a_transparent_pill_really_is_transparent():
    image = render_frame(a_frame(pill_alpha=0.0, bar_alpha=0.0), 78, 30)
    assert max(image.getpixel((x, y))[3] for x in range(78) for y in range(30)) < 40


def test_louder_bars_paint_more_ink():
    """`panel.PILL_FILL_RGB` is near-black and `panel.BAR_RGB` is light --
    inverted from the old cream pill's dark-on-light bars (the whole panel
    redesign this task pulls in). "More ink" now means more *bright* pixels,
    not more dark ones."""
    def ink(heights):
        image = render_frame(a_frame(heights=heights), 78, 30)
        return sum(
            1
            for x in range(78)
            for y in range(30)
            if sum(image.getpixel((x, y))[:3]) > 400
            and image.getpixel((x, y))[3] > 128
        )

    assert ink((1.0,) * wf.BAR_COUNT) > ink(wf.idle_heights(wf.BAR_COUNT))


def test_the_render_is_vertically_mirrored():
    # The app's rule, on the Windows path too: bars grow equally up and down
    # from the centre line, never from a baseline.
    heights = tuple(
        0.3 + 0.6 * (i / (wf.BAR_COUNT - 1)) for i in range(wf.BAR_COUNT)
    )
    image = render_frame(a_frame(heights=heights), 78, 30)
    assert image.tobytes() == image.transpose(Image.FLIP_TOP_BOTTOM).tobytes()


def test_render_handles_the_widened_message_pill():
    assert render_frame(a_frame(width=260.0), 260, 30).size == (260, 30)


# --- premultiplied_bgra -----------------------------------------------------

def test_the_buffer_is_four_bytes_per_pixel():
    image = Image.new("RGBA", (4, 3), (255, 0, 0, 255))
    assert len(premultiplied_bgra(image)) == 4 * 3 * 4


def test_channels_come_out_in_bgra_order():
    # Pure red in, blue-green-red-alpha out. Get this wrong and the pill draws
    # with red and blue swapped -- nearly invisible on a monochrome design.
    image = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
    assert tuple(premultiplied_bgra(image)) == (0, 0, 255, 255)


def test_colours_are_premultiplied_by_alpha():
    # AC_SRC_ALPHA means Windows expects this already done. Skip it and every
    # antialiased edge picks up a bright halo.
    image = Image.new("RGBA", (1, 1), (255, 255, 255, 128))
    blue, green, red, alpha = premultiplied_bgra(image)
    assert alpha == 128
    assert red == green == blue == pytest.approx(128, abs=2)


def test_a_fully_transparent_pixel_is_all_zero():
    image = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    assert tuple(premultiplied_bgra(image)) == (0, 0, 0, 0)


def test_rows_come_out_bottom_up():
    # A DIB with positive biHeight is stored bottom row first, so the image has
    # to be flipped going in. The pill is symmetric about its centre line, so
    # this mistake would look completely fine.
    image = Image.new("RGBA", (1, 2), (0, 0, 0, 0))
    image.putpixel((0, 0), (255, 255, 255, 255))   # top row white
    buffer = premultiplied_bgra(image)
    assert tuple(buffer[0:4]) == (0, 0, 0, 0), "bottom row should come first"
    assert tuple(buffer[4:8]) == (255, 255, 255, 255)


def test_the_real_pill_converts_without_error():
    assert len(premultiplied_bgra(render_frame(a_frame(), 78, 30))) == 78 * 30 * 4


# ---------------------------------------------------------------------------
# The window procedure -- Windows only, because off Windows there is no Win32
# to get the prototype wrong against.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "win32", reason="there is no window procedure off Windows"
)
def test_the_window_procedure_survives_a_pointer_sized_lparam():
    """lparam is pointer-sized and really does carry pointers -- WM_CREATE
    passes a ``CREATESTRUCTW*``.

    Without an explicit ``argtypes`` on ``DefWindowProcW`` ctypes marshals it
    as a 32-bit C int, and the first message the window ever receives raises
    ``OverflowError: int too long to convert``. It happens inside a ctypes
    callback, where the exception cannot propagate: Python prints the
    traceback, hands Windows a 0, and the message goes unhandled -- so the
    overlay half-works and floods the console instead of failing outright.
    Found the first time this was run on Windows, on 2026-08-20.
    """
    from vocal_advantage.flowbar_win import _default_wndproc

    # WM_NULL, so DefWindowProcW is guaranteed to return 0 and we are testing
    # the marshalling and nothing else.
    assert _default_wndproc(0, 0, 0, 0x7FFF_FFFF_FFFF) == 0
    assert _default_wndproc(0, 0, 0, -1) == 0


# ---------------------------------------------------------------------------
# The contract main.py relies on
#
# All of this is plain introspection and arithmetic, so it runs on the Mac --
# which is the entire point. `main._make_flow_bar` passed `point=` to a Windows
# FlowBar that had never accepted it, so the overlay raised TypeError and was
# swallowed by the "decoration must never stop dictation" guard. Under the .pyw
# launcher there is no console, so it failed in total silence: no pill, no
# error, nothing to search for.
# ---------------------------------------------------------------------------


def test_flowbar_accepts_every_argument_main_passes_it():
    """main._make_flow_bar constructs the bar as
    FlowBar(indicator, position=..., point=...). The macOS twin took `point`
    and this one did not, so the whole overlay was dead on Windows."""
    import inspect

    accepted = set(inspect.signature(FlowBar.__init__).parameters)
    for name in ("indicator", "position", "point"):
        assert name in accepted, f"main.py passes {name!r} and FlowBar rejects it"


def test_flowbar_exposes_the_move_mode_api_the_tray_menu_calls():
    """main._move_mode reads `bar.movable` and calls `bar.set_movable`, and the
    quit path calls `bar.current_point()`. Missing any of them is an
    AttributeError from a menu click, long after startup looked fine."""
    for name in ("movable", "set_movable", "current_point", "open", "close"):
        assert hasattr(FlowBar, name), f"FlowBar is missing {name!r}"


def test_a_dragged_point_is_centred_on_that_point():
    # (centre_x, bottom_y) -- centre, so a widening pill grows symmetrically
    # instead of drifting sideways.
    x, y = point_origin([400.0, 300.0], 78, 30, 1920, 1080)
    assert (x + 78 / 2, y + 30) == (400.0, 300.0)


def test_a_dragged_point_off_the_screen_is_clamped_back_on():
    # A saved position can name a monitor that is no longer plugged in, and a
    # pill nobody can see reads as the app being broken.
    x, y = point_origin([9999.0, 9999.0], 78, 30, 1920, 1080)
    assert 0 <= x <= 1920 - 78
    assert 0 <= y <= 1080 - 30

    x, y = point_origin([-500.0, -500.0], 78, 30, 1920, 1080)
    assert (x, y) == (0, 0)


def test_y_for_a_dragged_point_is_measured_downward():
    """The macOS twin measures bottom_y upward from the bottom of the screen.
    Same picture, opposite arithmetic -- the exact trap `pill_origin` already
    has its own test for."""
    _, near_top = point_origin([960.0, 100.0], 78, 30, 1920, 1080)
    _, near_bottom = point_origin([960.0, 1000.0], 78, 30, 1920, 1080)
    assert near_top < near_bottom


# ---------------------------------------------------------------------------
# The panel -- the same rects `panel.layout` gives the macOS renderer, drawn
# with Pillow instead of AppKit.
# ---------------------------------------------------------------------------


def a_panel_frame(**kwargs):
    defaults = dict(
        state=flowbar.RECORDING,
        heights=(0.5,) * wf.BUFFER_BARS,
        text="", width=panel.PANEL_WIDTH, pill_alpha=1.0, bar_alpha=1.0,
        text_alpha=0.0, open=1.0, height=panel.PANEL_HEIGHT,
        radius=panel.PANEL_RADIUS,
        strip=(
            panel.StripItem("stop", "Stop", "F8"),
            panel.StripItem("cancel", "Cancel", "Esc"),
        ),
        hover="",
    )
    defaults.update(kwargs)
    return flowbar.Frame(**defaults)


def test_the_panel_renders_at_its_full_size():
    image = render_frame(a_panel_frame(), 420, 96)
    assert image.size == (420, 96)


def test_the_two_bands_are_different_colours():
    """Gate 1a. The strip is charcoal; the band is near-black."""
    image = render_frame(a_panel_frame(), 420, 96).convert("RGB")
    band = image.getpixel((30, 20))
    strip = image.getpixel((210, 80))
    assert sum(strip) > sum(band) + 40


def test_neither_band_is_flat():
    """Gate 1b."""
    image = render_frame(a_panel_frame(), 420, 96).convert("RGB")
    assert image.getpixel((30, 4)) != image.getpixel((30, 50))
    assert image.getpixel((210, 60)) != image.getpixel((210, 92))


def test_the_panel_is_not_forced_symmetric():
    """`_mirrored` exists for the pill and is wrong here: the panel's top and
    bottom halves are different by design. Applying it would paint a mirrored
    waveform band over the control strip."""
    image = render_frame(a_panel_frame(), 420, 96).convert("RGB")
    top = image.getpixel((210, 10))
    bottom = image.getpixel((210, 86))
    assert top != bottom


def test_the_pill_is_still_forced_symmetric():
    """And the pill still gets it, for the reason `_mirrored` documents."""
    image = render_frame(a_frame(), 78, 30).convert("RGBA")
    for y in range(6):
        assert image.getpixel((39, y)) == image.getpixel((39, 29 - y))


def test_the_panel_has_an_outer_border():
    """Gate 1c. Lighter than either band, on all four edges -- actually
    sampled on all four, not just the top."""
    image = render_frame(a_panel_frame(), 420, 96).convert("RGB")
    for edge_xy, inside_xy in (
        ((210, 0), (210, 8)),      # top
        ((210, 95), (210, 87)),    # bottom
        ((0, 48), (8, 48)),        # left
        ((419, 48), (411, 48)),    # right
    ):
        edge = image.getpixel(edge_xy)
        inside = image.getpixel(inside_xy)
        assert sum(edge) > sum(inside), f"{edge_xy} not lighter than {inside_xy}"


def test_the_resting_pill_has_a_border_too():
    """The border used to draw only inside `if frame.open > 0.001:`, so a
    resting frame -- `Frame.open`'s default, and what every pre-existing pill
    test exercises -- had no outline at all, while flowbar_mac strokes the
    border unconditionally. Found by cross-platform pixel sampling."""
    image = render_frame(a_frame(), 78, 30).convert("RGB")
    edge = image.getpixel((39, 0))
    inside = image.getpixel((39, 8))
    assert sum(edge) > sum(inside)


def test_the_tallest_bar_is_about_69_percent_of_the_band():
    """Gate 1e. Measured off superwhisper, not chosen -- a full-height trace
    reads as clipping and a short one reads as a dead microphone."""
    image = render_frame(
        a_panel_frame(heights=(1.0,) * wf.BUFFER_BARS), 420, 96
    ).convert("RGB")
    band_h = panel.BAND_HEIGHT
    # Scanned across the whole band rather than down one column: the bars sit
    # on a 4pt pitch, so a hard-coded x is one constant change away from
    # landing in a gap and asserting about the background.
    lit = [
        y for y in range(int(band_h))
        if any(image.getpixel((x, y))[0] > 120 for x in range(80, 340))
    ]
    assert lit, "no bars were drawn at all"
    assert 0.60 < (max(lit) - min(lit) + 1) / band_h < 0.78


def test_the_renderer_computes_no_layout_of_its_own():
    """Gate 5a. Every rect comes from panel.py, or the platforms drift."""
    import inspect
    from vocal_advantage import flowbar_win
    source = inspect.getsource(flowbar_win.render_frame)
    assert "panel.layout(" in source
    assert "STRIP_HEIGHT" not in source
    assert "BAND_HEIGHT" not in source


def test_writes_a_png_to_look_at(tmp_path):
    """The honest substitute for eyeballing this on Windows. Not an assertion
    about beauty -- it is the fixture the spec's verification table names."""
    out = tmp_path / "panel.png"
    render_frame(a_panel_frame(), 420, 96).save(out)
    assert out.stat().st_size > 0
