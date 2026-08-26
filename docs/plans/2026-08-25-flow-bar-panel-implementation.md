# Flow Bar Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Flow Bar into superwhisper's two-band recording panel — a
near-black waveform band over a charcoal control strip — that grows out of the
existing resting pill while you dictate and shrinks back afterwards.

**Architecture:** All layout arithmetic moves into a new pure module,
`vocal_advantage/panel.py`, which computes rects and does hit-testing and
imports no GUI library at all. The two existing renderers (`flowbar_mac.py`,
AppKit; `flowbar_win.py`, Pillow + Win32) consume those rects and compute none
of their own, so the platforms cannot drift. A single eased scalar `open`
(0 = pill, 1 = panel) drives width, height, corner radius, bar count and strip
opacity together, so the grow is always self-consistent.

**Tech Stack:** Python 3.11+, pyobjc (AppKit) on macOS, Pillow + ctypes/Win32 on
Windows, pytest. No new dependencies.

**Spec:** [`2026-08-25-flow-bar-panel.md`](2026-08-25-flow-bar-panel.md). Every
gate number referenced below is from that document.

## Global Constraints

- **No new dependencies.** Everything here uses what `pyproject.toml` already declares.
- **`panel.py` must import nothing but the standard library.** No AppKit, no Win32, no Pillow. Gate 5c.
- **Both renderers take every rect from `panel.py`.** Neither computes one. Gate 5a.
- **Panel geometry is exactly 420 × 96, radius 12**; band 57, hairline 1, strip 38. Gate 1d.
- **Bars are 2.0 wide with 2.0 gaps** — a 1:1 ratio. Gate 1e.
- **Peak bar amplitude is 69% of band height.** Gate 1e.
- **The right group (`Stop`, `Cancel`) appears in `RECORDING` only.** Gate 2d. Drawing a `Stop` that stops nothing is worse than an empty strip.
- **A `flash()` message never opens the panel.** Gate 3f.
- **Click-through is the default state.** It is dropped only while the cursor is inside the panel, and restored on leaving. Gate 4a, 4e.
- **Colours live in `panel.py` as 0–255 integer triples.** AppKit callers divide by 255. This stops the two renderers holding different values, as `PILL_FILL_RGB` does today: `(0.97, 0.965, 0.945)` in the mac file and `(247, 246, 241)` in the Windows one.
- **Every measurement is from `design-research/superwhisper/assets/`**, ÷2 from a 2× capture. Do not "improve" these numbers.
- **Four tests inspect source rather than behaviour, on purpose.** `test_module_is_pure`, `test_the_renderer_computes_no_layout_of_its_own`, `test_panel_height_is_taken_from_the_frame_not_the_constant` and `test_click_through_is_the_default` guard architectural constraints (gates 5a, 5c, 4a) that produce no observable runtime difference until the exact thing they forbid ships to the wrong platform. They are deliberate, not a smell — do not rewrite them as behavioural tests, and do not delete them.
- **Tests must be watched failing before the implementation is written.** If a new test file's first red is `ModuleNotFoundError`, that proves the file runs and nothing about any assertion in it — keep going.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `vocal_advantage/panel.py` | **New.** Geometry constants, palette, `Rect`, `StripItem`, `Placed`, `Layout`, `layout()`, `hit_test()`, `bars_for_open()`, `lerp()`. Pure arithmetic, standard library only. |
| `tests/test_panel.py` | **New.** Unit tests for all of the above. Runs on any machine. |
| `vocal_advantage/waveform.py` | Bar geometry constants change to 2.0 / 2.0; `BUFFER_BARS = 69` added. `ScrollingWave` and the height generators are untouched. |
| `vocal_advantage/flowbar.py` | `Frame` gains `open`, `height`, `radius`, `strip`, `hover`; loses `legend`. `Indicator` eases `open` and builds the strip. Still knows nothing about drawing. |
| `vocal_advantage/flowbar_mac.py` | Draws the panel from `panel.py` rects. View becomes `isFlipped`. Cursor polling and click-through toggling. |
| `vocal_advantage/flowbar_win.py` | `render_frame` draws the panel from the same rects. `_mirrored` is bypassed for the panel. Cursor polling and `WS_EX_TRANSPARENT` toggling. |
| `vocal_advantage/controller.py` | Gains `request_stop()` / `request_cancel()` — recorded on call, performed by `tick`, so a click does not put a third thread inside the state machine. |
| `vocal_advantage/main.py` | Passes the hotkey and `cancel_key` into `Indicator` at **both** sites (977 Windows, 1131 macOS), and the click dispatcher into `_make_flow_bar`. `legend_for` is deleted. |
| `tests/test_flowbar_legend.py` | **Deleted**, superseded by `tests/test_flowbar_strip.py`. |

**Coordinate convention, stated once and relied on everywhere:** `panel.py`
returns rects in **top-left origin, y increasing downward**. That is Pillow's
convention natively. AppKit's is the opposite, so `_PillView` gains
`isFlipped() -> True`, after which AppKit uses top-left too and the same rects
serve both renderers unmodified. The existing bar drawing is symmetric about the
horizontal centre line, so flipping the view does not change it.

---

## Task 1: Bar geometry and the 69-slot buffer

Superwhisper's bars are 2.0 wide with 2.0 gaps — a 1:1 ratio against today's
1.5 / 2.2. The trace buffer grows from 15 slots to 69 so the panel has history
to show; the pill draws a window onto its newest bars.

**Files:**
- Modify: `vocal_advantage/waveform.py:43-51`
- Test: `tests/test_waveform.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `waveform.BAR_WIDTH = 2.0`, `waveform.BAR_GAP = 2.0`,
  `waveform.BUFFER_BARS = 69`. `BAR_COUNT = 15` keeps its meaning: how many
  bars the resting pill shows.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_waveform.py`:

```python
def test_bars_and_gaps_are_equal_width():
    """Superwhisper's trace is 1:1 bar-to-gap; ours was 1.5 : 2.2."""
    assert wf.BAR_WIDTH == wf.BAR_GAP


def test_fifteen_bars_still_clear_the_pill_ends():
    """The pill's ends are round, so bars must not reach the cap's curve.

    15 bars at a 4.0 pitch is 58pt of content in a 78pt pill, leaving 10pt
    margins. That is tighter than the 12pt they had at 1.5/2.2 and still
    clear -- but it is the number BAR_MARGIN_Y's docstring is about, so it
    gets asserted rather than assumed.
    """
    content = wf.BAR_COUNT * wf.BAR_WIDTH + (wf.BAR_COUNT - 1) * wf.BAR_GAP
    assert content == 58.0
    assert (wf.PILL_WIDTH - content) / 2.0 == 10.0


def test_buffer_holds_enough_history_for_the_panel():
    """69 bars at a 4.0 pitch is 274pt -- 65% of a 420pt panel, against
    superwhisper's measured ~66%."""
    assert wf.BUFFER_BARS == 69
    content = wf.BUFFER_BARS * wf.BAR_WIDTH + (wf.BUFFER_BARS - 1) * wf.BAR_GAP
    assert content == 274.0


def test_buffer_holds_about_seven_seconds():
    """BUFFER_BARS * SCROLL_FRAMES / fps, the one number that sets how much
    history is visible. 69 * 6 / 60 = 6.9s, up from 15 * 6 / 60 = 1.5s."""
    seconds = wf.BUFFER_BARS * wf.SCROLL_FRAMES / 60.0
    assert 6.8 < seconds < 7.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_waveform.py -k "equal_width or clear_the_pill or history or seven_seconds" -v`

Expected: FAIL. `test_bars_and_gaps_are_equal_width` fails on `1.5 == 2.2`;
the `BUFFER_BARS` tests fail with `AttributeError: module 'vocal_advantage.waveform' has no attribute 'BUFFER_BARS'`.

- [ ] **Step 3: Write minimal implementation**

In `vocal_advantage/waveform.py`, replace the block at lines 43–51:

```python
#: How many bars the *resting pill* shows. The buffer is longer; see
#: BUFFER_BARS.
BAR_COUNT = 15
#: Bar and gap are equal, which is what makes a bar-style waveform read as a
#: hi-fi VU meter rather than a chart. Measured off superwhisper at 2.0/2.0
#: (4px each in a 2x capture); ours were 1.5/2.2, a 1.47:1 ratio that looked
#: airier and less like the thing being copied.
BAR_WIDTH = 2.0
BAR_GAP = 2.0
#: The full trace history, in bars. The panel draws all of them; the pill draws
#: a window onto the newest few, so growing the panel *reveals* history rather
#: than resetting the trace.
#:
#: 69 bars at a 4.0 pitch is 274pt of content, 65% of the 420pt panel -- against
#: superwhisper's measured ~66%. At SCROLL_FRAMES = 6 and 60fps that is 6.9
#: seconds of visible history, up from 1.5.
BUFFER_BARS = 69
#: Clearance between the tallest bar and the pill's edge. 15 bars of 2.0 with
#: 2.0 gaps is 58pt of content, which leaves 10pt margins inside a 78pt pill.
#: Those margins are load-bearing, not slack: the ends are fully round, so a bar
#: pushed much closer to the edge sits under the curve of the cap and clips
#: against it when the level is high.
BAR_MARGIN_Y = 3.5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_waveform.py -v`

Expected: PASS, all of them.

- [ ] **Step 5: Check nothing else asserted the old numbers**

Run: `python -m pytest tests/ -q`

Expected: PASS. If a test in `test_flowbar_win.py` or `test_flowbar_mac.py`
asserts pixel positions derived from 1.5 / 2.2, update the expected number —
do not revert the constant.

- [ ] **Step 6: Commit**

```bash
git add vocal_advantage/waveform.py tests/test_waveform.py
git commit -m "Match the trace's bar-to-gap ratio to the thing being copied"
```

---

## Task 2: `panel.py` — palette, geometry, and the band layout

The panel's shape, with nothing in the strip yet. Pure arithmetic, no drawing.

**Files:**
- Create: `vocal_advantage/panel.py`
- Test: `tests/test_panel.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Rect(x, y, w, h)` — frozen dataclass, top-left origin, y down.
  - `PANEL_WIDTH = 420.0`, `PANEL_HEIGHT = 96.0`, `PANEL_RADIUS = 12.0`,
    `BAND_HEIGHT = 57.0`, `HAIRLINE = 1.0`, `STRIP_HEIGHT = 38.0`
  - `PILL_RADIUS = 15.0`
  - Palette constants, all `(r, g, b)` 0–255 ints.
  - `lerp(a, b, t) -> float`
  - `bands(width, height) -> tuple[Rect, Rect, Rect]` returning
    `(band, hairline, strip)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_panel.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_panel.py -v`

Expected: FAIL at collection with
`ModuleNotFoundError: No module named 'vocal_advantage.panel'`. That is the
boring red — it proves the file runs and nothing about any assertion in it.

- [ ] **Step 3: Write minimal implementation**

Create `vocal_advantage/panel.py`:

```python
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


def bands(width: float, height: float) -> tuple[Rect, Rect, Rect]:
    """The waveform band, the hairline, and the control strip.

    The strip and hairline keep their fixed heights and the band absorbs the
    remainder, so a part-grown panel still has its strip sitting on the bottom
    edge instead of floating in the middle of the animation.
    """
    strip_h = min(STRIP_HEIGHT, max(0.0, height - HAIRLINE))
    hairline_h = min(HAIRLINE, max(0.0, height - strip_h))
    band_h = max(0.0, height - strip_h - hairline_h)
    return (
        Rect(0.0, 0.0, width, band_h),
        Rect(0.0, band_h, width, hairline_h),
        Rect(0.0, band_h + hairline_h, width, strip_h),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_panel.py -v`

Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
git add vocal_advantage/panel.py tests/test_panel.py
git commit -m "Give the panel a layout module that draws nothing"
```

---

## Task 3: `panel.py` — the strip's items, hit-testing, and bar count

Where `Stop`, `Cancel`, the dot and the state word sit, and which of them the
cursor is over.

**Files:**
- Modify: `vocal_advantage/panel.py`
- Test: `tests/test_panel.py`

**Interfaces:**
- Consumes: `Rect`, `bands`, `lerp`, the palette (Task 2).
- Produces:
  - `StripItem(id: str, label: str, cap: str)` — frozen dataclass.
  - `Placed(id, hover_rect, label_rect, label, cap, cap_rect)` — frozen.
  - `Layout(width, height, radius, band, hairline, strip, dot, state_rect, state_label, items, divider)` — frozen.
  - `layout(width, height, radius, state_label, items) -> Layout`
  - `hit_test(placed_layout, x, y) -> str | None`
  - `bars_for_open(open_) -> int`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_panel.py`:

```python
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

def a_layout(width=420.0, height=96.0, items=None, state="Recording"):
    if items is None:
        items = (
            panel.StripItem("stop", "Stop", "F8"),
            panel.StripItem("cancel", "Cancel", "Esc"),
        )
    return panel.layout(width, height, panel.PANEL_RADIUS, state, items)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_panel.py -v`

Expected: FAIL. The Task 2 tests still pass; the new ones fail with
`AttributeError: module 'vocal_advantage.panel' has no attribute 'bars_for_open'`
and `... has no attribute 'StripItem'`.

- [ ] **Step 3: Write minimal implementation**

First add the waveform import to the top of `vocal_advantage/panel.py`, beside
`from dataclasses import dataclass`:

```python
from vocal_advantage import waveform as wf
```

`waveform` imports only `math` and `numpy`, so this keeps gate 5c intact — and
there is no cycle, because nothing in `waveform` knows about `panel`.

Then append:

```python
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
    state_label: str,
    items: tuple[StripItem, ...] = (),
) -> Layout:
    """Everything a renderer needs to draw one panel, and nothing else."""
    band, hairline, strip = bands(width, height)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_panel.py -v`

Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
git add vocal_advantage/panel.py tests/test_panel.py
git commit -m "Lay out the strip right-to-left, so the gap absorbs the slack"
```

---

## Task 4: `flowbar.py` — one scalar drives the whole grow

`Frame` learns about the panel. `Indicator` eases a single `open` value that
width, height, radius, bar count and strip opacity all derive from, so they
cannot fall out of step.

**Files:**
- Modify: `vocal_advantage/flowbar.py`
- Delete: `tests/test_flowbar_legend.py`
- Test: `tests/test_flowbar_strip.py` (new)

**Interfaces:**
- Consumes: `panel.PANEL_WIDTH`, `panel.PANEL_HEIGHT`, `panel.PANEL_RADIUS`,
  `panel.PILL_RADIUS`, `panel.StripItem`, `panel.bars_for_open`,
  `waveform.BUFFER_BARS` (Tasks 1–3).
- Produces: `Frame` with new fields `open: float`, `height: float`,
  `radius: float`, `strip: tuple[panel.StripItem, ...]`, `hover: str`, and
  **without** `legend`. `Indicator(level_source, hotkey, cancel_key)` replacing
  the `legend` argument, and `Indicator.next_frame(hover="")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_flowbar_strip.py`:

```python
"""The pill/panel grow, and what the control strip says in each state.

Supersedes tests/test_flowbar_legend.py: the legend was a line of prose beside
the trace, and it is now a laid-out strip of controls.
"""

import pytest

from vocal_advantage import flowbar, panel
from vocal_advantage import waveform as wf


def an_indicator(**kwargs):
    kwargs.setdefault("hotkey", "F8")
    kwargs.setdefault("cancel_key", "Esc")
    return flowbar.Indicator(**kwargs)


def settle(indicator, frames=200):
    """Run the ease to completion and return the final frame."""
    frame = None
    for _ in range(frames):
        frame = indicator.next_frame()
    return frame


# --- the grow ---------------------------------------------------------------

def test_idle_rests_as_the_pill():
    frame = settle(an_indicator())
    assert frame.open == pytest.approx(0.0, abs=1e-3)
    assert frame.width == pytest.approx(wf.PILL_WIDTH, abs=0.5)
    assert frame.height == pytest.approx(wf.PILL_HEIGHT, abs=0.5)
    assert frame.radius == pytest.approx(panel.PILL_RADIUS, abs=0.1)


def test_recording_grows_to_the_panel():
    indicator = an_indicator()
    indicator.show_recording()
    frame = settle(indicator)
    assert frame.open == pytest.approx(1.0, abs=1e-3)
    assert frame.width == pytest.approx(panel.PANEL_WIDTH, abs=0.5)
    assert frame.height == pytest.approx(panel.PANEL_HEIGHT, abs=0.5)
    assert frame.radius == pytest.approx(panel.PANEL_RADIUS, abs=0.1)


def test_width_height_and_radius_never_move_independently():
    """Gate 3b. One scalar drives all three, so a frame can never be a wide
    pill or a tall lozenge."""
    indicator = an_indicator()
    indicator.show_recording()
    for _ in range(40):
        frame = indicator.next_frame()
        assert frame.width == pytest.approx(
            panel.lerp(wf.PILL_WIDTH, panel.PANEL_WIDTH, frame.open), abs=0.6
        )
        assert frame.height == pytest.approx(
            panel.lerp(wf.PILL_HEIGHT, panel.PANEL_HEIGHT, frame.open), abs=0.6
        )
        assert frame.radius == pytest.approx(
            panel.lerp(panel.PILL_RADIUS, panel.PANEL_RADIUS, frame.open),
            abs=0.2,
        )


def test_the_grow_is_a_fade_not_a_cut():
    indicator = an_indicator()
    indicator.show_recording()
    first = indicator.next_frame()
    assert 0.0 < first.open < 1.0


def test_transcribing_stays_open():
    indicator = an_indicator()
    indicator.show_recording()
    settle(indicator)
    indicator.show_processing()
    frame = settle(indicator)
    assert frame.open == pytest.approx(1.0, abs=1e-3)


def test_hiding_shrinks_back_to_the_pill():
    indicator = an_indicator()
    indicator.show_recording()
    settle(indicator)
    indicator.hide()
    frame = settle(indicator)
    assert frame.open == pytest.approx(0.0, abs=1e-3)


def test_a_message_never_opens_the_panel():
    """Gate 3f. A panel is for dictating; 'could not paste' is not."""
    indicator = an_indicator()
    indicator.flash("could not paste - press Cmd+V")
    for _ in range(30):
        frame = indicator.next_frame()
        assert frame.open == pytest.approx(0.0, abs=1e-3)
    assert frame.width > wf.PILL_WIDTH


# --- the trace across the grow ---------------------------------------------

def test_the_buffer_is_the_long_one():
    indicator = an_indicator()
    settle(indicator)
    indicator.show_recording()
    frame = settle(indicator)
    assert len(frame.heights) == wf.BUFFER_BARS


def test_the_pill_windows_the_newest_bars():
    """Gate 3e. Not a separate buffer -- a window onto the same history."""
    frame = settle(an_indicator())
    assert len(frame.heights) == wf.BAR_COUNT


def test_the_grow_reveals_history_rather_than_clearing_it():
    indicator = an_indicator()
    indicator.show_recording()
    counts = [len(indicator.next_frame().heights) for _ in range(60)]
    assert counts == sorted(counts)
    assert counts[0] >= wf.BAR_COUNT
    assert counts[-1] <= wf.BUFFER_BARS


# --- what the strip says ----------------------------------------------------

def test_recording_shows_stop_and_cancel():
    """Gate 2b."""
    indicator = an_indicator(hotkey="F8")
    indicator.show_recording()
    frame = settle(indicator)
    assert [item.id for item in frame.strip] == ["stop", "cancel"]


def test_the_stop_cap_is_the_configured_hotkey():
    """Gate 2e."""
    indicator = an_indicator(hotkey="ctrl+alt+d")
    indicator.show_recording()
    frame = settle(indicator)
    stop = next(item for item in frame.strip if item.id == "stop")
    assert stop.cap == "ctrl+alt+d"
    assert stop.label == "Stop"


def test_cancel_is_bound_to_esc():
    indicator = an_indicator()
    indicator.show_recording()
    frame = settle(indicator)
    cancel = next(item for item in frame.strip if item.id == "cancel")
    assert cancel.cap == "Esc"


def test_no_cancel_control_when_esc_is_itself_the_hotkey():
    """`legend_for` already enforced this and must not lose it: _handle_down
    gives the hotkey precedence, so a Cancel here would be a lie."""
    indicator = an_indicator(hotkey="esc", cancel_key="")
    indicator.show_recording()
    frame = settle(indicator)
    assert [item.id for item in frame.strip] == ["stop"]


def test_transcribing_shows_no_controls():
    """Gate 2d, and the reasoning LEGEND_STATES already carried: once the
    model has the audio, no key stops it and none bins the result, so
    anything shown here would be false."""
    indicator = an_indicator()
    indicator.show_processing()
    frame = settle(indicator)
    assert frame.strip == ()


def test_idle_shows_no_controls():
    assert settle(an_indicator()).strip == ()


def test_a_message_shows_no_controls():
    indicator = an_indicator()
    indicator.flash("could not paste")
    assert indicator.next_frame().strip == ()


# --- hover ------------------------------------------------------------------

def test_hover_defaults_to_nothing():
    assert settle(an_indicator()).hover == ""


def test_hover_is_carried_through_to_the_frame():
    indicator = an_indicator()
    indicator.show_recording()
    frame = indicator.next_frame(hover="cancel")
    assert frame.hover == "cancel"


def test_the_legend_is_gone():
    """It was prose beside the trace; it is a laid-out strip now."""
    assert not hasattr(flowbar.Frame, "legend")
    assert not hasattr(flowbar, "legend_width")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_flowbar_strip.py -v`

Expected: FAIL. `TypeError: Indicator.__init__() got an unexpected keyword
argument 'hotkey'` on nearly every test.

- [ ] **Step 3: Write the implementation**

In `vocal_advantage/flowbar.py`:

1. Import panel at the top, beside the waveform import:

```python
from vocal_advantage import panel
from vocal_advantage import waveform as wf
```

2. Replace the `LEGEND_STATES` block (lines 54–77, through `LEGEND_GAP`) with:

```python
#: The states that open the panel. Everything else rests as the pill.
#:
#: A message is deliberately absent: it widens the pill to fit its text, as it
#: always has, but it does not open the panel. A panel is for dictating, and
#: "could not paste" should not need one.
PANEL_STATES = frozenset({RECORDING, TRANSCRIBING})

#: The states that show the strip's right-hand controls.
#:
#: RECORDING only, and the exclusions are each a decision. Not IDLE: the
#: resting pill is what sits over your work all day, and a standing reminder of
#: a key you are not currently holding is clutter. Not MESSAGE: "could not
#: paste" is urgent and a reminder is not. Not TRANSCRIBING either, which is
#: the one that looks wrong and is not -- once the model has the audio, no key
#: stops it and none bins the result, so anything shown there would be false.
CONTROL_STATES = frozenset({RECORDING})

```

**Do not define `CANCEL_KEY` here.** It already lives in `main.py`, and
`legend_for` uses it to enforce a rule that must survive this change: **when
Esc *is* the hotkey, no Cancel control is shown at all.** `_handle_down` gives
the hotkey precedence in that case, so a Cancel on the strip would be
advertising something that cannot happen. `Indicator` takes `cancel_key` as an
argument and omits the control when it is empty; `main.py` decides.

3. Replace the `Frame` dataclass:

```python
@dataclass(frozen=True)
class Frame:
    """Everything a renderer needs for one moment in time, and nothing else."""

    state: str
    heights: tuple[float, ...]
    text: str
    width: float
    pill_alpha: float
    bar_alpha: float
    text_alpha: float
    #: 0 = the resting pill, 1 = the open panel. The single scalar the whole
    #: grow derives from: width, height, radius, bar count and strip opacity
    #: are all read off it, so no two of them can fall out of step.
    open: float = 0.0
    height: float = float(wf.PILL_HEIGHT)
    radius: float = panel.PILL_RADIUS
    #: The strip's right-hand controls. Empty in every state but RECORDING.
    strip: tuple[panel.StripItem, ...] = ()
    #: The id of the item under the cursor, or "". Supplied by the platform
    #: layer, which is the only thing that knows where the cursor is.
    hover: str = ""
```

4. In `Indicator.__init__`, replace the `legend` parameter and its
   `n_bars` default:

```python
    def __init__(
        self,
        level_source=None,
        n_bars: int = wf.BUFFER_BARS,
        hotkey: str = "",
        cancel_key: str = "",
    ) -> None:
        ...
        #: Named by the caller, because the hotkey lives in main.py and this
        #: module deliberately knows nothing about hotkeys.
        self._hotkey = hotkey
        self._cancel_key = cancel_key
        ...
        self._open = 0.0
```

   Delete `self._legend = legend`. Keep everything else in `__init__` as it is.

5. Replace the body of `next_frame` from the `legend = ...` line to the
   `return Frame(...)`:

```python
    def next_frame(self, hover: str = "") -> Frame:
        """Drain, advance one frame of motion, and return what to draw.

        `hover` comes from the platform layer, which is the only thing that
        knows where the cursor is. It is one frame stale by the time it is
        drawn, which at 60fps nobody can see.
        """
        while True:
            try:
                self._mode, self._text = self._commands.get_nowait()
            except queue.Empty:
                break
            self._frames = 0

        if self._mode == MESSAGE and self._frames >= MESSAGE_FRAMES:
            self._mode, self._text = IDLE, ""

        heights = self._advance_wave()

        self._open = _ease(
            self._open, 1.0 if self._mode in PANEL_STATES else 0.0, FADE_ALPHA
        )
        target_width = (
            panel.PANEL_WIDTH
            if self._mode in PANEL_STATES
            else message_width(self._text)
            if self._mode == MESSAGE
            else float(wf.PILL_WIDTH)
        )
        self._width = _ease(self._width, target_width, FADE_ALPHA)
        self._pill_alpha = _ease(
            self._pill_alpha, PILL_ALPHA[self._mode], FADE_ALPHA
        )
        self._bar_alpha = _ease(
            self._bar_alpha, BAR_ALPHA[self._mode], FADE_ALPHA
        )
        self._text_alpha = _ease(
            self._text_alpha, TEXT_ALPHA[self._mode], FADE_ALPHA
        )

        # Sliced, not regenerated: the buffer always holds BUFFER_BARS of real
        # history and the pill shows a window onto its newest. Index 0 is the
        # newest, so this keeps the recent end and drops the old.
        self._heights = heights[: panel.bars_for_open(self._open)]

        self._frames += 1
        return Frame(
            state=self._mode,
            heights=self._heights,
            text=self._text if self._mode == MESSAGE else "",
            width=self._width,
            pill_alpha=self._pill_alpha,
            bar_alpha=self._bar_alpha,
            text_alpha=self._text_alpha,
            open=self._open,
            height=panel.lerp(
                float(wf.PILL_HEIGHT), panel.PANEL_HEIGHT, self._open
            ),
            radius=panel.lerp(
                panel.PILL_RADIUS, panel.PANEL_RADIUS, self._open
            ),
            strip=self._strip(),
            hover=hover,
        )

    def _strip(self) -> tuple[panel.StripItem, ...]:
        """The strip's right-hand controls, for this state.

        Cancel is dropped when `cancel_key` is empty, which is how the caller
        says Esc is itself the hotkey. `_handle_down` gives the hotkey
        precedence there, so a Cancel control would be advertising something
        that cannot happen -- the rule `legend_for` already enforced, moved
        onto the strip rather than lost with the legend.
        """
        if self._mode not in CONTROL_STATES:
            return ()
        items = [panel.StripItem("stop", "Stop", self._hotkey)]
        if self._cancel_key:
            items.append(panel.StripItem("cancel", "Cancel", self._cancel_key))
        return tuple(items)
```

6. Delete `legend_width`, `LEGEND_CHAR_WIDTH` and `LEGEND_GAP`. Keep
   `message_width`, `MESSAGE_CHAR_WIDTH` and `MESSAGE_PADDING`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_flowbar_strip.py -v`

Expected: PASS.

- [ ] **Step 5: Retire the superseded test file and fix the callers**

```bash
git rm tests/test_flowbar_legend.py
```

Run: `python -m pytest tests/ -q`

Expected: failures in `test_flowbar.py`, `test_flowbar_mac.py`,
`test_flowbar_win.py` and `test_main.py` wherever they build a `Frame` with
`legend=` or construct `Indicator(legend=...)`. Update each to the new field
names. Do not reintroduce `legend`.

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q`

Expected: PASS. The renderers still draw pills — they ignore the new fields
until Tasks 5 and 6 — which is correct at this point.

- [ ] **Step 7: Commit**

```bash
git add -A vocal_advantage/flowbar.py tests/
git commit -m "Drive the whole grow from one eased number"
```

---

## Task 5: `flowbar_mac.py` — draw the panel

The first point at which any of this is on screen.

**Files:**
- Modify: `vocal_advantage/flowbar_mac.py`
- Test: `tests/test_flowbar_mac.py`

**Interfaces:**
- Consumes: `panel.layout`, the palette, `Frame.open/height/radius/strip/hover`.
- Produces: no new public API. `_PillView.isFlipped()` returns `True`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_flowbar_mac.py`, following the skip pattern already in
that file for AppKit-dependent cases:

```python
def test_the_view_is_flipped():
    """panel.py returns top-left-origin rects, which is Pillow's convention.
    A flipped NSView adopts it, so one set of rects serves both renderers.
    """
    assert flowbar_mac._PillView.isFlipped(None) is True


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
```

`_a_visible_frame` is whatever helper `tests/test_flowbar_mac.py` already uses
to fake an `NSScreen.visibleFrame`. Reuse it; do not write a second one. Find
it with `grep -n "visible" tests/test_flowbar_mac.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_flowbar_mac.py -v`

Expected: FAIL — `AttributeError: type object '_PillView' has no attribute
'isFlipped'`, and `_resize` still names `PILL_HEIGHT`.

- [ ] **Step 3: Write the implementation**

In `vocal_advantage/flowbar_mac.py`:

1. Import the layout module:

```python
from vocal_advantage import panel
```

2. Delete the local `PILL_FILL_RGB` and `BAR_RGB` constants. Add a converter
   beside `ensure_app`:

```python
def _colour(rgb, alpha: float):
    """A 0-255 triple from `panel` as an NSColor.

    The conversion lives here rather than in `panel`, which must stay free of
    AppKit. One representation, one place it is converted.
    """
    red, green, blue = rgb
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(
        red / 255.0, green / 255.0, blue / 255.0, alpha
    )


def _vertical_gradient(rect, top_rgb, bottom_rgb, alpha: float) -> None:
    """Fill `rect` with a vertical blend. Nothing in this panel is flat."""
    NSGradient.alloc().initWithStartingColor_endingColor_(
        _colour(top_rgb, alpha), _colour(bottom_rgb, alpha)
    ).drawInRect_angle_(rect, 270.0)
```

   Add `NSGradient` to the guarded AppKit import block and to the
   `except ImportError` stub assignments.

3. Add `isFlipped` to `_PillView`:

```python
    def isFlipped(self) -> bool:
        """Top-left origin, y down -- matching `panel` and Pillow.

        The bars are symmetric about the horizontal centre line, so this does
        not change how they draw. It exists so the strip's rects can be used
        exactly as `panel.layout` returns them.
        """
        return True
```

4. Replace `drawRect_`:

```python
    def drawRect_(self, _dirty) -> None:
        data = getattr(self, "_data", None)
        if data is None:
            return

        bounds = self.bounds()
        width = bounds.size.width
        height = bounds.size.height
        placed = panel.layout(
            width, height, data.radius,
            flowbar.STATUS_TEXT.get(data.state, ""),
            data.strip,
        )

        clip = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, width, height), data.radius, data.radius
        )
        NSGraphicsContext.currentContext().saveGraphicsState()
        clip.addClip()

        # The pill's single fill fades out as the panel's two bands fade in, so
        # the shape is never momentarily both and never momentarily neither.
        if data.open < 0.999:
            _colour(panel.PILL_FILL_RGB, data.pill_alpha * (1.0 - data.open)).set()
            NSBezierPath.bezierPathWithRect_(
                NSMakeRect(0, 0, width, height)
            ).fill()
        if data.open > 0.001:
            band_alpha = data.pill_alpha * data.open
            _vertical_gradient(
                _rect(placed.band), panel.BAND_TOP_RGB,
                panel.BAND_BOTTOM_RGB, band_alpha,
            )
            _vertical_gradient(
                _rect(placed.strip), panel.STRIP_TOP_RGB,
                panel.STRIP_BOTTOM_RGB, band_alpha,
            )
            _colour(panel.HAIRLINE_RGB, band_alpha).set()
            NSBezierPath.bezierPathWithRect_(_rect(placed.hairline)).fill()

        NSGraphicsContext.currentContext().restoreGraphicsState()

        _colour(panel.BORDER_RGB, data.pill_alpha).set()
        clip.setLineWidth_(1.0)
        clip.stroke()

        if data.bar_alpha > 0.01:
            self._draw_bars(data, placed)
        if data.open > 0.01:
            self._draw_strip(data, placed)
        if data.text and data.text_alpha > 0.01:
            self._draw_message(data, width, height)
        if getattr(self, "_movable", False):
            self._draw_move_outline(width, height)
```

   Add a module-level helper:

```python
def _rect(r) -> "NSMakeRect":
    """A `panel.Rect` as an NSRect. Safe because the view is flipped."""
    return NSMakeRect(r.x, r.y, r.w, r.h)
```

   Add `NSGraphicsContext` to the guarded import block and its stub.

5. Replace `_draw_bars` so it centres on the band rather than the whole view,
   and delete `_draw_legend` entirely:

```python
    def _draw_bars(self, data, placed) -> None:
        band = placed.band
        if band.h <= 0.0:
            return
        centre_y = band.y + band.h / 2.0
        # 69% of band height at peak, mirrored -- so the tallest bar's half is
        # 0.345 of the band. Measured off superwhisper, not chosen.
        max_half = band.h * 0.345

        _colour(panel.BAR_RGB, data.bar_alpha).set()
        for x, normalised in zip(
            wf.bar_layout(band.w, len(data.heights)), data.heights
        ):
            half = normalised * max_half
            total = max(wf.BAR_WIDTH, half * 2.0)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(
                    band.x + x - wf.BAR_WIDTH / 2.0, centre_y - total / 2.0,
                    wf.BAR_WIDTH, total,
                ),
                wf.BAR_WIDTH / 2.0, wf.BAR_WIDTH / 2.0,
            ).fill()
```

6. Add `_draw_strip`:

```python
    def _draw_strip(self, data, placed) -> None:
        """The dot, the state word, and each control beside its own key cap.

        Alpha rides `open` throughout, so the strip fades in as the panel
        widens rather than drawing squashed into a part-grown one.
        """
        alpha = data.open
        if placed.dot is not None:
            dot_rgb = panel.DOT_RECORDING_RGB
            if data.state == flowbar.TRANSCRIBING:
                dot_rgb = panel.DOT_TRANSCRIBING_RGB
            _colour(dot_rgb, alpha).set()
            NSBezierPath.bezierPathWithOvalInRect_(_rect(placed.dot)).fill()

        if placed.state_rect is not None and placed.state_label:
            self._text(
                placed.state_label, placed.state_rect,
                panel.LABEL_FONT_SIZE, panel.TEXT_RGB, alpha,
            )

        for item in placed.items:
            if item.id == data.hover:
                _colour(panel.HOVER_FILL_RGB, alpha).set()
                radius = item.hover_rect.h / 2.0
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    _rect(item.hover_rect), radius, radius
                ).fill()
            self._text(
                item.label, item.label_rect,
                panel.LABEL_FONT_SIZE, panel.TEXT_RGB, alpha,
            )
            if item.cap_rect is not None:
                _colour(panel.CAP_FILL_RGB, alpha).set()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    _rect(item.cap_rect), panel.CAP_RADIUS, panel.CAP_RADIUS
                ).fill()
                self._text(
                    item.cap, item.cap_rect,
                    panel.CAP_FONT_SIZE, panel.TEXT_RGB, alpha,
                    centred=True,
                )

        if placed.divider is not None:
            _colour(panel.HAIRLINE_RGB, alpha).set()
            NSBezierPath.bezierPathWithRect_(_rect(placed.divider)).fill()

    def _text(self, string, rect, size, rgb, alpha, centred=False) -> None:
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(
            NSTextAlignmentCenter if centred else NSTextAlignmentLeft
        )
        red, green, blue = rgb
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(size),
            NSForegroundColorAttributeName:
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    red / 255.0, green / 255.0, blue / 255.0, alpha
                ),
            NSParagraphStyleAttributeName: style,
        }
        text = NSString.stringWithString_(string)
        measured = text.sizeWithAttributes_(attributes)
        NSString.stringWithString_(string).drawInRect_withAttributes_(
            NSMakeRect(
                rect.x, rect.y + (rect.h - measured.height) / 2.0,
                max(rect.w, measured.width), measured.height,
            ),
            attributes,
        )
```

   Add `NSBezierPath.bezierPathWithOvalInRect_` needs no import; it is a class
   method on the already-imported `NSBezierPath`.

7. In `FlowBar._tick`, pass the frame's height through to `_resize`, and change
   `_resize` to take both:

```python
    def _resize(self, width: float, height: float) -> None:
        origin = self._origin(width, height)
        self._panel.setFrame_display_(
            NSMakeRect(origin[0], origin[1], width, height), True
        )
```

   Every call site changes from `self._resize(frame.width)` to
   `self._resize(frame.width, frame.height)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_flowbar_mac.py -v`

Expected: PASS.

- [ ] **Step 5: Look at it**

Run: `python -m vocal_advantage`

Press the hotkey. Expected: the pill grows upward into a two-band panel, its
bottom edge stationary; the band is near-black with white bars; the strip reads
`● Recording   Stop [F8] │ Cancel [Esc]`; releasing shrinks it back.

Gates 1a–1e, 2a–2c, 2e, 3a–3d. Tick them in the spec, or write down which
failed and what it looked like.

- [ ] **Step 6: Commit**

```bash
git add vocal_advantage/flowbar_mac.py tests/test_flowbar_mac.py
git commit -m "Draw the panel on macOS, from rects it does not compute"
```

---

## Task 6: `flowbar_win.py` — the same panel in Pillow

Verifiable on this Mac by writing PNGs, because `render_frame` touches no
Win32 at all.

**Files:**
- Modify: `vocal_advantage/flowbar_win.py:249-310`
- Test: `tests/test_flowbar_win.py`

**Interfaces:**
- Consumes: `panel.layout`, the palette, the same `Frame` fields as Task 5.
- Produces: `render_frame(frame, width, height)` unchanged in signature.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_flowbar_win.py`:

```python
def a_panel_frame(**kwargs):
    from vocal_advantage import panel
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
    """Gate 1c. Lighter than either band, on all four edges."""
    image = render_frame(a_panel_frame(), 420, 96).convert("RGB")
    edge = image.getpixel((210, 0))
    inside = image.getpixel((210, 8))
    assert sum(edge) > sum(inside)


def test_the_tallest_bar_is_about_69_percent_of_the_band():
    """Gate 1e. Measured off superwhisper, not chosen -- a full-height trace
    reads as clipping and a short one reads as a dead microphone."""
    from vocal_advantage import panel
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_flowbar_win.py -v`

Expected: FAIL. `test_the_two_bands_are_different_colours` fails because
`render_frame` still paints one flat pill fill.

- [ ] **Step 3: Write the implementation**

In `vocal_advantage/flowbar_win.py`:

1. `from vocal_advantage import panel`, and delete the local `PILL_FILL_RGB`
   and `BAR_RGB`.

2. Add a gradient helper:

```python
def _gradient(draw, rect, top_rgb, bottom_rgb, alpha, scale):
    """A vertical blend, drawn a row at a time.

    Pillow has no gradient primitive. One horizontal line per pixel row is
    crude and exactly good enough: the bands are under 60pt tall and this
    renders once per frame into a supersampled buffer.
    """
    height = max(1.0, rect.h * scale)
    for step in range(int(height)):
        t = step / height
        colour = tuple(
            int(round(top_rgb[i] + (bottom_rgb[i] - top_rgb[i]) * t))
            for i in range(3)
        )
        y = rect.y * scale + step
        draw.rectangle(
            (rect.x * scale, y, (rect.x + rect.w) * scale - 1, y),
            fill=colour + (alpha,),
        )
```

3. Replace `render_frame`'s body between the `image = Image.new(...)` line and
   the `return`:

```python
    alpha = int(round(_clamp01(frame.pill_alpha) * 255))
    placed = panel.layout(
        float(width), float(height), frame.radius,
        flowbar.STATUS_TEXT.get(frame.state, ""),
        frame.strip,
    )
    radius = frame.radius * scale

    if frame.open < 0.999:
        draw.rounded_rectangle(
            (0, 0, width * scale - 1, height * scale - 1),
            radius=radius,
            fill=panel.PILL_FILL_RGB
            + (int(round(alpha * (1.0 - frame.open))),),
        )
    if frame.open > 0.001:
        band_alpha = int(round(alpha * frame.open))
        _gradient(draw, placed.band, panel.BAND_TOP_RGB,
                  panel.BAND_BOTTOM_RGB, band_alpha, scale)
        _gradient(draw, placed.strip, panel.STRIP_TOP_RGB,
                  panel.STRIP_BOTTOM_RGB, band_alpha, scale)
        draw.rectangle(
            (placed.hairline.x * scale, placed.hairline.y * scale,
             placed.hairline.right * scale - 1,
             placed.hairline.bottom * scale - 1),
            fill=panel.HAIRLINE_RGB + (band_alpha,),
        )
        # Clip the square-cornered gradients back to the rounded shape by
        # punching the corners out with a rounded-rectangle mask.
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width * scale - 1, height * scale - 1),
            radius=radius, fill=255,
        )
        image.putalpha(
            Image.composite(image.getchannel("A"),
                            Image.new("L", image.size, 0), mask)
        )
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, width * scale - 1, height * scale - 1),
            radius=radius, outline=panel.BORDER_RGB + (alpha,),
            width=int(scale),
        )

    bar_alpha = int(round(_clamp01(frame.bar_alpha) * 255))
    if bar_alpha > 2 and placed.band.h > 0:
        centre_y = (placed.band.y + placed.band.h / 2.0) * scale
        max_half = placed.band.h * 0.345 * scale
        bar_width = wf.BAR_WIDTH * scale
        for x, normalised in zip(
            wf.bar_layout(placed.band.w * scale, len(frame.heights),
                          bar_width, wf.BAR_GAP * scale),
            frame.heights,
        ):
            half = normalised * max_half
            total = max(bar_width, half * 2.0)
            draw.rounded_rectangle(
                (placed.band.x * scale + x - bar_width / 2.0,
                 centre_y - total / 2.0,
                 placed.band.x * scale + x + bar_width / 2.0,
                 centre_y + total / 2.0),
                radius=bar_width / 2.0,
                fill=panel.BAR_RGB + (bar_alpha,),
            )

    if frame.open > 0.01:
        _draw_strip(draw, placed, frame, scale)

    # `_mirrored` forces symmetry about the horizontal centre line, which is
    # right for a pill and actively wrong for a panel: the two bands differ by
    # design, and mirroring would paint the waveform band over the strip.
    if frame.open < 0.001:
        image = _mirrored(image)
    return image.resize((width, height), Image.LANCZOS)
```

4. Add `_draw_strip`, using `ImageFont.load_default(size)` so the module needs
   no font file:

```python
def _draw_strip(draw, placed, frame, scale):
    """The dot, the state word, and each control beside its own key cap."""
    alpha = int(round(_clamp01(frame.open) * 255))

    def font(size):
        try:
            return ImageFont.load_default(size * scale)
        except TypeError:      # Pillow < 10.1 takes no size argument
            return ImageFont.load_default()

    if placed.dot is not None:
        rgb = (panel.DOT_TRANSCRIBING_RGB
               if frame.state == flowbar.TRANSCRIBING
               else panel.DOT_RECORDING_RGB)
        draw.ellipse(
            (placed.dot.x * scale, placed.dot.y * scale,
             placed.dot.right * scale, placed.dot.bottom * scale),
            fill=rgb + (alpha,),
        )
    if placed.state_rect is not None and placed.state_label:
        draw.text(
            (placed.state_rect.x * scale, placed.state_rect.y * scale),
            placed.state_label, font=font(panel.LABEL_FONT_SIZE),
            fill=panel.TEXT_RGB + (alpha,),
        )
    for item in placed.items:
        if item.id == frame.hover:
            draw.rounded_rectangle(
                (item.hover_rect.x * scale, item.hover_rect.y * scale,
                 item.hover_rect.right * scale, item.hover_rect.bottom * scale),
                radius=item.hover_rect.h * scale / 2.0,
                fill=panel.HOVER_FILL_RGB + (alpha,),
            )
        draw.text(
            (item.label_rect.x * scale, item.label_rect.y * scale),
            item.label, font=font(panel.LABEL_FONT_SIZE),
            fill=panel.TEXT_RGB + (alpha,),
        )
        if item.cap_rect is not None:
            draw.rounded_rectangle(
                (item.cap_rect.x * scale, item.cap_rect.y * scale,
                 item.cap_rect.right * scale, item.cap_rect.bottom * scale),
                radius=panel.CAP_RADIUS * scale,
                fill=panel.CAP_FILL_RGB + (alpha,),
            )
            draw.text(
                ((item.cap_rect.x + panel.CAP_PAD_X) * scale,
                 (item.cap_rect.y + 4.0) * scale),
                item.cap, font=font(panel.CAP_FONT_SIZE),
                fill=panel.TEXT_RGB + (alpha,),
            )
    if placed.divider is not None:
        draw.rectangle(
            (placed.divider.x * scale, placed.divider.y * scale,
             placed.divider.right * scale, placed.divider.bottom * scale),
            fill=panel.HAIRLINE_RGB + (alpha,),
        )
```

   Add `ImageFont` to the Pillow import at the top of the file.

5. In `FlowBar._draw`, pass `frame.height` where `wf.PILL_HEIGHT` is used.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_flowbar_win.py -v`

Expected: PASS.

- [ ] **Step 5: Write a PNG and look at it**

```bash
python -c "
from vocal_advantage import flowbar, panel
from vocal_advantage.flowbar_win import render_frame
f = flowbar.Frame(
    state=flowbar.RECORDING, heights=(0.4,)*69, text='',
    width=420.0, pill_alpha=1.0, bar_alpha=1.0, text_alpha=0.0,
    open=1.0, height=96.0, radius=12.0,
    strip=(panel.StripItem('stop','Stop','F8'),
           panel.StripItem('cancel','Cancel','Esc')), hover='cancel')
render_frame(f, 420, 96).save('/tmp/panel-win.png')
print('wrote /tmp/panel-win.png')
"
```

Open it. Expected: the same panel as macOS draws — two bands, hairline, dot,
`Recording`, `Stop [F8] │ Cancel [Esc]`, with a hover pill behind `Cancel`.

This is gate 5b, and it is the whole of what can be verified about Windows from
this machine. **Do not record the Win32 side as verified.**

- [ ] **Step 6: Commit**

```bash
git add vocal_advantage/flowbar_win.py tests/test_flowbar_win.py
git commit -m "Draw the same panel with Pillow, and stop mirroring it"
```

---

## Task 7: Hover and click-through, on both platforms

Click-through by default; interactive only under the cursor.

**Files:**
- Modify: `vocal_advantage/flowbar_mac.py`, `vocal_advantage/flowbar_win.py`
- Test: `tests/test_panel.py`, `tests/test_flowbar_mac.py`

**Interfaces:**
- Consumes: `panel.hit_test`, `Frame.width/height/radius/strip`.
- Produces: `FlowBar._hover_for(screen_x, screen_y) -> str` on both classes —
  pure given an origin, so it can be tested without a cursor.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_flowbar_mac.py`:

```python
def test_hover_is_empty_when_the_cursor_is_elsewhere():
    bar = flowbar_mac.FlowBar.__new__(flowbar_mac.FlowBar)
    bar._last_layout = None
    assert bar._hover_for(0.0, 0.0) == ""


def test_hover_names_the_item_under_the_cursor():
    from vocal_advantage import panel
    bar = flowbar_mac.FlowBar.__new__(flowbar_mac.FlowBar)
    bar._last_layout = panel.layout(
        420.0, 96.0, 12.0, "Recording",
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


def test_click_through_is_the_default():
    """Gate 4a. The three guards in this file's docstring are why."""
    import inspect
    source = inspect.getsource(flowbar_mac.FlowBar)
    assert "setIgnoresMouseEvents_(True)" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_flowbar_mac.py -k hover -v`

Expected: FAIL — `AttributeError: 'FlowBar' object has no attribute '_hover_for'`.

- [ ] **Step 3: Write the implementation — macOS**

In `flowbar_mac.py`'s `FlowBar`:

```python
    def _hover_for(self, screen_x: float, screen_y: float) -> str:
        """Which strip item the cursor is over, in screen coordinates.

        Split out from the tick and given no side effects so it can be tested
        without a cursor, a screen or a run loop.
        """
        placed = getattr(self, "_last_layout", None)
        if placed is None:
            return ""
        origin_x, origin_y = getattr(self, "_last_origin", (0.0, 0.0))
        x = screen_x - origin_x
        # The view is flipped; the window's origin is its bottom-left.
        y = origin_y + placed.height - screen_y
        return panel.hit_test(placed, x, y) or ""
```

In `_tick`, before asking for the frame:

```python
        location = NSEvent.mouseLocation()
        hover = self._hover_for(location.x, location.y)
        inside = self._contains(location.x, location.y)
        if inside != getattr(self, "_interactive", False):
            self._interactive = inside
            # Move bar mode owns this flag while it is on; never fight it.
            if not self.movable():
                self._panel.setIgnoresMouseEvents_(not inside)
        frame = self._indicator.next_frame(hover=hover)
```

and after resizing, record what was drawn so the *next* tick can hit-test it:

```python
        self._last_origin = self._origin(frame.width, frame.height)
        self._last_layout = panel.layout(
            frame.width, frame.height, frame.radius,
            flowbar.STATUS_TEXT.get(frame.state, ""), frame.strip,
        )
```

Add `_contains`, and dispatch clicks in `_PillView.mouseDown_`:

```python
    def _contains(self, screen_x: float, screen_y: float) -> bool:
        origin_x, origin_y = getattr(self, "_last_origin", (0.0, 0.0))
        placed = getattr(self, "_last_layout", None)
        if placed is None:
            return False
        return (
            origin_x <= screen_x < origin_x + placed.width
            and origin_y <= screen_y < origin_y + placed.height
        )
```

In `_PillView.mouseDown_`, before the existing drag handling:

```python
        hover = getattr(self, "_hover", "")
        callback = getattr(self, "_on_click", None)
        if hover and callback is not None:
            callback(hover)
            return
```

`_PillView.setData_` gains `self._hover = data.hover`, and `FlowBar.open()`
sets `view._on_click = self._on_click`, stored from a new
`FlowBar(..., on_click=None)` argument.

Add `NSEvent` to the guarded import block and its stub.

- [ ] **Step 4: Write the implementation — Windows**

The same shape, with the Win32 spellings. In `flowbar_win.py`'s `FlowBar`,
add the identical `_hover_for` and `_contains` (they only touch
`panel`, so copy them verbatim — the coordinate flip is the one difference,
and Windows needs none because its screen origin is already top-left):

```python
    def _hover_for(self, screen_x: float, screen_y: float) -> str:
        placed = getattr(self, "_last_layout", None)
        if placed is None:
            return ""
        origin_x, origin_y = getattr(self, "_last_origin", (0.0, 0.0))
        return panel.hit_test(
            placed, screen_x - origin_x, screen_y - origin_y
        ) or ""
```

In `_draw`, poll the cursor and toggle the click-through bit:

```python
        point = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        hover = self._hover_for(float(point.x), float(point.y))
        inside = self._contains(float(point.x), float(point.y))
        if inside != getattr(self, "_interactive", False):
            self._interactive = inside
            if not self.movable():
                style = ctypes.windll.user32.GetWindowLongW(
                    self._hwnd, GWL_EXSTYLE
                )
                style = (style & ~WS_EX_TRANSPARENT) if inside \
                    else (style | WS_EX_TRANSPARENT)
                ctypes.windll.user32.SetWindowLongW(
                    self._hwnd, GWL_EXSTYLE, style
                )
        frame = self._indicator.next_frame(hover=hover)
```

and record `_last_origin` / `_last_layout` exactly as macOS does.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest -q`

Expected: PASS.

- [ ] **Step 6: Look at it**

Run: `python -m vocal_advantage`, start a recording, move the cursor over the
strip.

Expected: a hover pill fills behind `Stop` or `Cancel`; with the cursor away
from the panel a click lands in the window underneath; with it over the panel a
click on `Cancel` discards the recording and nothing steals focus.

Gates 4a, 4b, 4d, 4e. **Gate 4c cannot pass yet** — nothing is wired to the
controller until Task 8. Expect the click to do nothing and say so.

- [ ] **Step 7: Commit**

```bash
git add vocal_advantage/flowbar_mac.py vocal_advantage/flowbar_win.py tests/
git commit -m "Take clicks only while the cursor is over the bar"
```

---

## Task 8: Wire the controls, and amend the interface spec

The strip becomes real: clicking `Stop` ends the recording, clicking `Cancel`
discards it.

**Files:**
- Modify: `vocal_advantage/controller.py`
- Modify: `vocal_advantage/main.py:977`, `vocal_advantage/main.py:1131`
- Modify: `docs/plans/2026-08-25-interface-design.md`
- Test: `tests/test_controller_cancel.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `FlowBar(on_click=...)` from Task 7, which is handed the id
  `panel.hit_test` returned.
- Produces: `Controller.request_stop() -> None` and
  `Controller.request_cancel() -> None`.

**Read this before writing anything.** Three facts about the existing code that
the obvious implementation gets wrong:

1. **The controller has no public stop or cancel.** Its whole public surface is
   `on_key_event`, `set_hotkey` and `tick`. `_cancel` and `_stop_and_process`
   are private and are called from the key path.
2. **It is already touched from two threads** — the hotkey thread via
   `on_key_event`, and whatever drives `tick`. Calling `_stop_and_process`
   straight from the AppKit main thread adds a third and races the other two.
   So `request_*` only *records* the request, and `tick` performs it. That is
   the pump the rest of this file already uses.
3. **`Indicator` is constructed before the `Controller` exists**, at
   `main.py:977` and `main.py:1131`. Callbacks cannot be passed to it at
   construction, which is why the click channel is `FlowBar(on_click=...)` and
   not an `Indicator` argument. Do not try to give `Indicator` an
   `on_stop`/`on_cancel`; the wiring order forbids it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_controller_cancel.py`, reusing whatever helper that file
already has for building a controller with fakes (find it with
`grep -n "^def \|^class " tests/test_controller_cancel.py`):

```python
def test_request_cancel_discards_the_recording():
    """Gate 4c. A clicked Cancel must do exactly what Esc does."""
    controller = a_recording_controller()
    controller.request_cancel()
    controller.tick()
    assert controller.state is State.IDLE
    assert controller.recorder.stopped


def test_request_stop_transcribes_rather_than_discarding():
    controller = a_recording_controller()
    controller.request_stop()
    controller.tick()
    assert controller.state is not State.IDLE


def test_a_request_is_performed_by_tick_not_by_the_caller():
    """The controller is already driven from the hotkey thread and the tick
    thread. A click arrives on a third -- the UI thread -- so the request is
    recorded and the existing pump performs it, rather than three threads
    mutating the state machine directly."""
    controller = a_recording_controller()
    controller.request_cancel()
    assert controller.state is State.RECORDING
    controller.tick()
    assert controller.state is State.IDLE


def test_a_request_while_idle_is_ignored():
    """Clicking Stop on a bar that is not recording must be harmless. The
    strip hides its controls outside RECORDING, but a click can still land in
    the frame between the state changing and the redraw."""
    controller = an_idle_controller()
    controller.request_stop()
    controller.request_cancel()
    controller.tick()
    assert controller.state is State.IDLE


def test_only_the_latest_request_is_kept():
    """Two clicks in one frame are one action, not two."""
    controller = a_recording_controller()
    controller.request_stop()
    controller.request_cancel()
    controller.tick()
    assert controller.state is State.IDLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_controller_cancel.py -k request -v`

Expected: FAIL — `AttributeError: 'Controller' object has no attribute
'request_cancel'`.

- [ ] **Step 3: Write the implementation — the controller**

In `vocal_advantage/controller.py`, add a slot in `__init__`:

```python
        #: A control clicked on the Flow Bar, waiting for the next tick.
        #: Written from the UI thread, read and cleared in `tick`.
        self._requested: str | None = None
```

and the two public methods, next to `on_key_event`:

```python
    def request_stop(self) -> None:
        """Ask for the recording to be stopped and transcribed.

        Recorded rather than performed. This is called from the UI thread when
        the Flow Bar's Stop is clicked, and this object is already driven from
        the hotkey thread and the tick thread -- performing it here would put a
        third thread inside the state machine. `tick` does the work, which is
        the pump every other transition already goes through.
        """
        self._requested = "stop"

    def request_cancel(self) -> None:
        """Ask for the recording to be discarded. See `request_stop`."""
        self._requested = "cancel"
```

At the top of `tick`, before anything else it does:

```python
        requested, self._requested = self._requested, None
        if requested is not None and self.state is State.RECORDING:
            # Ignored outside RECORDING on purpose: the strip hides its
            # controls in every other state, but a click can still land in the
            # frame between the state changing and the redraw, and a stray
            # click must never bin a transcription in flight.
            if requested == "cancel":
                self._cancel()
            else:
                self._stop_and_process()
```

- [ ] **Step 4: Write the implementation — main.py**

Both `Indicator` sites change. **There are two**, and they are not the same.

At `main.py:1131` (macOS), replace the `legend=` argument:

```python
    indicator = Indicator(
        level_source=lambda: recorder.level,
        hotkey=str(spec),
        cancel_key="" if CANCEL_KEY in spec.keys else CANCEL_KEY,
    )
```

The `cancel_key` expression is `legend_for`'s rule, moved: when Esc is itself
the hotkey there is no Cancel to advertise. Once nothing calls `legend_for`,
delete it and its test.

At `main.py:977` (Windows), the current comment says a legend is deliberately
omitted because *"`flowbar_win.render_frame` draws no text — there is no font
in that file"*. Task 6 gave it one. Replace both the comment and the call:

```python
    # The Windows renderer can draw text as of the panel work, so the hotkey
    # goes in here too -- the comment that used to sit here explained why it
    # could not, and that reason is gone.
    indicator = Indicator(
        level_source=lambda: recorder.level,
        hotkey=str(spec),
        cancel_key="" if CANCEL_KEY in spec.keys else CANCEL_KEY,
    )
```

Then, where `_make_flow_bar(cfg, indicator)` builds the bar — which happens
*after* the controller exists — pass the dispatch in:

```python
def _make_flow_bar(cfg: dict, indicator, on_click=None):
    ...
```

and at the call site, after the controller exists:

```python
    def activate(item_id: str) -> None:
        """Perform a Flow Bar control, by the id `panel.hit_test` returned.

        A click and the key it names go through the same request, so they
        cannot drift into doing two different things.
        """
        action = {
            "stop": controller.request_stop,
            "cancel": controller.request_cancel,
        }.get(item_id)
        if action is not None:
            action()

    flow_bar = _make_flow_bar(cfg, indicator, on_click=activate)
```

Note it takes the id and *performs* the action. Task 7's `mouseDown_` calls
`callback(hover)`, so handing it a bare `dict.get` would look right, type-check
fine, and silently do nothing — it would return the function instead of calling
it.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest -q`

Expected: PASS. If `test_main.py` asserts on `legend_for`, delete those cases
along with the function.

- [ ] **Step 6: Look at it — gate 4c**

Run: `python -m vocal_advantage`, record, click `Cancel`.

Expected: the recording is discarded, nothing is pasted, the panel shrinks back
to the pill, and focus never leaves the window you were typing in.

- [ ] **Step 7: Amend the interface spec**

In `docs/plans/2026-08-25-interface-design.md`, replace gate 1e:

```markdown
- [x] 1e. ~~The pill keeps its warm paper ground and black bars.~~
      **Reversed 2026-08-25.** The ground is near-black and the bars white, and
      the pill grows into a two-band panel while recording. See
      [`2026-08-25-flow-bar-panel.md`](2026-08-25-flow-bar-panel.md), which
      records why and what was measured.
```

and add, under gate 1's heading:

```markdown
*Superseded in part by the panel spec: 1b and 1c still hold but are now strip
items rather than legend text. 1a is unchanged and still blocked on gate 6.*
```

- [ ] **Step 8: Tick the panel spec's gates**

Go through the five gate groups in
[`2026-08-25-flow-bar-panel.md`](2026-08-25-flow-bar-panel.md) and tick only
what was actually observed. Leave the Win32 plumbing untested and say so in
one line under **Verification** — that is the honest outcome, not a gap.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Make the strip's controls do what they say"
```

---

## Notes for whoever runs this

**The trap that will cost the most time.** `_mirrored` in `flowbar_win.py`
forces the rendered image to be symmetric about its horizontal centre line. It
exists for a real reason documented in its docstring, and it is silently wrong
for the panel: it will paint a mirror of the waveform band over the control
strip and the result looks like a rendering fault rather than the wiring fault
it is. Task 6 Step 3 bypasses it for the panel and keeps it for the pill.

**The second trap.** `panel.py` returns top-left-origin rects. Forget
`isFlipped` on the macOS view and every strip item draws in the waveform band,
upside down, and the panel looks broken in a way that reads as a layout bug in
`panel.py` — which will be the one file that is actually correct.

**The third trap, and the one that will not show up in a test.** A click
arrives on the UI thread. The controller is already driven from the hotkey
thread and the tick thread, and its transitions are not guarded. Calling
`_cancel()` straight from a mouse handler works every time you try it by hand
and races under load. `request_*` records; `tick` performs. Do not shortcut it.

**There are two `Indicator` construction sites, and they differ.** The Windows
one at `main.py:977` carries a comment explaining that no legend is passed
because that renderer draws no text. Task 6 makes that false. Update the
comment as well as the call — a stale comment asserting the opposite of the
code is worse than no comment.

**On the character-width estimates.** `LABEL_CHAR_WIDTH` and `CAP_CHAR_WIDTH`
are deliberate over-estimates, and `flowbar.LEGEND_CHAR_WIDTH`'s docstring
records what happened last time someone tightened one. If a label looks like it
has too much room, that is the intended failure direction. Leave it.

**What must not be claimed.** The Win32 window plumbing — `WS_EX_TRANSPARENT`
toggling, `GetCursorPos`, the layered window under a real cursor — cannot be
exercised from this Mac. `render_frame` can, and covers the drawing. Report the
plumbing as written-but-unverified.
