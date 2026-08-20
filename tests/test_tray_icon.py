"""The generated tray icon.

Drawn in code with Pillow so no image files live in the repo. What is worth
asserting is not whether it looks nice -- that is a job for eyes -- but the
handful of properties that make it work at all, and each has a specific way of
failing silently:

* transparent corners, or the icon is a rectangle sitting in the menu bar;
* the right mode and size, or the platform silently scales or refuses it;
* **legible on a dark AND a light background**, which is the requirement that
  is easiest to satisfy on the machine you developed it on and nowhere else.

The two platforms need genuinely different images, which is why `template`
exists. macOS takes a template image -- black plus alpha -- and recolours it
per menu-bar appearance, so it is correct in both by construction. Windows has
no such mechanism, so that icon has to carry its own contrast.
"""

from __future__ import annotations

import pytest
from PIL import Image

from vocal_advantage.tray_icon import ICON_SIZE, make_icon


def alpha_of(image, xy):
    return image.getpixel(xy)[3]


def opaque_pixels(image):
    return [
        image.getpixel((x, y))
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y))[3] > 0
    ]


@pytest.mark.parametrize("template", [True, False])
def test_icon_is_rgba(template):
    assert make_icon(template=template).mode == "RGBA"


@pytest.mark.parametrize("template", [True, False])
def test_icon_is_square_and_the_default_size(template):
    icon = make_icon(template=template)
    assert icon.size == (ICON_SIZE, ICON_SIZE)


@pytest.mark.parametrize("size", [16, 22, 32, 64])
def test_icon_can_be_drawn_at_any_size(size):
    assert make_icon(size=size).size == (size, size)


@pytest.mark.parametrize("template", [True, False])
def test_the_corners_are_transparent(template):
    # A tray icon with opaque corners reads as a rectangle pasted into the
    # menu bar rather than a glyph.
    icon = make_icon(template=template)
    last = ICON_SIZE - 1
    for corner in ((0, 0), (last, 0), (0, last), (last, last)):
        assert alpha_of(icon, corner) == 0


@pytest.mark.parametrize("template", [True, False])
def test_the_icon_actually_draws_something(template):
    icon = make_icon(template=template)
    assert len(opaque_pixels(icon)) > 20


@pytest.mark.parametrize("template", [True, False])
def test_the_glyph_is_vertically_mirrored(template):
    # It is the app's own waveform, so it obeys the app's own rule: bars grow
    # equally up and down from the centre line, never from a baseline.
    icon = make_icon(template=template)
    flipped = icon.transpose(Image.FLIP_TOP_BOTTOM)
    assert icon.tobytes() == flipped.tobytes()


def test_the_template_icon_is_black_plus_alpha():
    # macOS only honours a template image if it carries no colour of its own;
    # anything else and it stops adapting to the menu bar appearance.
    for pixel in opaque_pixels(make_icon(template=True)):
        assert pixel[:3] == (0, 0, 0)


def test_the_template_icon_is_antialiased():
    # Partly-transparent edge pixels are what stop the round caps looking like
    # staircases at 22px. All-or-nothing alpha means the supersampling is gone.
    alphas = {p[3] for p in opaque_pixels(make_icon(template=True))}
    assert any(0 < a < 255 for a in alphas)


def test_the_windows_icon_carries_its_own_contrast():
    # No template mechanism on Windows, so the icon must be legible on a dark
    # taskbar and a light one by itself. It does that by pairing a light glyph
    # with a dark outline, so both extremes must be present.
    pixels = opaque_pixels(make_icon(template=False))
    brightness = [sum(p[:3]) / 3 for p in pixels]
    assert max(brightness) > 200, "nothing light enough to show on a dark bar"
    assert min(brightness) < 60, "nothing dark enough to show on a light bar"


def test_the_windows_icon_is_legible_against_both_extremes():
    # The real test of the pairing: whichever background it lands on, some
    # part of the glyph must contrast strongly with it.
    pixels = opaque_pixels(make_icon(template=False))
    brightness = [sum(p[:3]) / 3 for p in pixels]
    assert max(abs(b - 0) for b in brightness) > 140    # on black
    assert max(abs(b - 255) for b in brightness) > 140  # on white


def test_two_icons_of_the_same_size_are_identical():
    # Redeploying must not make the tray icon flicker or look different.
    assert make_icon().tobytes() == make_icon().tobytes()


def test_the_two_variants_differ():
    assert make_icon(template=True).tobytes() != make_icon(
        template=False
    ).tobytes()


def test_a_tiny_icon_still_draws_bars():
    # 16px is the smallest the Windows tray asks for; the glyph must not
    # collapse to nothing through rounding.
    assert len(opaque_pixels(make_icon(size=16))) > 8
