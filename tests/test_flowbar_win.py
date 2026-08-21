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

from vocal_advantage import waveform as wf
from vocal_advantage.flowbar import Frame
from vocal_advantage.flowbar_win import (
    SIDE_MARGIN,
    pill_origin,
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
    def ink(heights):
        image = render_frame(a_frame(heights=heights), 78, 30)
        return sum(
            1
            for x in range(78)
            for y in range(30)
            if sum(image.getpixel((x, y))[:3]) < 200
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
