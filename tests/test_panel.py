"""The panel's layout arithmetic.

Everything here runs on any machine, which is the point of the module: the
two renderers draw from these rects, so a layout bug is caught once, here,
instead of twice, by eye, on two operating systems.
"""

import pytest

from vocal_advantage import panel


# --- geometry ---------------------------------------------------------------

def test_panel_is_the_measured_size():
    assert (panel.PANEL_WIDTH, panel.PANEL_HEIGHT) == (420.0, 96.0)
    assert panel.PANEL_RADIUS == 12.0


def test_the_three_horizontal_pieces_fill_the_height_exactly():
    """57 + 1 + 38 = 96. A rounding error here shows as a seam."""
    assert panel.BAND_HEIGHT + panel.HAIRLINE + panel.STRIP_HEIGHT == \
        panel.PANEL_HEIGHT


def test_bands_stack_without_gap_or_overlap():
    band, hairline, strip = panel.bands(420.0, 96.0)
    assert band.y == 0.0
    assert hairline.y == band.y + band.h
    assert strip.y == hairline.y + hairline.h
    assert strip.y + strip.h == 96.0


def test_bands_span_the_full_width():
    for rect in panel.bands(420.0, 96.0):
        assert rect.x == 0.0
        assert rect.w == 420.0


def test_bands_scale_with_a_shrunken_panel():
    """Mid-animation the panel is neither pill nor panel, and the strip must
    still sit on the bottom edge rather than floating."""
    band, hairline, strip = panel.bands(200.0, 60.0)
    assert strip.y + strip.h == 60.0
    assert band.y == 0.0
    assert band.h + hairline.h + strip.h == 60.0


# --- palette ----------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("BAND_TOP_RGB", (24, 24, 24)),
    ("BAND_BOTTOM_RGB", (1, 1, 1)),
    ("STRIP_TOP_RGB", (46, 47, 47)),
    ("STRIP_BOTTOM_RGB", (35, 36, 36)),
    ("HAIRLINE_RGB", (99, 100, 100)),
    ("BORDER_RGB", (83, 83, 83)),
    ("BAR_RGB", (213, 213, 213)),
    ("TEXT_RGB", (175, 176, 176)),
    ("CAP_FILL_RGB", (31, 32, 32)),
    ("PILL_FILL_RGB", (11, 11, 11)),
    ("DOT_RECORDING_RGB", (255, 69, 58)),
    ("DOT_TRANSCRIBING_RGB", (50, 121, 192)),
])
def test_palette_matches_what_was_measured(name, expected):
    """Measured off design-research/superwhisper/assets/, not chosen."""
    assert getattr(panel, name) == expected


def test_neither_band_is_a_flat_fill():
    """Both bands are vertical gradients. Flat fills are most of why a copy
    of this panel looks like a rectangle of paint."""
    assert panel.BAND_TOP_RGB != panel.BAND_BOTTOM_RGB
    assert panel.STRIP_TOP_RGB != panel.STRIP_BOTTOM_RGB


def test_key_caps_are_darker_than_the_strip_they_sit_on():
    """Gate 2c. The chips recede into the strip rather than standing off it."""
    assert sum(panel.CAP_FILL_RGB) < sum(panel.STRIP_BOTTOM_RGB)


def test_module_is_pure():
    """Gate 5c. The whole value of this module is that it runs anywhere.

    Parsed rather than grepped: the docstring names AppKit and Pillow to
    explain why it must not import them, and a text search would fail on the
    explanation.
    """
    import ast

    with open(panel.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"AppKit", "Foundation", "objc", "ctypes", "PIL", "Quartz"}
    assert not (imported & forbidden), f"panel.py imports {imported & forbidden}"


# --- lerp -------------------------------------------------------------------

def test_lerp_hits_both_ends_exactly():
    assert panel.lerp(78.0, 420.0, 0.0) == 78.0
    assert panel.lerp(78.0, 420.0, 1.0) == 420.0


def test_lerp_is_linear_in_the_middle():
    assert panel.lerp(0.0, 10.0, 0.25) == 2.5
