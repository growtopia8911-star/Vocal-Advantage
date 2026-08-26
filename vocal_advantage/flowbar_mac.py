"""The macOS Flow Bar: a click-through, non-activating panel drawn with AppKit.

This app pastes into whatever window has focus. An overlay that took focus
would send the paste into our own process instead of the user's editor, and the
dictation would be lost silently with the pill still looking perfect. The macOS
port design deferred the pill for exactly that reason. Three separate guards
close it now, and none of them is optional:

* ``NSWindowStyleMaskNonactivatingPanel`` -- clicking the panel never activates
  our application.
* ``setIgnoresMouseEvents_(True)`` -- clicks are not merely ignored, they are
  delivered to whatever is underneath. This is the click-through requirement,
  and it is the one line Tk on macOS has no equivalent for at all, which is why
  this file exists rather than a cross-platform tkinter one.
* ``NSApplicationActivationPolicyAccessory`` -- no Dock icon and no application
  menu, so nothing about this process is focusable. It is also what makes the
  "Python rocket in the Dock" problem noted in ``main.py`` go away.

Everything here must run on the main thread: AppKit is not thread-safe, and
``NSApplication`` owns the run loop. The controller, hotkey and audio threads
are untouched -- they talk to ``flowbar.Indicator``, which is a queue.
"""

from __future__ import annotations

from vocal_advantage import flowbar
from vocal_advantage import panel
from vocal_advantage import waveform as wf

# Guarded exactly as hotkey_mac and paste_mac guard Quartz, and for the same
# reason: this file must import on Windows so `pytest` can collect it and so
# `pill_origin` -- which is plain arithmetic -- can be tested anywhere. Only the
# drawing genuinely needs AppKit.
try:  # pragma: no cover - absence is only reachable off macOS
    import objc
    from AppKit import (
        NSApplication,
        NSBezierPath,
        NSColor,
        NSEvent,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSGradient,
        NSGraphicsContext,
        NSMutableParagraphStyle,
        NSPanel,
        NSParagraphStyleAttributeName,
        NSRunLoop,
        NSRunLoopCommonModes,
        NSScreen,
        NSTimer,
        NSView,
    )
    from Foundation import NSMakeRect, NSString
except ImportError:  # pragma: no cover - not macOS
    objc = None
    NSApplication = NSBezierPath = NSColor = NSEvent = NSFont = None
    NSFontAttributeName = NSForegroundColorAttributeName = None
    NSGradient = NSGraphicsContext = None
    NSMutableParagraphStyle = NSPanel = NSParagraphStyleAttributeName = None
    NSRunLoop = NSRunLoopCommonModes = NSScreen = NSTimer = None
    NSMakeRect = NSString = None
    # Subclassing needs *something*; the class is never instantiated off macOS.
    NSView = object

# --- AppKit constants -------------------------------------------------------
# Spelled out rather than imported: several of these are absent from older
# pyobjc releases, and a missing name here would take the whole app down at
# import time for a decoration.
NSWindowStyleMaskBorderless = 0
NSWindowStyleMaskNonactivatingPanel = 1 << 7
NSBackingStoreBuffered = 2
#: Above ordinary and floating windows, below the screen saver. High enough to
#: stay visible over a full-screen editor, low enough not to cover system alerts.
NSStatusWindowLevel = 25
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorStationary = 1 << 4
NSWindowCollectionBehaviorIgnoresCycle = 1 << 6
NSApplicationActivationPolicyAccessory = 1
NSTextAlignmentLeft = 0
NSTextAlignmentCenter = 2

# --- palette ----------------------------------------------------------------
# The colours themselves live in `panel.py`, as 0-255 integer triples -- not
# here. Keeping a second copy in float-0-1 AppKit form is the exact drift this
# design exists to prevent: `PILL_FILL_RGB` used to disagree, silently, with
# its Pillow counterpart. `_colour` below is what converts, once, on the way
# in.

#: Drawn only while "Move bar" is on. Not decoration: in that mode the pill
#: stops being click-through and starts eating clicks, so it has to be
#: unmistakable that the mode is on -- leaving it on by accident is the one real
#: drawback of a menu toggle, and this is what stops it happening quietly.
MOVE_OUTLINE_RGB = (0.15, 0.45, 0.95)
MOVE_OUTLINE_WIDTH = 2.0

FPS = 60
SIDE_MARGIN = 24        # from the screen edge, for the left/right positions
MESSAGE_FONT_SIZE = 10.0

POSITIONS = ("bottom-centre", "bottom-left", "bottom-right")


def ensure_app() -> NSApplication:
    """The shared NSApplication, configured as a background accessory.

    Accessory policy is what keeps this process out of the Dock and out of
    Cmd-Tab. Safe to call more than once.
    """
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    return app


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


def _rect(r) -> "NSMakeRect":
    """A `panel.Rect` as an NSRect. Safe because the view is flipped."""
    return NSMakeRect(r.x, r.y, r.w, r.h)


def point_origin(point, width: float, height: float, visible_frame):
    """Bottom-left corner for a dragged pill, clamped onto the visible screen.

    ``point`` is (centre_x, bottom_y) -- the centre rather than the left edge,
    so the pill grows evenly in both directions when it widens for a message
    instead of walking sideways every time one appears.

    The clamp is the point of this function. A saved position can name a
    monitor that has since been unplugged, or a spot that is now under a Dock
    that moved; without it the bar is simply invisible and there is nothing on
    screen to drag it back with.
    """
    centre_x, bottom_y = float(point[0]), float(point[1])
    x_min = visible_frame.origin.x
    y_min = visible_frame.origin.y
    x_max = x_min + visible_frame.size.width - width
    y_max = y_min + visible_frame.size.height - height
    x = min(max(centre_x - width / 2.0, x_min), max(x_min, x_max))
    y = min(max(bottom_y, y_min), max(y_min, y_max))
    return x, y


def pill_origin(position: str, width: float, visible_frame):
    """Bottom-left corner of the pill, in macOS screen coordinates (y is up).

    ``visibleFrame`` already excludes the menu bar and the Dock, so
    ``SCREEN_MARGIN`` is clearance above the Dock rather than the screen edge.
    """
    x_min = visible_frame.origin.x
    y_min = visible_frame.origin.y
    screen_width = visible_frame.size.width

    if position == "bottom-left":
        x = x_min + SIDE_MARGIN
    elif position == "bottom-right":
        x = x_min + screen_width - width - SIDE_MARGIN
    else:
        x = x_min + (screen_width - width) / 2.0
    return x, y_min + wf.SCREEN_MARGIN


class _PillView(NSView):
    """Draws one `flowbar.Frame`. Holds no state of its own beyond that frame."""

    def initWithFrame_(self, rect):
        self = objc.super(_PillView, self).initWithFrame_(rect)
        if self is None:
            return None
        self._data = None
        self._hover = ""
        self._on_click = None
        return self

    def isFlipped(self) -> bool:
        """Top-left origin, y down -- matching `panel` and Pillow.

        The bars are symmetric about the horizontal centre line, so this does
        not change how they draw. It exists so the strip's rects can be used
        exactly as `panel.layout` returns them.
        """
        return True

    def setData_(self, data) -> None:
        self._data = data
        self._hover = data.hover

    def setMovable_(self, movable) -> None:
        self._movable = bool(movable)

    def mouseDown_(self, event) -> None:
        """Dispatch a click on a hovered strip item, else start a window drag.

        Reachable in two situations now: the cursor is over the panel (`_tick`
        dropped ignoresMouseEvents for exactly that reason), or move mode is on
        and ignoresMouseEvents is off for the whole panel. A click on a hovered
        item takes priority and never starts a drag; nothing here fires unless
        one of those two conditions put us on the receiving end of a mouse
        event in the first place -- the rest of the time ignoresMouseEvents is
        set and no mouse event arrives here at all.

        performWindowDragWithEvent_ hands the whole drag to AppKit, which is
        both less code and better behaved than tracking mouseDragged_ by hand --
        it gets screen edges and multiple displays right for free.
        """
        hover = getattr(self, "_hover", "")
        callback = getattr(self, "_on_click", None)
        if hover and callback is not None:
            callback(hover)
            return
        if getattr(self, "_movable", False):
            self.window().performWindowDragWithEvent_(event)

    def isOpaque(self) -> bool:
        # False is the default, but saying so is what lets the rounded corners
        # show the desktop rather than a grey box.
        return False

    def drawRect_(self, _dirty) -> None:
        data = getattr(self, "_data", None)
        if data is None:
            return

        bounds = self.bounds()
        width = bounds.size.width
        height = bounds.size.height
        placed = panel.layout(
            width, height, data.radius, data.open,
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

        if panel.PILL_BORDER_WIDTH > 0.0:
            _colour(panel.PILL_BORDER_RGB, data.pill_alpha).set()
            clip.setLineWidth_(panel.PILL_BORDER_WIDTH)
            clip.stroke()

        if data.dot is not None:
            self._draw_dot(data, width, height)
        if data.bar_alpha > 0.01:
            self._draw_bars(data, placed)
        # Same test `_draw_strip` uses internally to decide whether to draw
        # at all, so the guard here and the guard inside it cannot disagree
        # about whether the strip is visible this frame.
        if panel.strip_alpha(placed.strip.h) > 0.0:
            self._draw_strip(data, placed)
        if data.text and data.text_alpha > 0.01:
            self._draw_message(data, width, height)
        if getattr(self, "_movable", False):
            self._draw_move_outline(width, height, data.radius)

    def _draw_move_outline(self, width: float, height: float, radius: float) -> None:
        """Say loudly that clicks are being intercepted right now.

        The radius came from `(height - MOVE_OUTLINE_WIDTH) / 2.0` -- full
        round, which was right when the bar was always the 30pt pill. At the
        96pt panel that computed a radius of 47 against the panel's actual
        `radius` of 12, so the outline sliced across the waveform band and
        cut the corners off the strip instead of tracing the panel's real
        shape. `radius` is `data.radius` -- the same value `drawRect_` clips
        the whole panel to -- inset by half the stroke width so the outline's
        curve stays concentric with that clip rather than bulging outside it.
        """
        inset = MOVE_OUTLINE_WIDTH / 2.0
        red, green, blue = MOVE_OUTLINE_RGB
        NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0).set()
        outline_radius = max(0.0, radius - inset)
        outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(
                inset, inset,
                width - MOVE_OUTLINE_WIDTH, height - MOVE_OUTLINE_WIDTH,
            ),
            outline_radius,
            outline_radius,
        )
        outline.setLineWidth_(MOVE_OUTLINE_WIDTH)
        outline.stroke()

    def _draw_dot(self, data, width: float, height: float) -> None:
        """The state dot on the compact indicator.

        Rides `pill_alpha` rather than `bar_alpha` so it fades with the pill
        it sits in: the bars drop to nothing for a message, and a dot left at
        full ink over a fading pill would be the last thing on screen.
        """
        _colour(data.dot, data.pill_alpha).set()
        NSBezierPath.bezierPathWithOvalInRect_(
            _rect(panel.compact_dot(width, height))
        ).fill()

    def _draw_bars(self, data, placed) -> None:
        band = placed.band
        if band.h <= 0.0:
            return
        if data.dot is not None:
            # The compact indicator: the dot owns the left end, so the trace
            # gets everything right of it rather than the full width. Taken
            # from `panel` rather than worked out here, so the Windows
            # renderer cannot lay the same bars out anywhere else.
            band = panel.compact_trace(band.w, band.h)
        centre_y = band.y + band.h / 2.0
        # `panel.PEAK_FRACTION` is 69% of band height at peak, mirrored -- so
        # the tallest bar's half is half of that. See panel.py for why this
        # is not a bare float here.
        max_half = band.h * panel.PEAK_FRACTION / 2.0

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

    def _draw_strip(self, data, placed) -> None:
        """The dot, the state word, and each control beside its own key cap.

        Alpha rides the strip's own real height against what its tallest
        content needs -- see `panel.strip_alpha` -- not `open` directly.
        "Alpha rides `open`" was the plan's original claim, and it was wrong:
        alpha does not stop geometric overflow, and a part-grown strip can be
        shorter than the 20pt key cap it holds, which clips against the
        panel's rounded corner at any alpha. Waiting until the strip can
        actually hold its contents is what `strip_alpha` buys instead.
        """
        alpha = panel.strip_alpha(placed.strip.h)
        if alpha <= 0.0:
            return
        if placed.dot is not None:
            dot_rgb = panel.DOT_RECORDING_RGB
            if data.state == flowbar.TRANSCRIBING:
                dot_rgb = panel.DOT_TRANSCRIBING_RGB
            _colour(dot_rgb, alpha).set()
            NSBezierPath.bezierPathWithOvalInRect_(_rect(placed.dot)).fill()

        if placed.state_rect is not None and placed.state_label:
            self._draw_text(
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
            self._draw_text(
                item.label, item.label_rect,
                panel.LABEL_FONT_SIZE, panel.TEXT_RGB, alpha,
            )
            if item.cap_rect is not None:
                _colour(panel.CAP_FILL_RGB, alpha).set()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    _rect(item.cap_rect), panel.CAP_RADIUS, panel.CAP_RADIUS
                ).fill()
                self._draw_text(
                    item.cap, item.cap_rect,
                    panel.CAP_FONT_SIZE, panel.TEXT_RGB, alpha,
                    centred=True,
                )

        if placed.divider is not None:
            _colour(panel.HAIRLINE_RGB, alpha).set()
            NSBezierPath.bezierPathWithRect_(_rect(placed.divider)).fill()

    def _draw_text(self, string, rect, size, rgb, alpha, centred=False) -> None:
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

    def _draw_message(self, data, width: float, height: float) -> None:
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(NSTextAlignmentCenter)
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(MESSAGE_FONT_SIZE),
            # Was an 8%-white ink, correct against the old warm-paper pill and
            # a 1.35:1 contrast against `panel.PILL_FILL_RGB` -- the near-black
            # ground this panel redesign introduced. `panel.TEXT_RGB` is what
            # the strip's own labels already use, and taking it from `panel`
            # closes the last colour either renderer held itself.
            NSForegroundColorAttributeName: _colour(panel.TEXT_RGB, data.text_alpha),
            NSParagraphStyleAttributeName: style,
        }
        text = NSString.stringWithString_(data.text)
        size = text.sizeWithAttributes_(attributes)
        text.drawInRect_withAttributes_(
            NSMakeRect(0, (height - size.height) / 2.0, width, size.height),
            attributes,
        )


class FlowBar:
    """The panel, its render timer, and where on screen it sits.

    Main thread only. `open()` builds the window; the timer then pulls a frame
    from the `Indicator` 60 times a second and asks the view to redraw.
    """

    def __init__(
        self,
        indicator,
        position: str = "bottom-centre",
        fps: int = FPS,
        point=None,
        on_click=None,
    ) -> None:
        self._indicator = indicator
        self._position = position if position in POSITIONS else "bottom-centre"
        self._fps = fps
        #: (centre_x, bottom_y) once dragged, else None to use `position`.
        self._point = list(point) if point else None
        #: Called with a strip item's id on click. Task 8 supplies it; this
        #: task only builds the channel, so None (the default) is a no-op.
        self._on_click = on_click
        self._panel = None
        self._view = None
        self._timer = None
        self._movable = False
        self._width = float(wf.PILL_WIDTH)
        #: Whether the panel is currently ordered on screen. Tracked
        #: separately from `Frame.visible` so `_tick` only calls
        #: `orderFrontRegardless`/`orderOut_` on an actual transition, not
        #: every frame.
        self._shown = False
        #: What was last drawn, for hit-testing the *next* tick's cursor
        #: sample against. One frame stale, which at 60fps nobody can see.
        self._last_layout = None
        self._last_origin = (0.0, 0.0)
        #: Whether the cursor was inside the panel as of the last tick that
        #: changed it -- so the ignoresMouseEvents flag is only touched on a
        #: transition, not every frame.
        self._interactive = False

    def open(self) -> None:
        height = float(wf.PILL_HEIGHT)
        x, y = self._origin(self._width, height)
        rect = NSMakeRect(x, y, self._width, height)

        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        # Transparent everywhere outside the pill, so what you see is the
        # rounded shape and not a grey box around it.
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setHasShadow_(False)

        self._panel.setIgnoresMouseEvents_(True)   # the click-through guarantee
        self._panel.setFloatingPanel_(True)
        self._panel.setBecomesKeyOnlyIfNeeded_(True)
        # AFTER setFloatingPanel_, never before: that setter assigns
        # NSFloatingWindowLevel itself, so setting the level first silently
        # leaves the panel at level 3 and it loses to any other floating
        # window. Verified by reading .level() back.
        self._panel.setLevel_(NSStatusWindowLevel)
        # Our app is never active, so without this the panel would vanish the
        # moment you clicked anything else -- which is always.
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )

        self._view = _PillView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self._width, height)
        )
        self._view._on_click = self._on_click
        self._panel.setContentView_(self._view)
        # Not shown here, and not unconditionally: nothing is on screen at
        # idle now, so whether the panel is ordered front at all is decided
        # every tick from `frame.visible`, same as its size and its data.
        # `_tick` reuses this exact call -- see there for the focus note.

        self._start_timer()

    def _start_timer(self) -> None:
        timer = NSTimer.timerWithTimeInterval_repeats_block_(
            1.0 / self._fps, True, lambda _timer: self._tick()
        )
        # Common modes, not the default mode: while a menu is being tracked the
        # default mode is suspended, and the bar would freeze every time the
        # tray menu was open.
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self._timer = timer

    def _tick(self) -> None:
        location = NSEvent.mouseLocation()
        hover = self._hover_for(location.x, location.y)
        # `and self._shown`: a hidden panel's `_last_layout`/`_last_origin`
        # are stale geometry from wherever it last drew, and the cursor can
        # easily be sitting inside that stale rect while the panel is off
        # screen and idle. Without this, that would drop click-through on a
        # window that is not there to click -- harmless to the user, who has
        # nothing to click, but it would leave `_interactive` wrong for the
        # moment the panel is next shown.
        inside = self._contains(location.x, location.y) and self._shown
        if inside != self._interactive:
            self._interactive = inside
            # Move bar mode owns this flag while it is on; never fight it.
            if not self.movable:
                self._panel.setIgnoresMouseEvents_(not inside)

        frame = self._indicator.next_frame(hover=hover)
        if abs(frame.width - self._width) > 0.5:
            self._resize(frame.width, frame.height)
        self._view.setData_(frame)
        self._view.setNeedsDisplay_(True)

        if frame.visible != self._shown:
            self._shown = frame.visible
            if self._shown:
                # Not the "make key" variant: that would activate us and
                # steal focus, sending the user's next paste into our own
                # process -- the one guarantee this file's docstring will
                # not bend on.
                self._panel.orderFrontRegardless()
            else:
                self._panel.orderOut_(None)

        # Recorded for the *next* tick's hit-test: `_hover_for`/`_contains`
        # must be pure, so they read this rather than the frame just drawn.
        self._last_origin = self._origin(frame.width, frame.height)
        self._last_layout = panel.layout(
            frame.width, frame.height, frame.radius, frame.open,
            flowbar.STATUS_TEXT.get(frame.state, ""), frame.strip,
        )

    def _hover_for(self, screen_x: float, screen_y: float) -> str:
        """Which strip item the cursor is over, in screen coordinates.

        Split out from the tick and given no side effects so it can be tested
        without a cursor, a screen or a run loop.
        """
        placed = self._last_layout
        if placed is None:
            return ""
        origin_x, origin_y = self._last_origin
        x = screen_x - origin_x
        # The view is flipped; the window's origin is its bottom-left.
        y = origin_y + placed.height - screen_y
        return panel.hit_test(placed, x, y) or ""

    def _contains(self, screen_x: float, screen_y: float) -> bool:
        """Whether a screen point falls inside the last drawn panel, AND the
        panel actually has something in it to click.

        This is what decides click-through, not `_hover_for`: the panel
        should stop ignoring clicks as soon as the cursor is anywhere over it,
        not only over a strip item -- but only in a state that has controls at
        all. Without the `placed.items` check, the resting pill and the
        TRANSCRIBING panel (which opens but, by design, offers zero controls)
        both ate every click that crossed them, including ones meant for
        whatever sits underneath -- the Dock, at the resting pill's position.

        `panel.strip_alpha(placed.strip.h) > 0.0` is the same test
        `_draw_strip` gates drawing on, so a part-grown strip cannot be
        clickable before it is visible: `panel.layout` builds `items` as soon
        as the strip has any height at all, well before `strip_alpha` says
        there is anything to see.
        """
        placed = self._last_layout
        if placed is None:
            return False
        if not placed.items or panel.strip_alpha(placed.strip.h) <= 0.0:
            return False
        origin_x, origin_y = self._last_origin
        return (
            origin_x <= screen_x < origin_x + placed.width
            and origin_y <= screen_y < origin_y + placed.height
        )

    def _origin(self, width: float, height: float):
        """Where the pill goes: a dragged point if there is one, else a preset."""
        visible = NSScreen.mainScreen().visibleFrame()
        if self._point is not None:
            return point_origin(self._point, width, height, visible)
        return pill_origin(self._position, width, visible)

    def _resize(self, width: float, height: float) -> None:
        """Re-anchor as the panel grows, so its bottom edge does not drift."""
        self._width = width
        origin = self._origin(width, height)
        self._panel.setFrame_display_(
            NSMakeRect(origin[0], origin[1], width, height), True
        )

    # --- "Move bar" -------------------------------------------------------

    def set_movable(self, movable: bool) -> None:
        """Let the pill be dragged, at the cost of click-through while it is on.

        These are one setting, not two: a window that ignores mouse events
        cannot be dragged, because it never receives the mouse-down that would
        start the drag. So move mode is exactly "stop ignoring them", and the
        blue outline the view draws is what makes that visible.
        """
        self._movable = bool(movable)
        # So idle stays visible while there is something to drag -- see
        # `flowbar.Indicator.set_movable`.
        self._indicator.set_movable(self._movable)
        if self._panel is not None:
            self._panel.setIgnoresMouseEvents_(not self._movable)
        if self._view is not None:
            self._view.setMovable_(self._movable)

    @property
    def movable(self) -> bool:
        return self._movable

    def current_point(self):
        """Where it is now, as (centre_x, bottom_y), or None if never opened.

        Read back off the panel rather than tracked as the drag happens:
        AppKit owns the drag once performWindowDragWithEvent_ takes over, and
        the window's own frame is the only account of where it ended up that
        cannot disagree with the screen.
        """
        if self._panel is None:
            return None
        frame = self._panel.frame()
        return [
            float(frame.origin.x + frame.size.width / 2.0),
            float(frame.origin.y),
        ]

    def close(self) -> None:
        """Stop drawing and take the panel off screen. Safe to call twice."""
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel = None
        self._view = None
