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
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
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
    NSApplication = NSBezierPath = NSColor = NSFont = None
    NSFontAttributeName = NSForegroundColorAttributeName = None
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
#: A light ground with black bars on it, and NO outline. The edge of the fill is
#: what defines the shape now, so the rounded ends come purely from the
#: antialiased fill -- which is why the fill must never be drawn inset for a
#: stroke that no longer exists, or the pill loses a pixel all the way round.
#: Warm rather than pure white, so it reads as paper rather than a blown-out box.
PILL_FILL_RGB = (0.97, 0.965, 0.945)
BAR_RGB = (0.0, 0.0, 0.0)

#: Drawn only while "Move bar" is on. Not decoration: in that mode the pill
#: stops being click-through and starts eating clicks, so it has to be
#: unmistakable that the mode is on -- leaving it on by accident is the one real
#: drawback of a menu toggle, and this is what stops it happening quietly.
MOVE_OUTLINE_RGB = (0.15, 0.45, 0.95)
MOVE_OUTLINE_WIDTH = 2.0

FPS = 60
SIDE_MARGIN = 24        # from the screen edge, for the left/right positions
MESSAGE_FONT_SIZE = 10.0
#: Smaller than a message: a standing reminder, not an announcement.
LEGEND_FONT_SIZE = 9.5
#: From the pill's left edge. Comfortably inside the rounded end.
LEGEND_PAD_X = 12.0

POSITIONS = ("bottom-centre", "bottom-left", "bottom-right")


def ensure_app() -> NSApplication:
    """The shared NSApplication, configured as a background accessory.

    Accessory policy is what keeps this process out of the Dock and out of
    Cmd-Tab. Safe to call more than once.
    """
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    return app


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


class _FlowBarView(NSView):
    """Draws one `flowbar.Frame`. Holds no state of its own beyond that frame."""

    def initWithFrame_(self, rect):
        self = objc.super(_FlowBarView, self).initWithFrame_(rect)
        if self is None:
            return None
        self._data = None
        return self

    def setData_(self, data) -> None:
        self._data = data

    def setMovable_(self, movable) -> None:
        self._movable = bool(movable)

    def mouseDown_(self, event) -> None:
        """Start a native window drag.

        Only ever reached while move mode is on: the rest of the time the panel
        has ignoresMouseEvents set and no mouse event arrives here at all.

        performWindowDragWithEvent_ hands the whole drag to AppKit, which is
        both less code and better behaved than tracking mouseDragged_ by hand --
        it gets screen edges and multiple displays right for free.
        """
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
        radius = height / 2.0     # fully rounded ends, not a rounded rectangle

        # Fill only, drawn on the full bounds. With the outline gone there is
        # nothing to inset for, and insetting anyway would shrink the pill.
        fill_red, fill_green, fill_blue = PILL_FILL_RGB
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            fill_red, fill_green, fill_blue, data.pill_alpha
        ).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, width, height), radius, radius
        ).fill()

        if data.bar_alpha > 0.01:
            self._draw_bars(data, width, height)
        if data.legend and data.bar_alpha > 0.01:
            self._draw_legend(data, width, height)
        if data.text and data.text_alpha > 0.01:
            self._draw_message(data, width, height)
        if getattr(self, "_movable", False):
            self._draw_move_outline(width, height)

    def _draw_move_outline(self, width: float, height: float) -> None:
        """Say loudly that clicks are being intercepted right now."""
        inset = MOVE_OUTLINE_WIDTH / 2.0
        red, green, blue = MOVE_OUTLINE_RGB
        NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0).set()
        outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(
                inset, inset,
                width - MOVE_OUTLINE_WIDTH, height - MOVE_OUTLINE_WIDTH,
            ),
            (height - MOVE_OUTLINE_WIDTH) / 2.0,
            (height - MOVE_OUTLINE_WIDTH) / 2.0,
        )
        outline.setLineWidth_(MOVE_OUTLINE_WIDTH)
        outline.stroke()

    def _draw_bars(self, data, width: float, height: float) -> None:
        centre_y = height / 2.0
        max_half = height / 2.0 - wf.BAR_MARGIN_Y
        # With a legend the trace keeps its resting width and moves to the
        # right-hand end, so the text gets the space the pill grew by and the
        # bars stay exactly the size they are at rest. Laying them out across
        # the whole widened pill instead would stretch the trace every time a
        # recording started, which reads as the waveform changing shape.
        bars_width = float(wf.PILL_WIDTH) if data.legend else width
        offset = width - bars_width
        xs = [x + offset for x in wf.bar_layout(bars_width, len(data.heights))]

        bar_red, bar_green, bar_blue = BAR_RGB
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            bar_red, bar_green, bar_blue, data.bar_alpha
        ).set()
        for x, normalised in zip(xs, data.heights):
            # Mirrored about the centre line: each bar grows up and down by the
            # same amount. A bar chart would run from the bottom edge instead,
            # and would look completely wrong.
            half = normalised * max_half
            # Never shorter than it is wide, so the round caps stay circular
            # instead of squashing into an ellipse at rest.
            total = max(wf.BAR_WIDTH, half * 2.0)
            bar = NSMakeRect(
                x - wf.BAR_WIDTH / 2.0, centre_y - total / 2.0,
                wf.BAR_WIDTH, total,
            )
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar, wf.BAR_WIDTH / 2.0, wf.BAR_WIDTH / 2.0
            ).fill()

    def _draw_legend(self, data, width: float, height: float) -> None:
        """The hotkey reminder, left-aligned in the space the pill grew by.

        Grey rather than black: it is a standing reminder sitting next to the
        thing you are actually watching, and at full ink it would compete with
        the trace. Alpha rides `bar_alpha` so it fades in with the bars instead
        of needing a channel of its own.
        """
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(NSTextAlignmentLeft)
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(LEGEND_FONT_SIZE),
            NSForegroundColorAttributeName:
                NSColor.colorWithCalibratedWhite_alpha_(0.34, data.bar_alpha),
            NSParagraphStyleAttributeName: style,
        }
        text = NSString.stringWithString_(data.legend)
        size = text.sizeWithAttributes_(attributes)
        available = max(0.0, width - float(wf.PILL_WIDTH) - LEGEND_PAD_X)
        text.drawInRect_withAttributes_(
            NSMakeRect(
                LEGEND_PAD_X, (height - size.height) / 2.0,
                available, size.height,
            ),
            attributes,
        )

    def _draw_message(self, data, width: float, height: float) -> None:
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(NSTextAlignmentCenter)
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(MESSAGE_FONT_SIZE),
            NSForegroundColorAttributeName:
                NSColor.colorWithCalibratedWhite_alpha_(0.08, data.text_alpha),
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
    ) -> None:
        self._indicator = indicator
        self._position = position if position in POSITIONS else "bottom-centre"
        self._fps = fps
        #: (centre_x, bottom_y) once dragged, else None to use `position`.
        self._point = list(point) if point else None
        self._panel = None
        self._view = None
        self._timer = None
        self._movable = False
        self._width = float(wf.PILL_WIDTH)

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

        self._view = _FlowBarView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self._width, height)
        )
        self._panel.setContentView_(self._view)
        # Not makeKeyAndOrderFront_: that would activate us and steal focus.
        self._panel.orderFrontRegardless()

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
        frame = self._indicator.next_frame()
        if abs(frame.width - self._width) > 0.5:
            self._resize(frame.width)
        self._view.setData_(frame)
        self._view.setNeedsDisplay_(True)

    def _origin(self, width: float, height: float):
        """Where the pill goes: a dragged point if there is one, else a preset."""
        visible = NSScreen.mainScreen().visibleFrame()
        if self._point is not None:
            return point_origin(self._point, width, height, visible)
        return pill_origin(self._position, width, visible)

    def _resize(self, width: float) -> None:
        """Re-anchor as the pill widens for a message, so it does not drift."""
        self._width = width
        height = float(wf.PILL_HEIGHT)
        x, y = self._origin(width, height)
        self._panel.setFrame_display_(NSMakeRect(x, y, width, height), False)
        self._view.setFrame_(NSMakeRect(0, 0, width, height))

    # --- "Move bar" -------------------------------------------------------

    def set_movable(self, movable: bool) -> None:
        """Let the pill be dragged, at the cost of click-through while it is on.

        These are one setting, not two: a window that ignores mouse events
        cannot be dragged, because it never receives the mouse-down that would
        start the drag. So move mode is exactly "stop ignoring them", and the
        blue outline the view draws is what makes that visible.
        """
        self._movable = bool(movable)
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
