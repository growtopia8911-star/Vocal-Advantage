# Flow Bar panel — spec

Written 2026-08-25. Amends gate 1 of [`2026-08-25-interface-design.md`](2026-08-25-interface-design.md),
which reserved the Flow Bar's identity and added only a text legend. That gate
is now partly reversed; see **Amendments** at the end.

**Amended again, same day, after the grow shipped and the user saw it move.**
The panel no longer *grows* open and shut -- it appears at full size and
disappears, and idle now shows nothing at all. The prose below still
describes the grow at length because the reasoning that produced the two
*shapes* is still correct; only the eased transition between them is gone.
See **"The grow removed" amendments** near the end for what changed, why, and
which gates below are reversed rather than silently deleted -- the same way
gate 1e's reversal is recorded, not erased.

**Source of truth for every measurement below:** the native-resolution captures
in `design-research/superwhisper/assets/` (gitignored), not the prose in
[`../superwhisper-teardown.md`](../superwhisper-teardown.md). The teardown calls
the recording window "roughly 3:1"; measured, it is **4.74:1**. Where the two
disagree, the pixels win.

All superwhisper figures are **÷2 from a 2× capture** and therefore in points.

---

## What is being built

The Flow Bar becomes superwhisper's **main recording window**: a two-band
rounded panel with a near-black waveform band above a charcoal control strip.
It is one object with two shapes — a small resting pill that grows into the
panel while you dictate and shrinks back afterwards. *(As shipped and then
amended: the two shapes stayed, the grow between them did not — see "The grow
removed" amendments.)*

Not being built: their mini-window three-icon cluster, their resize toggle,
their context-capture glyph. Reasons in **Rejected**.

---

## Measurements

From `get-started-interface-rec-window-001.png` (the panel) and `-012.png`
(the mini pill).

| | Superwhisper, measured | Vocal Advantage today | Chosen |
| --- | --- | --- | --- |
| Panel | 600 × 126, radius ~12 | — | **420 × 96**, radius 12 |
| Aspect | 4.74 : 1 | — | 4.4 : 1 — see below |
| Waveform band | 80 | — | **57** |
| Hairline | 1 pt `#636464` | — | same |
| Control strip | 46 (band:strip 1.76:1) | — | **38** |
| Outer border | 1 pt, `#535353` top → `#747676` sides | none | same |
| Band fill | gradient `#181818` → `#010101` | flat `#F7F6F1` | same |
| Strip fill | gradient `#2E2F2F` → `#232424` | — | same |
| Bar colour | `#D5D5D5` | black | same |
| Bar / gap | 2.0 / 2.0 — **1:1** | 1.5 / 2.2 — 1.47:1 | **2.0 / 2.0** |
| Peak amplitude | 69% of band height | — | same |
| Strip text | `#AFB0B0`, ~13 pt | `#575757`, 9.5 pt | same |
| Key cap chip | `#1F2020`, radius ~6 | — | same |
| Status dot | `#3279C0` blue | — | see state table |
| Record red | `#FF453A` (Apple system red) | — | used for the recording dot |
| Mini pill | ~58 × 30, fill `#0B0B0B` | 78 × 30, fill `#F7F6F1` | **78 × 30**, fill `#0B0B0B` |

**Two departures from exact, each on purpose.**

*4.4:1 rather than 4.74:1.* Scaling 600 × 126 down to 420 would give a 30 pt
strip, and 13 pt text in a 30 pt strip has no room to breathe. The strip holds a
legible fixed height and the band takes the loss. The panel is squatter than
theirs and reads correctly; a proportionally-correct panel with 9 pt text would
not.

*420 rather than 600.* Theirs occupies 42% of a 1440 pt screen. 420 is 29%.
Nothing is lost but presence, and this app's bar sits over your work.

**Nothing is a flat fill.** Both bands are vertical gradients, and that is most
of why their panel does not look like a rectangle of paint. Reproducing the two
gradients matters more than any single hex value here.

---

## The two shapes

```
IDLE                RECORDING                       TRANSCRIBING
.--------.          .---------------------------.   .---------------------------.
| ..il|i. |   -->   |   ..il|II|l..  ..il|I|l.. |   |     . . il|Ili| . .       |
'--------'          |---------------------------|   |---------------------------|
 78 x 30            | (o) Recording  Stop | Canc |   | (o) Transcribing          |
 radius 15          '---------------------------'   '---------------------------'
 full-round          420 x 96, radius 12              right group absent
```

*As originally written, this grew:* the panel eased **width, height and
corner radius together** (15 → 12), and the strip's alpha rode the width so
it never drew squashed mid-animation. **Removed 2026-08-25 -- see "The grow
removed" amendments.** `open` now snaps straight to its target instead of
easing, so every size is fixed at one of the two shapes above and never in
between. `point_origin` still treats its anchor as `(centre_x, bottom_y)`, so
the bottom edge still does not move when the shape hops from one to the
other — the correct behaviour at the bottom of a screen, for free, whether
that hop is animated or not.

### The trace across both shapes

One ring buffer of **69 heights**. The panel draws all 69; the pill draws a
window onto the newest 15. No bar count is ever interpolated, and the grow
*reveals history* rather than resetting the trace.

69 bars at a 4 pt pitch is 274 pt of content in a 420 pt panel — 65% of the
width, against superwhisper's ~66%. At `SCROLL_FRAMES = 6` and 60 fps that is
**6.9 seconds** of visible history, up from today's 1.5.

`bar_layout` already accepts `bar_width` and `gap` as arguments, so this needs
no signature change. At 2.0 / 2.0 the pill's 15 bars are 58 pt of content inside
78 pt, leaving 10 pt margins where there are 12 today — still clear of the round
caps that `BAR_MARGIN_Y` exists to protect.

Band half-height is 28.5; at 69% peak amplitude the tallest bar is 19.7,
so the band's vertical margin is **8.8**, against `BAR_MARGIN_Y = 3.5` in the
pill. Two constants, one per shape.

---

## The control strip

```
  (o) Recording                        Stop [F8]  |  Cancel [Esc]
   ^        ^                            ^    ^   ^
   |        |                            |    |   '- 1pt divider, #636464
   |        |                            |    '- key cap: #1F2020 chip, radius 6,
   |        |                            |       DARKER than the strip it sits on
   |        |                            '- label, #AFB0B0
   |        '- state word, #AFB0B0 13pt
   '- 8pt dot
```

Every action is **label + its keyboard shortcut, side by side, at nearly equal
visual weight**. That is the single transferable pattern from their strip and it
is the whole reason the strip exists.

The left group holds a dot and a state word only. Gate 6 of the interface spec
later inserts a profile name and its switch shortcut beside the dot without
moving anything else.

### Why the right group vanishes while transcribing

`LEGEND_STATES = frozenset({RECORDING})` in `flowbar.py` already carries this
decision, and its reasoning survives the redesign verbatim:

> Not TRANSCRIBING either, which is the one that looks wrong and is not — once
> the model has the audio, no key stops it and none bins the result, so anything
> the legend said there would be false.

Drawing a `Stop` that stops nothing is worse than an empty strip.

---

## States

| State | Shape | Dot | Trace | Right group | On screen |
| --- | --- | --- | --- | --- | --- |
| `IDLE` | pill 78 × 30, alpha 0.82 → **0.0, since the grow removal** | none | `idle_heights` | — | no *(exception: "Move bar" — see the amendments)* |
| `RECORDING` | panel 420 × 96 | `#FF453A` | live, from audio | `Stop` + `Cancel` | yes |
| `TRANSCRIBING` | panel 420 × 96 | `#3279C0` | `transcribing_heights` sweep | — | yes |
| `MESSAGE` | pill, widened to fit the text | none — a pill has no strip | replaced by the text | — | yes |

`MESSAGE` stays pill-shaped. A panel is for dictating; "could not paste" should
not open one. This preserves today's `message_width` behaviour unchanged.

The existing `PILL_ALPHA` / `BAR_ALPHA` / `TEXT_ALPHA` tables and `FADE_ALPHA`
easing are unchanged in shape — only the ground and bar colours invert. The one
value that later changed is `PILL_ALPHA[IDLE]`, from 0.82 to 0.0; see the
amendments.

---

## Interaction

Superwhisper's strip is clickable: hover fills a rounded pill behind an item,
and at rest nothing has a border. Reproducing that collides with the three
guards documented at the top of `flowbar_mac.py`, which exist because a panel
that takes focus sends the paste into our own process.

**Resolution: click-through by default, interactive only under the cursor.**

Poll the cursor on the 60 fps timer that already runs. On entering the panel,
drop click-through and draw hover pills; on leaving, restore it. The
non-activating panel (macOS) and `WS_EX_NOACTIVATE` (Windows) already guarantee
that a click never activates this process, so focus is never stolen — the only
cost is that while the pointer is over the panel, clicks land on it instead of
what is underneath. That is exactly superwhisper's behaviour.

| | macOS | Windows |
| --- | --- | --- |
| Read cursor | `NSEvent.mouseLocation` | `GetCursorPos` |
| Drop click-through | `setIgnoresMouseEvents_(False)` | clear `WS_EX_TRANSPARENT` |
| Restore | `setIgnoresMouseEvents_(True)` | set `WS_EX_TRANSPARENT` |

`Move bar` mode already toggles exactly these on both platforms, so this is a
second caller of machinery that exists, not new machinery.

**Clicks need a return channel that does not exist.** `Indicator` is a one-way
queue today. It gains `on_stop` and `on_cancel` callables, invoked on the UI
thread, which poke the controller the same way the hotkey thread already does.

---

## Code shape

### New: `vocal_advantage/panel.py`

Pure arithmetic. Given a width, height and the strip's items it returns rects
for the band, the strip, the hairline, the dot, the label, each key cap and the
divider — plus `hit_test(x, y) -> item_id | None`.

Both renderers consume it, so the two platforms **cannot** drift, and the layout
*and the hover logic* are unit-testable on any machine — exactly the property
`pill_origin` was given for the same reason.

### Changed: `vocal_advantage/flowbar.py`

- `Frame` gains `hover: str = ""` (the hovered item id, `""` for none) and
  `strip: tuple[StripItem, ...] = ()`, where a `StripItem` is
  `(id, label, key_cap)` — `id` is what `hit_test` returns and what `hover`
  holds. `legend: str` goes.
- `legend_width` / `LEGEND_CHAR_WIDTH` / `LEGEND_GAP` go — a fixed-size panel
  does not measure text to size itself.
- `LEGEND_STATES` survives under a clearer name as the set of states that show
  the right group. The frozenset and its comment are kept.
- The width ease is **kept and becomes load-bearing**: it drives the pill↔panel
  grow. Height and radius ease alongside it.

### Changed: `flowbar_mac.py`, `flowbar_win.py`

Both draw from `panel.py`'s rects. Neither computes a layout.

Deliberately **not** WKWebView, despite `pyproject.toml` already carrying
`pyobjc-framework-WebKit` for the settings window: the bar must be
click-through, non-activating and redraw at 60 fps, and there is no equivalent
already built on Windows. The two native renderers stay.

---

## Gates

Ticked 2026-08-25 (Task 8), against what was actually run — not against what
merely has a test file. Method for each group is under **Verification** below.

### 1. The panel

- [x] 1a. Two bands, near-black above charcoal, separated by a 1 pt hairline.
- [x] 1b. Both band fills are vertical gradients, not flat.
- [x] 1c. A 1 pt outer border, lighter than either band.
- [x] 1d. 420 × 96 with a 12 pt radius; band 57, strip 38.
- [x] 1e. Bars are `#D5D5D5`, 2.0 wide with 2.0 gaps, peaking at 69% of band
      height.

### 2. The strip

- [x] 2a. Left group is a state dot and a state word.
- [x] 2b. Right group is `Stop` and `Cancel`, each a label beside its own key
      cap, separated by a 1 pt divider.
- [x] 2c. Key cap chips are darker than the strip they sit on.
- [x] 2d. The right group is absent in every state but `RECORDING`.
- [x] 2e. The `Stop` cap shows the configured hotkey, and follows a change to
      it. **Fixed** in `ae08a3a` ("Keep the Flow Bar's Stop cap in step with a
      runtime hotkey change"): `Indicator.set_keys(hotkey, cancel_key)` is a
      plain thread-safe attribute assignment, matching how `_status`/
      `_state_name` already work, applying the identical
      `"" if CANCEL_KEY in spec.keys else CANCEL_KEY` rule the construction
      sites use. `HotkeyChanger._change()` calls it right beside
      `controller.set_hotkey(spec)`. Covered by
      `test_set_keys_updates_the_stop_cap_on_the_next_frame`,
      `test_changing_hotkey_to_esc_removes_the_cancel_control`, and
      `test_changing_hotkey_away_from_esc_restores_the_cancel_control`
      (`tests/test_flowbar_strip.py`), plus
      `test_a_hotkey_change_reaches_the_indicator` and
      `test_a_hotkey_change_to_esc_drops_the_cancel_control`
      (`tests/test_main.py`), all passing.

### 3. The two shapes

- [x] 3a. Idle rests as a 78 × 30 full-round pill.
      **Reversed 2026-08-25** — see "The grow removed" amendments below. Idle
      now shows *nothing*: the pill only rests, visibly, while "Move bar" is
      on. The shape itself (78 × 30, full-round) is unchanged and still what
      draws in that one exception, and still what a `flash()` message widens
      from — only "idle always rests as a visible pill" no longer holds.
- [x] 3b. Recording grows it to the panel; width, height and radius all ease.
      **Reversed 2026-08-25.** There is no more grow: recording opens the
      panel at full size on its first frame, and every size — width, height,
      radius, bar count — is fixed at one of the two shapes, never eased
      between them. `open` is set directly to its target in `next_frame`
      rather than eased. Pinned by
      `test_open_snaps_to_the_panel_on_the_very_first_frame`
      (`tests/test_flowbar_strip.py`, formerly `test_the_grow_is_a_fade_not_
      a_cut`, which asserted the opposite) and
      `test_no_frame_is_ever_an_intermediate_size_while_recording`
      (`tests/test_flowbar.py`).
- [x] 3c. The bottom edge does not move during the grow. **Fixed** in
      `78da93b` ("Pin the Windows Flow Bar's bottom edge as it grows into the
      panel"): `flowbar_win.pill_origin` now takes a live `height` parameter
      and uses it in place of the hardcoded `wf.PILL_HEIGHT`, and
      `FlowBar._origin` passes the live height through on the preset
      (un-dragged) path the same way it already did for a dragged point.
      **What is verified, precisely:** the *origin arithmetic* — `y + height`
      (the bottom edge) is identical between the 30pt pill and the 96pt panel
      for all three preset positions and for a dragged point, and the top
      edge is shown to fall (not rise) as height grows — pinned by
      `test_bottom_edge_is_identical_at_pill_height_and_panel_height`,
      `test_the_top_edge_rises_as_the_panel_grows`, and
      `test_bottom_edge_is_identical_for_a_dragged_point_too`
      (`tests/test_flowbar_win.py`), all passing on this Mac (pure Python, no
      Win32 needed). **What is still unverified:** the actual Win32 window —
      `CreateWindowExW`, `SetWindowPos` — staying visually put on a real
      screen. That is Win32 plumbing per the note below, and this tick does
      not claim it.
      **Still holds after the grow removal, unchanged**, and now for a
      simpler reason: the same origin arithmetic keeps the bottom edge fixed
      whether the shape *eases* from pill to panel or *hops* — the panel
      appearing already sized at 420 × 96 still has to anchor from the same
      bottom edge the 78 × 30 pill did.
- [x] 3d. The strip never draws squashed: its alpha rides the width.
      **Moot after the grow removal, not wrong** — `panel.strip_alpha` is
      unchanged and still gates the strip's own drawing (see the
      implementation notes in "The grow removed" amendments for why it was
      kept), but since `open` no longer passes through any value between 0
      and 1, the strip is now always either fully hidden or fully grown and
      never mid-ramp. Nothing left to draw squashed.
- [x] 3e. The trace is one 69-slot buffer; the pill windows its newest 15, and
      the grow reveals history rather than clearing it.
      **The "reveals" part is reversed 2026-08-25** — there is no more grow
      to reveal it gradually across; the panel's full 69-bar window is there
      on the first frame, same as every other size. The buffer/window
      relationship itself is unchanged: it is still one running history, not
      a separate freshly-cleared one, which is the part of this gate that
      still holds — see `test_opening_the_panel_reveals_history_rather_than_
      clearing_it` (`tests/test_flowbar_strip.py`, formerly `test_the_grow_
      reveals_history_rather_than_clearing_it`), rewritten to assert the bar
      count is `[BUFFER_BARS] * 60`, not a climbing sequence.
- [x] 3f. A `flash()` message widens the pill and does not open the panel.
      Unaffected by the grow removal: a message was never a panel state, so
      it was never eased into or out of one, and its own width easing is
      untouched — see "The grow removed" amendments for why that had to be
      kept eased while the panel's own size stopped being.
- [x] 3g. **New 2026-08-25.** Nothing is on screen at idle, except while
      "Move bar" is on. See "The grow removed" amendments.

### 4. Interaction

- [ ] 4a. With the cursor away from the panel, a click passes through to the
      window underneath.
- [ ] 4b. With the cursor over the panel, `Stop` and `Cancel` fill a hover pill.
- [x] 4c. Clicking `Stop` ends the recording; clicking `Cancel` discards it.
      Demonstrated, not merely unit-tested: the real, unmodified
      `_PillView.mouseDown_` was called with a hovered item id and a real
      `on_click`, wired to a real `DictationController` (fake recorder/
      transcriber/paster) exactly as `main.py`'s `activate()` wires it. Cancel
      left the controller `IDLE`, the recorder stopped, nothing transcribed or
      pasted; Stop left it `IDLE` with a transcription and a paste. See
      **Verification**.
      **Re-ticked after the final review's issue 2.** The original
      demonstration pumped `controller.tick()` by hand, which hid a real gap:
      `request_stop`/`request_cancel` only set a flag, and nothing woke
      `controller_loop` out of `events.get(timeout=...)` to notice it, so a
      clicked `Stop` could sit unacted-on for up to `TICK_INTERVAL_S` (1.0s in
      production) — long enough for a mis-aimed second click to land on
      `Cancel` and throw the dictation away, exactly what
      `test_only_the_latest_request_is_kept` guards against for the flag
      itself. `_make_activate` now also takes `events` and enqueues a
      `WAKE_SENTINEL` after calling `request_stop`/`request_cancel`, and
      `controller_loop` acts on that sentinel by calling `controller.tick()`
      immediately, the same way it already reacts to a real key event —
      **no on-screen click was performed; this is still a wiring-level
      claim.** What was tested, in `tests/test_main.py`:
      `test_activate_enqueues_a_wake_sentinel_not_merely_a_flag` (the click
      puts `WAKE_SENTINEL` on the same queue `controller_loop` drains, not
      only a flag), and `test_a_click_wakes_the_loop_before_it_would_otherwise_tick`
      (the real `controller_loop`, given a 10s tick interval, still calls
      `tick()` before it would otherwise have woken — proof the queue item is
      what unblocks it, not the passage of time).
- [x] 4d. Neither click activates this process or moves focus. Static at
      window-creation time on both platforms, not something the click path
      can affect either way: macOS builds the panel
      `NSWindowStyleMaskNonactivatingPanel` and shows it with
      `orderFrontRegardless()`, never `makeKeyAndOrderFront_`; Windows creates
      it with `WS_EX_NOACTIVATE` and shows it with `SW_SHOWNOACTIVATE`, never
      `SW_SHOW`. Neither flag is touched again by any other code path. The
      4c demonstration above also shows `mouseDown_`'s hover-hit branch never
      calls anything window- or focus-related.
- [ ] 4e. Leaving the panel restores click-through.
- [x] 4f. `hit_test` agrees with what is drawn, on both platforms, in a test.
      Both `_hover_for` implementations and both `drawRect_`/`render_frame`
      implementations consume the same `panel.layout(...)` call — confirmed
      for Windows by `test_the_renderer_computes_no_layout_of_its_own`, and
      for macOS by reading `drawRect_` directly (it takes every rect from one
      `placed = panel.layout(...)` and never computes one). `hit_test` itself
      is called on that same `placed` layout by both `_hover_for`s. Neither
      platform can drift from what it draws.

**4a, 4b and 4e are left unticked.** All three are the live cursor loop —
`NSEvent.mouseLocation`/`_tick` on macOS, `GetCursorPos`/`WS_EX_TRANSPARENT`
toggling on Windows — the exact plumbing this file's own Verification section
below says was never run on Windows and, per `test_flowbar_mac.py`'s own
docstring, is "hand-checked" rather than tested on macOS too. The pure
functions underneath (`_hover_for`, `_contains`) are unit-tested on both
platforms, and the drawn hover pill was directly observed for macOS (see
Verification) — but ticking gates whose literal wording is "with the cursor
over/away from the panel" on the strength of that alone would claim more than
was run. Left unticked rather than claimed.

### 5. Parity

- [x] 5a. Both renderers take every rect from `panel.py`; neither computes one.
      **This was not literally true when first ticked, and the final review
      caught both exceptions.** `flowbar_mac._draw_move_outline` computed its
      own radius — `(height - MOVE_OUTLINE_WIDTH) / 2.0`, full-round, correct
      only when the bar was always the 30pt pill — instead of taking
      `data.radius` from the same `panel.layout(...)` call every other rect in
      the file comes from; at the 96pt panel that drew a 47pt-radius outline
      against a 12pt-radius panel, slicing across the waveform band. And both
      `flowbar_mac._draw_bars` and `flowbar_win.render_frame` held the
      peak-bar amplitude as a bare `0.345`, comment and all, duplicated
      verbatim in both files — a value that determines a drawn rect, kept
      outside `panel.py` regardless. Both are fixed now: the move outline
      takes `data.radius` (inset by half the stroke width so its curve stays
      concentric with the panel's own clip), and `panel.PEAK_FRACTION = 0.69`
      is the one place the amplitude lives, with both renderers reading it as
      `band.h * panel.PEAK_FRACTION / 2.0`. `test_move_outline_uses_the_panels_own_radius`
      and `test_the_bar_amplitude_comes_from_panels_shared_constant` (both
      renderers' test files) pin it. With both closed, the gate's literal
      wording holds.
- [x] 5b. `render_frame` produces the panel at 420 × 96 on this Mac.
- [x] 5c. Nothing in `panel.py` imports AppKit, Win32 or Pillow.

---

## Verification

Per the vault note `Spec + Test Driven`, visual work gets a fixture to eyeball;
logic gets a test that is watched to fail first.

| What | How | Where it runs |
| --- | --- | --- |
| Layout arithmetic | unit tests on `panel.py` | anywhere |
| Hit-testing / hover | unit tests on `hit_test` | anywhere |
| Windows **drawing** | `render_frame` → PNG, asserted and eyeballed | **this Mac** |
| macOS drawing | existing `test_flowbar_mac.py` patterns | this Mac |
| State/lifecycle | `Indicator` frame sequences | anywhere |

`tests/test_flowbar_win.py:10` already records that `render_frame` is *"pure
Pillow, no Win32 at all"* and exercises it here. So the Windows **drawing** is
verifiable on this machine by writing PNGs.

**What cannot be verified from this Mac, and will not be claimed:** the Win32
plumbing — `WS_EX_TRANSPARENT` toggling, `GetCursorPos` polling, and the layered
window's behaviour under a real cursor. It gets written and unit-tested where
possible, and reported as unverified.

**Task 8 addendum, on how gate 1/2/3's drawn-appearance ticks above were
actually checked.** The macOS panel was rendered offscreen with a real
`_PillView` — `bitmapImageRepForCachingDisplayInRect_` /
`cacheDisplayInRect_toBitmapImageRep_` against a real `flowbar.Indicator` —
and the PNGs (idle pill, recording panel, recording panel with `Cancel`
hovered, transcribing) were looked at directly: two bands, the hairline, the
outer border, white bars, the dot, state word, both key caps, the divider, and
the hover fill were all visible exactly as specced. The Windows renderer was
checked the way this table already prescribes: `render_frame` on a
`RECORDING` frame, saved to PNG, and looked at — same layout, same colours,
Pillow rather than AppKit. Gate 4c was checked as described in the gate
itself, by calling the real `mouseDown_`. **The Win32 window plumbing itself —
the layered window under a real cursor, `WS_EX_TRANSPARENT` toggling driven by
`GetCursorPos` — has still never been run on Windows, on any task, and gates
4a/4b/4e stay unticked for exactly that reason.**

`tests/test_flowbar_legend.py` is superseded and its cases move to the strip.

---

## Rejected

- **Their mini window's three-icon cluster** (sparkle / record / expand). Every
  one of its three actions is a feature this app does not have: no modes to
  change, no click-to-record, no second window to expand into.
- **Their resize toggle and context-capture glyph.** Nothing to toggle to, and
  no context capture. Drawing them dark would be a picture of a different
  product — the same rule the interface spec applied to the dropped 40%.
- **A profile name in the strip.** Gate 6 does not exist. Gate 1a of the
  interface spec refuses to invent a name, and that still holds.
- **600 pt wide.** Faithful, and too much furniture over your work.
- **Hiding the bar at idle.** Faithful to their window lifecycle, but it deletes
  the resting thing you glance at. The mini pill keeps it.
  **Reversed 2026-08-25, later the same day, once the grow shipped and the
  user saw it move.** The reasoning above was about the *grow* existing to
  glance at, and the user's objection turned out to be to the grow itself,
  not to the pill's continued presence for its own sake: "I actually don't
  want my UI to show me it enhancing in size... it's just unnecessary." With
  the grow gone there is nothing left this rejection was protecting, so the
  bar now does hide at idle — see "The grow removed" amendments below for
  what changed and the one exception ("Move bar" mode).
- **Always-clickable.** Simplest code, but the panel would permanently eat
  clicks at the bottom of the screen, including ones meant for the Dock.
- **WKWebView.** Available on macOS, absent on Windows, and wrong for a 60 fps
  click-through overlay.

---

## Amendments to `2026-08-25-interface-design.md`

- **Gate 1e is reversed.** "The pill keeps its warm paper ground and black bars.
  The identity does not change" — it does now: near-black ground, white bars.
- **Gates 1b and 1c stand** but are restated as strip items rather than legend
  text.
- **Gate 1a is unchanged and still blocked** on gate 6. The dot and state word
  hold the slot.

---

## The grow removed (2026-08-25, later the same day)

Everything above describes the panel this spec originally shipped: a small
resting pill that *eased* open into the 420 × 96 panel and eased shut again.
That shipped, the user watched it happen, and did not like it: "I actually
don't want my UI to show me it enhancing in size. I'm not a big fan of how it
looks and honestly it's just unnecessary." Given the choice between several
options, they picked **nothing on screen at idle; the panel appears when
recording starts and vanishes when it ends.**

This section records what changed, kept in a separate section — rather than
rewritten in place throughout — for the same reason the interface-design
amendments above are: so a reader who wonders "why does `open` snap instead
of easing" can find the reasoning, instead of finding a spec that quietly
never mentioned the grow existed. The gates above are individually annotated
with **Reversed** or **Moot** where this affects them; this section is the one
place the whole change is explained together.

**What changed:**

- `open` — the scalar `width`, `height`, `radius` and bar count all derive
  from — is set directly to its target in `Indicator.next_frame`
  (`vocal_advantage/flowbar.py`) instead of eased. A state change now hops the
  shape in one frame: the panel is 420 × 96 on the very first frame of
  `RECORDING`/`TRANSCRIBING`, never smaller first.
- `PILL_ALPHA[IDLE]` goes from 0.82 to 0.0. The resting pill used to be
  visible on purpose ("it sits over your work all day"); now there is nothing
  to purposely keep visible, because there is no resting pill to see.
- The alphas still ease (`FADE_ALPHA`, unchanged) — the user objected to
  *size* animating, not to a fade — so the panel still fades in and out at
  its full, final size rather than cutting instantly. Only the size itself
  stopped animating.
- `MESSAGE`'s pill still widens to fit its text exactly as before, still
  eased. It was never a panel state and is unaffected by any of this — except
  for one seam: on the single frame a *panel* state (`RECORDING`/
  `TRANSCRIBING`) hands off directly to `MESSAGE` (the real path when a
  transcription finishes and the paste fails — `controller.py` calls `flash()`
  straight from `TRANSCRIBING`, never through `IDLE`), the pill's width snaps
  to its message target instead of easing from the panel's 420. Easing there
  too would have eased the width down from 420 while the height had already
  snapped to the pill's 30 — a squashed, panel-wide pill for a few frames,
  which is a shrink in every way but name, and exactly what this change
  exists to remove. `flowbar.py`'s `next_frame` tracks whether the previous
  frame was a panel (`was_panel`) to tell the two cases apart.
- `Frame` gains `visible: bool`. True for `RECORDING`, `TRANSCRIBING` and
  `MESSAGE`; true for `IDLE` while "Move bar" is on; otherwise false for
  `IDLE`, and — this is the part worth being precise about — *not* the
  instant the state changes to `IDLE`. It stays true until `pill_alpha` has
  actually eased down to (within `HIDE_ALPHA_EPS` of) zero, so the fade is
  something a renderer gets to draw before the window disappears, rather than
  being cut off by an instant hide. One rule, computed once in `flowbar.py`,
  rather than each renderer re-deriving "nothing to see" from alpha itself.
- Both renderers gate showing/hiding the actual window on `Frame.visible`:
  `orderFrontRegardless()` / `orderOut_(None)` on macOS (the panel already
  used the first for the reason documented at the top of `flowbar_mac.py` —
  never `makeKeyAndOrderFront_`, which would steal focus and send the user's
  next paste into this process instead of theirs), and `ShowWindow(hwnd,
  SW_SHOWNOACTIVATE | SW_HIDE)` on Windows (never bare `SW_SHOW`, for the same
  focus reason). Neither renderer shows the window unconditionally at
  creation time any more — that decision now waits for the first computed
  frame, exactly like every other frame-driven property.
- Both renderers' cursor/click-through polling was found to have a latent bug
  once a window could be hidden: `_contains`/`_hover_for` read `_last_layout`/
  `_last_origin`, which are the *last drawn* geometry and go stale the moment
  the window is hidden — an idle, hidden window sitting where a 420-wide
  panel last was would still report the cursor "inside" it if the cursor
  happened to be there, and would drop click-through on a window that is not
  on screen to click. Fixed by ANDing the containment check with "is the
  window currently shown" on both platforms.
- Windows had a second latent bug: `_reposition` (called whenever the frame's
  width changes) passed `SWP_SHOWWINDOW` to `SetWindowPos`, which would have
  forced a hidden, idle window back on screen the moment its width next
  changed by half a pixel — fighting the `ShowWindow` call meant to be the
  sole authority on visibility. That flag is dropped from `_reposition`'s
  call.
- Nothing in `panel.py` changed. `bars_for_open`, `lerp`, `strip_alpha` and
  the pill/panel geometry all still exist and are still called exactly as
  before — `MESSAGE` still needs the pill geometry, and `strip_alpha` still
  correctly gates the strip (see gate 3d's note: it is now moot in practice,
  since `open` never passes through a partial value any more, but it is not
  wrong, and removing it would have been removing a still-correct guard for
  no reason).

**The "Move bar" exception, and what shape it takes.** With nothing on
screen at idle, "Move bar" mode — the tray toggle that makes the bar
draggable, saving its position to `flow_bar_point` in `config.json` — would
have nothing to grab. So `Frame.visible` is also true for `IDLE` while
movable, on both platforms: `Indicator.set_movable(bool)` is a new,
thread-safe setter (same shape as the existing `set_keys`), called by each
renderer's own `set_movable` whenever the tray toggles it. **What that
visible idle pill looks like while movable is the resting pill exactly as it
was before this change** — `MOVABLE_IDLE_PILL_ALPHA = 0.82`, the same value
`PILL_ALPHA[IDLE]` used to be, substituted in for `IDLE`'s target alpha only
while movable is on. The alternative considered was some new "you can drag me"
affordance (a distinct colour, a label), but the pill is what this bar has
always looked like at rest, and "Move bar" is meant to answer "where did the
bar go, and how do I get it back" — the most useful answer is the exact thing
the user is trying to relocate, not a new shape they have to learn to
recognise. macOS also draws its existing blue `_draw_move_outline` in this
state, independent of `pill_alpha`, as it already did before this change;
Windows has no equivalent (`render_frame` never grew one — a pre-existing
platform asymmetry, not introduced here, and out of scope to fix in this
pass), so the resting pill's own visibility is what Windows relies on for a
grabbable target, which is exactly why `MOVABLE_IDLE_PILL_ALPHA` reusing a
fully legible alpha (not some dim compromise) mattered more there than it
might have on macOS alone.

**Tests.** `tests/test_flowbar.py` gained a "visibility" section: idle is not
visible, recording is, the panel is full-size on the first frame and never at
an intermediate width or height for the rest of the recording, transcribing
stays full-size, a flash message is visible and pill-shaped and opens no
panel, leaving the panel snaps geometry immediately but still fades before
hiding, a message expiring returns to not-visible, movable idle is visible
*and actually draws something* (not just the flag — `pill_alpha` settles
above 0.5), and turning movable back off lets idle fade out again.
`tests/test_flowbar_strip.py`'s grow-specific tests were rewritten in place —
see the gate 3b/3e annotations above for exactly what each one used to assert
and asserts now. Both renderer test files gained source-inspection tests
(the established pattern in both files, e.g. `test_click_through_is_the_
default`) pinning: which platform call shows/hides the window, that it is
gated on `frame.visible`, that neither window creation nor a resize
force-shows it, and that the click-through guard cannot fire on a hidden
window. All of the above were confirmed to fail against the pre-change
implementation before the implementation changed, then pass after.

---

## Order

1. `panel.py` — layout and hit-testing, with its tests. Nothing draws yet.
2. `flowbar.py` — `Frame`, hover, the pill↔panel ease.
3. `flowbar_mac.py` — draw the panel. First point it is on screen.
4. `flowbar_win.py` — `render_frame` for the panel, verified by PNG here.
5. Hover and click-through toggling, both platforms.
6. `on_stop` / `on_cancel` wired to the controller.
7. Amend the interface spec's gate 1.

Steps 1–2 are the ones worth getting right; 3 and 4 are transcription once the
rects exist.
