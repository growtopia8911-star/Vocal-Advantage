"""The panel's layout arithmetic.

Everything here runs on any machine, which is the point of the module: the
two renderers draw from these rects, so a layout bug is caught once, here,
instead of twice, by eye, on two operating systems.
"""

import pytest

from vocal_advantage import panel
from vocal_advantage import waveform as wf


# --- geometry ---------------------------------------------------------------

def test_panel_is_the_measured_size():
    assert (panel.PANEL_WIDTH, panel.PANEL_HEIGHT) == (420.0, 96.0)
    assert panel.PANEL_RADIUS == 12.0


def test_the_three_horizontal_pieces_fill_the_height_exactly():
    """57 + 1 + 38 = 96. A rounding error here shows as a seam."""
    assert panel.BAND_HEIGHT + panel.HAIRLINE + panel.STRIP_HEIGHT == \
        panel.PANEL_HEIGHT


def test_bands_stack_without_gap_or_overlap():
    band, hairline, strip = panel.bands(420.0, 96.0, 1.0)
    assert band.y == 0.0
    assert hairline.y == band.y + band.h
    assert strip.y == hairline.y + hairline.h
    assert strip.y + strip.h == 96.0


def test_bands_span_the_full_width():
    for rect in panel.bands(420.0, 96.0, 1.0):
        assert rect.x == 0.0
        assert rect.w == 420.0


def test_bands_scale_with_a_shrunken_panel():
    """Mid-animation the panel is neither pill nor panel, and the strip must
    still sit on the bottom edge rather than floating.

    The strip and hairline are no longer fixed heights: they scale with
    `open_` just as the band does, so a half-open panel gets a half-height
    strip -- not a full-height one squeezing an already-small band, which is
    the bug this replaces (see test_the_band_is_never_starved_mid_grow).
    """
    band, hairline, strip = panel.bands(200.0, 60.0, 0.5)
    assert strip.y + strip.h == 60.0
    assert band.y == 0.0
    assert band.h + hairline.h + strip.h == 60.0
    assert strip.h == pytest.approx(panel.STRIP_HEIGHT * 0.5)
    assert hairline.h == pytest.approx(panel.HAIRLINE * 0.5)


def test_a_resting_pill_is_all_waveform():
    """Gate 1a. open_=0 is the pill, and the pill has never shown anything
    but its waveform -- so the band must claim the whole height and the
    strip and hairline must vanish, not draw at some ghost fixed size behind
    a band that never gets to include their space.

    This is the exact defect a rendered offscreen capture caught and no test
    did: with the old fixed-height strip, a 30pt resting pill had 30 - 38 - 1
    left for its band, clamped to 0.0, and `_draw_bars` returns early on
    `band.h <= 0` -- so the pill drew as an empty black lozenge.
    """
    band, hairline, strip = panel.bands(78.0, 30.0, 0.0)
    assert band.h == 30.0
    assert hairline.h == 0.0
    assert strip.h == 0.0


def test_the_band_is_never_starved_mid_grow():
    """`open_` and `height` do not vary independently in the real app --
    `flowbar.Frame.height` is `lerp(PILL_HEIGHT, PANEL_HEIGHT, open_)` -- so
    this samples that actual relationship rather than an arbitrary pair.
    A fixed-height strip and hairline would eat into a still-small band
    before the panel had grown enough to afford them; this is the assertion
    whose absence let that ship.
    """
    for tenths in range(0, 11):
        open_ = tenths / 10.0
        height = panel.lerp(float(wf.PILL_HEIGHT), panel.PANEL_HEIGHT, open_)
        band, _, _ = panel.bands(420.0, height, open_)
        assert band.h > 0.0


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


# --- bar count across the grow ---------------------------------------------

def test_bar_count_at_rest_is_the_pill_count():
    from vocal_advantage import waveform as wf
    assert panel.bars_for_open(0.0) == wf.BAR_COUNT


def test_bar_count_when_open_is_the_whole_buffer():
    from vocal_advantage import waveform as wf
    assert panel.bars_for_open(1.0) == wf.BUFFER_BARS


def test_bar_count_grows_monotonically():
    """The grow reveals history. It must never take bars away mid-animation."""
    counts = [panel.bars_for_open(i / 40.0) for i in range(41)]
    assert counts == sorted(counts)


def test_bar_count_is_clamped_outside_zero_to_one():
    from vocal_advantage import waveform as wf
    assert panel.bars_for_open(-0.5) == wf.BAR_COUNT
    assert panel.bars_for_open(1.5) == wf.BUFFER_BARS


# --- strip layout -----------------------------------------------------------

def a_layout(width=420.0, height=96.0, items=None, state="Recording", open_=1.0):
    if items is None:
        items = (
            panel.StripItem("stop", "Stop", "F8"),
            panel.StripItem("cancel", "Cancel", "Esc"),
        )
    return panel.layout(width, height, panel.PANEL_RADIUS, open_, state, items)


def test_dot_and_state_word_sit_at_the_left():
    placed = a_layout()
    assert placed.dot.x == pytest.approx(panel.STRIP_PAD_X)
    assert placed.state_rect.x > placed.dot.right


def test_dot_is_vertically_centred_in_the_strip():
    placed = a_layout()
    strip = placed.strip
    centre = strip.y + strip.h / 2.0
    assert placed.dot.y + placed.dot.h / 2.0 == pytest.approx(centre)


def test_items_are_ordered_left_to_right_as_given():
    placed = a_layout()
    assert [item.id for item in placed.items] == ["stop", "cancel"]
    assert placed.items[0].hover_rect.right <= placed.items[1].hover_rect.x


def test_the_right_group_is_flush_right():
    placed = a_layout()
    last = placed.items[-1]
    assert last.hover_rect.right == pytest.approx(
        420.0 - panel.STRIP_PAD_X
    )


def test_a_divider_sits_between_the_two_items():
    placed = a_layout()
    assert placed.divider is not None
    assert placed.items[0].hover_rect.right <= placed.divider.x
    assert placed.divider.right <= placed.items[1].hover_rect.x


def test_each_item_puts_its_cap_after_its_label():
    """Gate 2b: label beside its own key cap, at nearly equal weight."""
    for item in a_layout().items:
        assert item.cap_rect is not None
        assert item.label_rect.right <= item.cap_rect.x


def test_an_item_without_a_cap_gets_no_cap_rect():
    placed = a_layout(items=(panel.StripItem("mode", "Voice", ""),))
    assert placed.items[0].cap_rect is None


def test_no_items_means_no_divider():
    """TRANSCRIBING. Gate 2d."""
    placed = a_layout(items=(), state="Transcribing")
    assert placed.items == ()
    assert placed.divider is None
    assert placed.state_rect is not None


def test_a_wider_panel_moves_the_right_group_but_not_the_left():
    narrow = a_layout(width=420.0)
    wide = a_layout(width=520.0)
    assert narrow.dot.x == wide.dot.x
    assert wide.items[-1].hover_rect.right > narrow.items[-1].hover_rect.right


# --- hit testing ------------------------------------------------------------

def test_hit_test_finds_an_item_under_its_own_centre():
    placed = a_layout()
    for item in placed.items:
        x = item.hover_rect.x + item.hover_rect.w / 2.0
        y = item.hover_rect.y + item.hover_rect.h / 2.0
        assert panel.hit_test(placed, x, y) == item.id


def test_hit_test_misses_the_waveform_band():
    placed = a_layout()
    assert panel.hit_test(placed, 210.0, 20.0) is None


def test_hit_test_misses_the_gap_between_the_groups():
    placed = a_layout()
    gap_x = (placed.state_rect.right + placed.items[0].hover_rect.x) / 2.0
    y = placed.strip.y + placed.strip.h / 2.0
    assert panel.hit_test(placed, gap_x, y) is None


def test_hit_test_misses_outside_the_panel():
    placed = a_layout()
    assert panel.hit_test(placed, -5.0, 50.0) is None
    assert panel.hit_test(placed, 500.0, 50.0) is None
    assert panel.hit_test(placed, 210.0, 200.0) is None


def test_hover_rects_do_not_overlap():
    """Two items claiming the same pixel is how a hover flickers."""
    a, b = a_layout().items
    assert a.hover_rect.right <= b.hover_rect.x
