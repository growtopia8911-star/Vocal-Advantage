"""The Flow Bar's state machine: commands and audio in, one `Frame` out.

Portable. No window, no platform code, no drawing -- `flowbar_mac.py` and
`flowbar_win.py` each take a `Frame` and put it on screen, and neither knows
anything else about what is going on.

Two halves, split along the thread boundary:

* **Any thread** may call `show_recording` / `show_processing` / `hide` /
  `flash`. Those are the four methods `controller.py` already calls, so the
  controller does not change at all. Each one only enqueues.
* **The render thread only** calls `next_frame()`, 60 times a second: it drains
  the queue, polls the audio level, advances the easing and returns a `Frame`.

`hide()` means "go idle". As of 2026-08-25 idle genuinely disappears -- the
bar orders itself off screen rather than resting as a dim pill, at the user's
request (see docs/plans/2026-08-25-flow-bar-panel.md's amendments). The one
exception is "Move bar" mode, which needs something on screen to grab.
`Frame.visible` is the single rule both renderers show/hide against, computed
once here rather than re-derived from alpha in two places.

Time is counted in frames rather than read off a clock, exactly as the old pill
counted pumps. `next_frame()` is called on a fixed interval, so frames are an
accurate stopwatch, and the whole machine stays deterministic with no clock to
inject.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass

from vocal_advantage import panel
from vocal_advantage import waveform as wf

IDLE = "idle"
RECORDING = "recording"
TRANSCRIBING = "transcribing"
MESSAGE = "message"

#: What the tray's non-clickable status line says for each state.
STATUS_TEXT = {
    IDLE: "Idle",
    RECORDING: "Recording",
    TRANSCRIBING: "Transcribing",
    MESSAGE: "Idle",
}

#: 1.5s at 60fps. Long enough to read "could not paste - press Ctrl+V" without
#: sitting in the way.
MESSAGE_FRAMES = 90

#: Rough advance width of the message font, in pixels per character. It only
#: has to be close: the pill is centred, so a few pixels of extra padding is
#: invisible, whereas text clipped at the pill's edge is not.
MESSAGE_CHAR_WIDTH = 5.5
MESSAGE_PADDING = 22.0

#: The states that open the panel. Everything else rests as the pill.
#:
#: A message is deliberately absent: it widens the pill to fit its text, as it
#: always has, but it does not open the panel. A panel is for dictating, and
#: "could not paste" should not need one.
PANEL_STATES = frozenset({RECORDING, TRANSCRIBING})

#: The states that keep the bar on screen even though nothing else says so.
#: IDLE is the only state left out -- and even IDLE is visible while "Move
#: bar" is on, which `next_frame` handles separately since it is a toggle, not
#: a state. See `Frame.visible`.
VISIBLE_STATES = frozenset({RECORDING, TRANSCRIBING, MESSAGE})

#: The states that show the strip's right-hand controls.
#:
#: RECORDING only, and the exclusions are each a decision. Not IDLE: the
#: resting pill is what sits over your work all day, and a standing reminder of
#: a key you are not currently holding is clutter. Not MESSAGE: "could not
#: paste" is urgent and a reminder is not. Not TRANSCRIBING either, which is
#: the one that looks wrong and is not -- once the model has the audio, no key
#: stops it and none bins the result, so anything shown there would be false.
CONTROL_STATES = frozenset({RECORDING})

# --- how bright the pill and its bars are in each state ---------------------
# The pill brightens slightly while recording. IDLE used to rest at a dim 0.82
# -- visible on purpose, since it sat over your work all day. Removed
# 2026-08-25 at the user's request ("I don't want my UI to show me it
# enhancing in size... it's just unnecessary"): idle is now 0.0, nothing on
# screen at rest. See docs/plans/2026-08-25-flow-bar-panel.md's amendments.
#: Opacity of the whole pill -- ground and outline together, so a state change
#: firms up the entire object rather than tinting part of it.
PILL_ALPHA = {
    IDLE: 0.0,
    RECORDING: 0.96,
    TRANSCRIBING: 0.90,
    MESSAGE: 0.96,
}
#: What the pill's own alpha eases toward at rest while "Move bar" is on.
#: `PILL_ALPHA[IDLE]` is 0.0 -- nothing on screen at idle -- but the one time
#: that is wrong is while the bar has to stay visible to be dragged. Reuses
#: the old resting pill's alpha rather than inventing a new look: the pill is
#: what this bar has always been at rest, so it is the shape most likely to
#: already read as "grab here."
MOVABLE_IDLE_PILL_ALPHA = 0.82
BAR_ALPHA = {
    IDLE: 0.55,
    RECORDING: 1.0,
    TRANSCRIBING: 0.85,
    MESSAGE: 0.0,      # the message replaces the bars rather than crowding them
}
#: Eased like the alphas above rather than derived from `bar_alpha`, so the
#: message crossfades in as the bars fade out instead of popping.
TEXT_ALPHA = {
    IDLE: 0.0,
    RECORDING: 0.0,
    TRANSCRIBING: 0.0,
    MESSAGE: 1.0,
}

#: The alphas always ease, and so does the pill's own width when the shape is
#: staying a pill (MESSAGE widening to fit its text). `open` -- and the panel
#: size it drives -- does not: see `next_frame`.
FADE_ALPHA = 0.18

#: How close `pill_alpha` must get to its IDLE target before the window is
#: actually taken off screen. `_ease` only approaches a target
#: asymptotically, so waiting for exactly 0.0 would never fire; this is
#: "close enough that the fade has visually finished."
HIDE_ALPHA_EPS = 0.01


def message_width(text: str) -> float:
    """How wide the pill has to be to show `text`. Never narrower than default."""
    return max(
        float(wf.PILL_WIDTH), MESSAGE_PADDING + len(text) * MESSAGE_CHAR_WIDTH
    )


def _ease(current: float, target: float, alpha: float) -> float:
    return current + (target - current) * alpha


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
    #: 0 = the resting pill, 1 = the open panel. Height, radius and bar count
    #: are all read off it, so no two of them can fall out of step. Snapped
    #: straight to its target in `next_frame`, never eased -- there is no
    #: grow left to derive, only a hop between two fixed shapes.
    open: float = 0.0
    height: float = float(wf.PILL_HEIGHT)
    radius: float = panel.PILL_RADIUS
    #: The strip's right-hand controls. Empty in every state but RECORDING.
    strip: tuple[panel.StripItem, ...] = ()
    #: The id of the item under the cursor, or "". Supplied by the platform
    #: layer, which is the only thing that knows where the cursor is.
    hover: str = ""
    #: Whether the bar should be on screen at all right now. True for every
    #: state but IDLE, true for IDLE too while "Move bar" is on, and -- for a
    #: plain IDLE -- true until the alpha fade has actually reached zero, so
    #: the window is never yanked off screen mid-fade. One rule, computed
    #: once in `next_frame`, so a renderer never has to re-derive "nothing to
    #: see" from alpha itself.
    visible: bool = True


class Indicator:
    """Thread-safe front end to the Flow Bar.

    Satisfies `controller.IndicatorLike`, so it drops in where the old
    Windows-only pill was without the controller noticing.
    """

    def __init__(
        self,
        level_source=None,
        n_bars: int = wf.BUFFER_BARS,
        hotkey: str = "",
        cancel_key: str = "",
    ) -> None:
        #: A zero-argument callable returning the current mic RMS, or None.
        #: `Recorder.level` is what production passes. Kept as a callable so
        #: this module never imports the recorder, and so the tests can drive
        #: the whole state machine with a lambda.
        self._level_source = level_source
        self._n_bars = n_bars
        #: Named by the caller, because the hotkey lives in main.py and this
        #: module deliberately knows nothing about hotkeys.
        self._hotkey = hotkey
        self._cancel_key = cancel_key

        self._commands: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._mode = IDLE
        self._text = ""
        self._frames = 0

        # Set by the *calling* thread, not derived from the drained queue: with
        # `flow_bar: false` there is no render loop, nothing drains the queue,
        # and a queue-derived status would leave the tray permanently stuck on
        # "Idle" while dictation worked perfectly.
        self._status = STATUS_TEXT[IDLE]
        #: The raw state, tracked beside `_status` and for the same reason: the
        #: tray reads it, and with `flow_bar: false` nothing ever drains the
        #: queue. `status_text` cannot serve here because it maps MESSAGE onto
        #: "Idle" -- right for a menu line, wrong for a coloured dot.
        self._state_name = IDLE

        self._wave = wf.ScrollingWave(n_bars)
        self._heights = wf.idle_heights(n_bars)
        self._width = float(wf.PILL_WIDTH)
        self._pill_alpha = PILL_ALPHA[IDLE]
        self._bar_alpha = BAR_ALPHA[IDLE]
        self._text_alpha = TEXT_ALPHA[IDLE]
        self._open = 0.0
        #: Whether "Move bar" is on. Set by the platform layer, the only
        #: thing that knows -- same shape as `_hotkey`/`_cancel_key` below.
        self._movable = False

    # --- callable from any thread ------------------------------------------

    def show_recording(self) -> None:
        self._status = STATUS_TEXT[RECORDING]
        self._state_name = RECORDING
        self._commands.put((RECORDING, ""))

    def show_processing(self) -> None:
        self._status = STATUS_TEXT[TRANSCRIBING]
        self._state_name = TRANSCRIBING
        self._commands.put((TRANSCRIBING, ""))

    def hide(self) -> None:
        """Go idle. The bar fades out and, once the fade finishes, disappears
        -- unless "Move bar" is on, in which case it stays visible to drag."""
        self._status = STATUS_TEXT[IDLE]
        self._state_name = IDLE
        self._commands.put((IDLE, ""))

    def flash(self, message: str) -> None:
        """Show a message for MESSAGE_FRAMES, then return to idle by itself.

        Callers must NOT follow this with `hide()` -- that cancels the message
        before it can be read. `controller.py` already gets this right.
        """
        self._status = STATUS_TEXT[MESSAGE]
        self._state_name = MESSAGE
        self._commands.put((MESSAGE, message))

    def set_keys(self, hotkey: str, cancel_key: str) -> None:
        """Update the strip's key caps after a runtime hotkey change.

        Plain attribute assignment, exactly like `_status`/`_state_name`
        above: safe to call from the thread that changes the hotkey (the
        tray's "Change hotkey" worker thread) because `_strip()` only ever
        reads a whole `str` object on the render thread, and CPython never
        hands back a half-written one.

        Both keys always travel together, not two separate setters, because
        they are one invariant: `cancel_key` must be `""` exactly when
        `hotkey` is Esc -- the same rule `main.py`'s construction sites apply
        (`"" if CANCEL_KEY in spec.keys else CANCEL_KEY`) -- and a caller that
        set only one could leave a Cancel control advertised beside an Esc
        that `_handle_down` will never let it fire against.
        """
        self._hotkey = hotkey
        self._cancel_key = cancel_key

    def set_movable(self, movable: bool) -> None:
        """Told by the platform layer whenever "Move bar" is toggled.

        Plain attribute assignment, exactly like `set_keys` above: safe from
        any thread because `next_frame` only ever reads a whole `bool` on the
        render thread, and CPython never hands back a half-written one.

        This is what keeps idle visible while the bar is draggable -- see
        `next_frame` and `MOVABLE_IDLE_PILL_ALPHA`.
        """
        self._movable = bool(movable)

    def state_name(self) -> str:
        """The raw state, for the tray's status dot. Safe from any thread."""
        return self._state_name

    def status_text(self) -> str:
        """The tray's status line. Safe to call from the tray's thread."""
        return self._status

    # --- render thread only -------------------------------------------------

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

        # `open` is set DIRECTLY to its target, never eased: it is what
        # `height`, `radius` and the bar count below are all read off, and
        # easing it is exactly what used to grow and shrink the panel. A
        # state change now hops the shape in one frame; only the alphas (and,
        # for a shape that is staying a pill, the width) keep easing, so what
        # is left to see is a fade, not a resize.
        was_panel = self._open >= 1.0
        self._open = 1.0 if self._mode in PANEL_STATES else 0.0

        if self._mode in PANEL_STATES:
            # Full size the instant it appears -- never eased into.
            self._width = panel.PANEL_WIDTH
        else:
            target_width = (
                message_width(self._text)
                if self._mode == MESSAGE
                else float(wf.PILL_WIDTH)
            )
            # Still eased pill-to-pill -- a message widening to fit its text,
            # exactly as before -- but snapped, not eased, on the one frame
            # the shape just dropped out of the panel. Easing here too would
            # ease the width down from 420 while the height has already
            # snapped to the pill's 30: a squashed, panel-wide pill for a few
            # frames, which is a shrink in every way but name.
            self._width = (
                target_width if was_panel
                else _ease(self._width, target_width, FADE_ALPHA)
            )

        pill_alpha_target = PILL_ALPHA[self._mode]
        if self._mode == IDLE and self._movable:
            pill_alpha_target = MOVABLE_IDLE_PILL_ALPHA
        self._pill_alpha = _ease(self._pill_alpha, pill_alpha_target, FADE_ALPHA)
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
            visible=(
                self._mode in VISIBLE_STATES
                or self._movable
                or self._pill_alpha > HIDE_ALPHA_EPS
            ),
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

    # --- internals ----------------------------------------------------------

    def _advance_wave(self) -> tuple[float, ...]:
        """One frame of the scrolling trace, whatever state we are in.

        The wave is fed in *every* state, not only while recording, and that is
        what makes releasing the hotkey look right: the trace keeps scrolling
        with silence behind it, so what you just said drifts off the left edge
        instead of being blanked the instant you let go.
        """
        level = (
            wf.level_from_rms(self._read_level())
            if self._mode == RECORDING
            else 0.0
        )
        wave = self._wave.update(level)

        if self._mode == TRANSCRIBING:
            # Max, not replace: for the first second the draining trace is
            # still taller than the sweep and shows through, and the sweep
            # takes over by itself as the trace empties. A hard switch here
            # would produce exactly the instant reset the scrolling is meant
            # to avoid.
            sweep = wf.transcribing_heights(self._n_bars, self._frames)
            return tuple(max(w, s) for w, s in zip(wave, sweep))
        return wave

    def _read_level(self) -> float:
        """The current mic RMS. Never raises, and never blocks the renderer.

        A recorder torn down underneath us -- shutdown racing a final frame --
        must cost one flat frame, not the render thread.
        """
        if self._level_source is None:
            return 0.0
        try:
            return float(self._level_source())
        except Exception:  # noqa: BLE001 - decoration must not break drawing
            # Silent on purpose. This runs 60 times a second, so a recorder
            # that has gone away would print 60 tracebacks a second and bury
            # the console output that actually matters. One flat frame is the
            # right cost, and the next dictation reopens the stream anyway.
            return 0.0
