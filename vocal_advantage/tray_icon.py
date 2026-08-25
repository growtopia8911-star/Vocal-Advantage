"""The tray / menu-bar icon, drawn in code. No image files in the repo.

The glyph is the app's own waveform: a short row of round-capped bars mirrored
about the centre line, the same rule the Flow Bar obeys. At 16px there is no
room for anything cleverer, and a miniature of the thing on screen is a better
answer than an unrelated microphone symbol.

**Legibility on both a dark and a light bar is the whole problem here**, and the
two platforms solve it differently, which is why `template` exists:

* macOS takes a *template image* -- black plus alpha, no colour of its own --
  and recolours it itself for whichever menu-bar appearance is in force. Correct
  in both by construction, and nothing needs to detect anything.
* Windows has no such mechanism. That icon carries its own contrast instead: a
  near-white glyph with a dark outline behind it, so one half of the pairing
  always stands against whatever the taskbar is doing.

Pillow is a dependency on both platforms for exactly this reason -- generating
the icon is what keeps binaries out of the repository.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

#: 64px is generous for a menu bar, and both platforms downscale cleanly. Being
#: larger than needed is what stops the round caps turning into mush at 22px.
ICON_SIZE = 64

#: Odd, so there is a true centre bar. Five is as many as survives 16px.
ICON_BARS = 5
#: Fractions of the icon's height, mirrored about the centre line. Shaped like a
#: snippet of speech rather than a tidy arch, so it reads as a waveform.
ICON_HEIGHTS = (0.34, 0.66, 1.0, 0.52, 0.24)

#: Fractions of the icon size, so every dimension scales with `size`.
BAR_WIDTH_FRACTION = 0.11
BAR_PITCH_FRACTION = 0.185
MAX_HALF_FRACTION = 0.33

GLYPH_LIGHT = (250, 250, 250, 255)
GLYPH_DARK = (10, 10, 10, 255)
#: Pure black, not GLYPH_DARK. macOS only treats an image as a template if it
#: carries no colour of its own; (10, 10, 10) is a colour, and the icon quietly
#: stops adapting to the menu-bar appearance.
TEMPLATE_BLACK = (0, 0, 0, 255)
#: How far the Windows outline extends past the glyph, as a fraction of size.
OUTLINE_FRACTION = 0.055

#: The state dot's colours, keyed by `flowbar`'s own state names so the two
#: cannot drift apart. Idle is absent on purpose -- doing nothing is the state
#: the icon sits in all day and it earns no mark.
#:
#: Matched to the recording window's own vocabulary: red while the microphone
#: is open, blue while the model is working, amber for something that wants a
#: look. Red is the only one that has to be unmistakable, because it is the one
#: that means a microphone is live.
STATE_DOTS = {
    "recording": (232, 62, 54),
    "transcribing": (10, 132, 255),
    "message": (255, 168, 38),
}

#: Fractions of the icon. Big enough to survive being resampled down to the
#: 16px the menu bar may ask for, and pushed into the lower-right corner so it
#: sits clear of the glyph's tallest bar.
DOT_RADIUS_FRACTION = 0.20
DOT_CENTRE_FRACTION = 0.76
#: A dark rim, so the dot separates from the light glyph behind it and from a
#: pale menu bar underneath.
DOT_RIM_FRACTION = 0.035

#: Drawn at this multiple and scaled down. Pillow has no antialiasing of its
#: own, so without it the round caps come out as staircases at 22px.
SUPERSAMPLE = 4


def _bar_boxes(size: int) -> list[tuple[float, float, float, float]]:
    """The mirrored bars, as (x0, y0, x1, y1) boxes in pixels."""
    centre = size / 2.0
    width = size * BAR_WIDTH_FRACTION
    pitch = size * BAR_PITCH_FRACTION
    max_half = size * MAX_HALF_FRACTION

    first = centre - pitch * (ICON_BARS - 1) / 2.0
    boxes = []
    for index, height in enumerate(ICON_HEIGHTS):
        x = first + index * pitch
        # Never shorter than it is wide, or the round cap becomes a squashed
        # ellipse -- the same floor the Flow Bar's renderer applies.
        half = max(width / 2.0, height * max_half)
        boxes.append((x - width / 2.0, centre - half, x + width / 2.0, centre + half))
    return boxes


def make_icon(
    size: int = ICON_SIZE, *, template: bool = False, state: str | None = None
) -> Image.Image:
    """The icon as an RGBA image with transparent corners.

    `template=True` gives macOS its black-plus-alpha template image.
    `template=False` gives Windows a light glyph on a dark outline.

    `state` adds the status dot, and **overrides `template`** rather than
    combining with it. That is not a convenience: a template image is
    black-plus-alpha and macOS recolours the whole of it for the current
    menu-bar appearance, so a colour put into one is flattened away. An icon
    that carries a state colour therefore cannot be a template, and having
    stopped being one it has to carry its own contrast -- which is exactly what
    the Windows glyph already does, so a state icon is that glyph plus the dot
    on both platforms.

    Idle is not a state here. It has no dot, stays a template on macOS, and is
    byte-for-byte the icon that shipped.
    """
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    boxes = _bar_boxes(big)
    dot = STATE_DOTS.get(state or "")

    if template and dot is None:
        for box in boxes:
            _capsule(draw, box, TEMPLATE_BLACK)
    else:
        # Windows: the dark outline first, by fattening every bar, then the
        # light glyph on top of it.
        grow = big * OUTLINE_FRACTION
        for x0, y0, x1, y1 in boxes:
            _capsule(
                draw, (x0 - grow, y0 - grow, x1 + grow, y1 + grow), GLYPH_DARK
            )
        for box in boxes:
            _capsule(draw, box, GLYPH_LIGHT)

    image = _mirrored(image)
    # After the mirroring, never before. `_mirrored` copies the top half over
    # the bottom, so a dot drawn into one corner beforehand comes back as two.
    if dot is not None:
        _dot(image, big, dot)
    return image.resize((size, size), Image.LANCZOS)


def _dot(image: Image.Image, big: int, colour) -> None:
    """The status blob, lower-right, with a dark rim to lift it off the glyph."""
    radius = big * DOT_RADIUS_FRACTION
    centre = big * DOT_CENTRE_FRACTION
    rim = big * DOT_RIM_FRACTION
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (centre - radius - rim, centre - radius - rim,
         centre + radius + rim, centre + radius + rim),
        fill=GLYPH_DARK,
    )
    draw.ellipse(
        (centre - radius, centre - radius, centre + radius, centre + radius),
        fill=colour + (255,),
    )


def _mirrored(image: Image.Image) -> Image.Image:
    """Force exact symmetry about the horizontal centre line.

    Not belt and braces -- a correctness fix. A bar spanning the centre of an
    even-sized image straddles a half-pixel boundary, and Pillow rounds its two
    ends independently, so the drawn glyph comes out a row taller above the
    centre than below it. Copying the top half over the bottom is the only way
    to guarantee the mirroring the design actually asks for.
    """
    half = image.height // 2
    top = image.crop((0, 0, image.width, half))
    out = image.copy()
    out.paste(top.transpose(Image.FLIP_TOP_BOTTOM), (0, image.height - half))
    return out


def _capsule(draw: ImageDraw.ImageDraw, box, colour) -> None:
    """One round-capped vertical bar."""
    x0, y0, x1, y1 = box
    radius = (x1 - x0) / 2.0
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=colour)
