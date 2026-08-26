"""The Windows Flow Bar: a per-pixel-alpha layered window, drawn with Pillow.

**Written on a Mac and never executed.** Every Win32 call below is reasoned
from the API contract, not observed. It is on the hand-check list, and the
failure modes to look for are called out in comments where they apply.

Why not tkinter, given the old pill used it: Tk's transparency on Windows is
``-transparentcolor``, which is a colour *key* -- a per-pixel yes/no test. The
pill's rounded ends would come out as visible stair-steps, and Tk cannot do
per-pixel alpha at all. ``UpdateLayeredWindow`` can, which buys smooth corners
and real translucency. It also removes Tk from the project, and with it the
only thing on Windows that wanted the main thread -- so the tray icon can have
it, exactly as on macOS.

The three window guarantees, which are the whole point of the overlay:

* ``WS_EX_TRANSPARENT`` -- clicks pass through to whatever is underneath. This
  is the click-through requirement. Note it is *not* ``WS_EX_NOACTIVATE``,
  which only stops activation; the old `indicator_win.py` had NOACTIVATE and
  was never click-through.
* ``WS_EX_NOACTIVATE`` -- never takes focus, so a paste can never land in our
  own process.
* ``WS_EX_TOOLWINDOW`` -- no taskbar button and no Alt+Tab entry.

Threading: a window belongs to the thread that created it, so this owns one
thread, creates the window on it, renders at 60fps and pumps messages. Nothing
here touches the controller or the recorder beyond reading one float.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vocal_advantage import flowbar
from vocal_advantage import panel
from vocal_advantage import waveform as wf
from vocal_advantage.console import warn

# --- Win32 constants --------------------------------------------------------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020      # the click-through bit
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

BI_RGB = 0
DIB_RGB_COLORS = 0

PM_REMOVE = 0x0001
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_LBUTTONDOWN = 0x0201
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2                        # "treat this click as one on the title bar"
GWL_EXSTYLE = -20

SM_CXSCREEN = 0
SM_CYSCREEN = 1

PROCESS_PER_MONITOR_DPI_AWARE = 2
_E_ACCESSDENIED = -2147024891

FPS = 60
SIDE_MARGIN = 24
MESSAGE_FONT_SIZE = 11

#: Rendered at this multiple and scaled down: Pillow has no antialiasing, and
#: the whole reason for this file is that the corners should not be jagged.
SUPERSAMPLE = 4

POSITIONS = ("bottom-centre", "bottom-left", "bottom-right")


if sys.platform == "win32":  # pragma: no cover - Windows only
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _shcore = ctypes.WinDLL("shcore", use_last_error=True)

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_byte),
            ("BlendFlags", ctypes.c_byte),
            ("SourceConstantAlpha", ctypes.c_byte),
            ("AlphaFormat", ctypes.c_byte),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    # LRESULT is LONG_PTR -- 64-bit in a 64-bit process. Declaring the window
    # procedure as returning c_long (32-bit) truncates every reply we give
    # Windows.
    LRESULT = ctypes.c_ssize_t

    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT,
        wintypes.WPARAM, wintypes.LPARAM,
    )

    # Without an explicit prototype ctypes marshals every Python int as a
    # 32-bit C int. lparam is pointer-sized and genuinely carries pointers
    # (WM_CREATE passes a CREATESTRUCTW*), so the very first message raises
    # "OverflowError: int too long to convert" inside the callback -- where
    # Python cannot propagate it, so it prints a traceback, returns 0 to
    # Windows, and the message goes unhandled.
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _user32.DefWindowProcW.restype = LRESULT

    # Same reasoning for every call below that takes a handle or a
    # pointer-sized argument. This file has already been bitten once.
    _user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _user32.SendMessageW.restype = LRESULT
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = wintypes.LONG
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    _user32.SetWindowLongW.restype = wintypes.LONG
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.ReleaseCapture.argtypes = []
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]
else:  # pragma: no cover - lets this file import on macOS for the tests
    _user32 = _gdi32 = _shcore = None
    POINT = SIZE = BLENDFUNCTION = BITMAPINFO = BITMAPINFOHEADER = None
    WNDPROC = WNDCLASSEXW = LRESULT = None


def set_dpi_awareness() -> None:
    """Tell Windows we do our own scaling. Call once, before the first window.

    Without it Windows bitmap-stretches the pill (soft edges, defeating the
    whole point of per-pixel alpha) and the "bottom centre of the screen"
    arithmetic is wrong on any display above 100% scaling. Calling it twice is
    harmless: Windows answers E_ACCESSDENIED and that is ignored.
    """
    if _shcore is None:
        return
    hresult = _shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
    if hresult not in (0, _E_ACCESSDENIED):  # pragma: no cover - cosmetic only
        warn(
            f"[flowbar] SetProcessDpiAwareness failed "
            f"(0x{hresult & 0xFFFFFFFF:08X}); the pill may look blurry"
        )


def pill_origin(
    position: str, width: float, height: float, screen_width: int, screen_height: int
):
    """Top-left corner of the pill, in Windows screen coordinates (y is down).

    The macOS twin measures from the bottom up, because that is how AppKit's
    coordinates run. Same result on screen, opposite arithmetic -- which is
    exactly the sort of thing that silently puts the bar off the top of one
    machine, hence a test for each.

    ``height`` is the *live* frame height, not a hardcoded ``wf.PILL_HEIGHT``
    -- gate 3c: the panel opens upward from a stationary bottom edge. The
    anchor is ``screen_height - SCREEN_MARGIN`` (the bottom edge), and ``y``
    -- the top edge -- must fall as ``height`` rises to keep that bottom edge
    fixed while the pill grows into the 96pt panel. Getting the sign backwards
    here reads as a plausible picture (the bar still appears, still animates)
    and only shows up as the bottom edge sliding down the screen.
    """
    if position == "bottom-left":
        x = SIDE_MARGIN
    elif position == "bottom-right":
        x = screen_width - width - SIDE_MARGIN
    else:
        x = (screen_width - width) / 2.0
    return int(round(x)), int(round(screen_height - wf.SCREEN_MARGIN - height))


def point_origin(point, width: float, height: float, screen_width: int,
                 screen_height: int):
    """Top-left corner for a *dragged* position, clamped on screen.

    ``point`` is ``(centre_x, bottom_y)`` -- the centre rather than the left
    edge, so the pill grows symmetrically when it widens for a message instead
    of drifting sideways.

    **``bottom_y`` is measured downward from the top of the screen here**, and
    upward from the bottom on macOS, exactly as `pill_origin` is. The saved
    value therefore means different things on the two machines -- which is
    fine, because `config.json` is per-machine and gitignored, and is noted
    because a shared one would silently put the bar in the wrong place.

    The clamp is the point of this function: a saved position can name a
    monitor that is no longer attached, and a pill nobody can see reads as the
    app being broken.
    """
    centre_x, bottom_y = float(point[0]), float(point[1])
    x = centre_x - width / 2.0
    y = bottom_y - height
    x = max(0.0, min(x, screen_width - width))
    y = max(0.0, min(y, screen_height - height))
    return int(round(x)), int(round(y))


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


def render_frame(frame, width: int, height: int) -> Image.Image:
    """One `flowbar.Frame` as an RGBA image. Pure: no Win32, no window.

    Kept importable and callable on any platform on purpose -- it is the half of
    this file that can be looked at from the Mac, by saving the result to a PNG.

    Every rect this draws comes from `panel.layout`, exactly as
    `flowbar_mac._PillView.drawRect_` does -- neither renderer computes a rect
    of its own, which is the whole reason `panel.py` exists.
    """
    scale = SUPERSAMPLE
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    alpha = int(round(_clamp01(frame.pill_alpha) * 255))
    placed = panel.layout(
        float(width), float(height), frame.radius, frame.open,
        flowbar.STATUS_TEXT.get(frame.state, ""), frame.strip,
    )
    radius = frame.radius * scale

    # The pill's single fill fades out as the panel's two bands fade in, so
    # the shape is never momentarily both and never momentarily neither --
    # matching flowbar_mac's drawRect_ exactly.
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
        # punching the corners out with a rounded-rectangle mask. AppKit gets
        # this for free from a clip on the graphics context; Pillow has no
        # such thing, so the mask does the same job after the fact.
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

    # Unconditional, matching flowbar_mac's drawRect_: the border strokes the
    # pill at rest just as it strokes the open panel, so it cannot vanish for
    # every closed-panel frame -- which is most of them, since `open` defaults
    # to 0.0 and every resting-pill frame lives at or near it. Uses `alpha`
    # (pill_alpha), not `band_alpha`: the border's own opacity, not the
    # gradient bands' faded-in one.
    draw.rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=radius, outline=panel.PILL_BORDER_RGB + (alpha,),
        width=max(1, int(panel.PILL_BORDER_WIDTH * scale)),
    )

    if frame.dot is not None:
        # The state dot on the compact indicator. Rides `alpha` (pill_alpha),
        # not the bar alpha, so it fades with the pill it sits in rather than
        # outliving it -- identical to flowbar_mac's `_draw_dot`.
        d = panel.compact_dot(float(width), float(height))
        draw.ellipse(
            (d.x * scale, d.y * scale, d.right * scale, d.bottom * scale),
            fill=frame.dot + (alpha,),
        )

    bar_alpha = int(round(_clamp01(frame.bar_alpha) * 255))
    if bar_alpha > 2 and placed.band.h > 0:
        band = placed.band
        if frame.dot is not None:
            # The dot owns the left end; the trace gets what is left. From
            # `panel`, so both renderers put the bars in the same place.
            band = panel.compact_trace(band.w, band.h)
        centre_y = (band.y + band.h / 2.0) * scale
        # `panel.PEAK_FRACTION` is 69% of band height at peak, mirrored -- so
        # the tallest bar's half is half of that. See panel.py for why this
        # is not a bare float here. Taken from `placed.band`, not the raw
        # pill height, so the resting pill (whose band *is* the whole pill --
        # see `panel.bands`) and the open panel use the identical rule.
        max_half = band.h * panel.PEAK_FRACTION / 2.0 * scale
        bar_width = wf.BAR_WIDTH * scale
        for x, normalised in zip(
            wf.bar_layout(band.w * scale, len(frame.heights),
                          bar_width, wf.BAR_GAP * scale),
            frame.heights,
        ):
            half = normalised * max_half
            # Never shorter than it is wide, so the round caps stay circular
            # rather than squashing into ellipses at rest.
            total = max(bar_width, half * 2.0)
            draw.rounded_rectangle(
                (band.x * scale + x - bar_width / 2.0,
                 centre_y - total / 2.0,
                 band.x * scale + x + bar_width / 2.0,
                 centre_y + total / 2.0),
                radius=bar_width / 2.0,
                fill=panel.BAR_RGB + (bar_alpha,),
            )

    # Same test `_draw_strip` uses internally to decide whether to draw at
    # all, so this guard and the one inside it cannot disagree about whether
    # the strip is visible this frame -- mirroring flowbar_mac's drawRect_.
    if panel.strip_alpha(placed.strip.h) > 0.0:
        _draw_strip(draw, placed, frame, scale)

    # `_mirrored` forces symmetry about the horizontal centre line, which is
    # right for a pill and actively wrong for a panel: the two bands differ by
    # design, and mirroring would paint the waveform band over the strip.
    if frame.open < 0.001:
        image = _mirrored(image)
    return image.resize((width, height), Image.LANCZOS)


def _draw_strip(draw, placed, frame, scale) -> None:
    """The dot, the state word, and each control beside its own key cap.

    Alpha rides the strip's own real height against what its tallest content
    needs -- `panel.strip_alpha` -- not `frame.open` directly. A part-grown
    strip can be shorter than the 20pt key cap it holds, and at any alpha that
    cap still clips against the panel's rounded corner; waiting until the
    strip can actually hold its contents is what `strip_alpha` buys instead.
    Exactly `flowbar_mac._PillView._draw_strip`'s reasoning, so both
    renderers use the identical ramp.
    """
    alpha = int(round(panel.strip_alpha(placed.strip.h) * 255))
    if alpha <= 0:
        return

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


def _mirrored(image: Image.Image) -> Image.Image:
    """Force exact symmetry about the horizontal centre line.

    The bars mirror about that line by design, but a shape straddling the
    centre of an even-height image has its two ends rounded independently by
    Pillow, so the drawn result comes out a row taller on one side. One row is
    invisible in isolation and unmistakable once the pill is animating.

    `tray_icon` needs the identical fix for the identical reason.
    """
    half = image.height // 2
    top = image.crop((0, 0, image.width, half))
    out = image.copy()
    out.paste(top.transpose(Image.FLIP_TOP_BOTTOM), (0, image.height - half))
    return out


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def premultiplied_bgra(image: Image.Image) -> bytes:
    """RGBA -> the premultiplied, bottom-up BGRA that UpdateLayeredWindow wants.

    Three separate traps, and each produces a different wrong picture:

    * **BGRA, not RGBA.** Get it wrong and the pill renders with red and blue
      swapped -- which on a black-and-cream design is nearly invisible, so it
      would ship.
    * **Premultiplied.** ``AC_SRC_ALPHA`` means Windows expects each colour
      already scaled by its alpha. Skip it and every antialiased edge picks up
      a bright halo.
    * **Bottom-up.** A DIB with positive height is stored bottom row first, so
      the image arrives vertically flipped. The pill is symmetric about its
      centre line, so this one hides too.
    """
    array = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    alpha = array[:, :, 3].astype(np.uint16)
    red = (array[:, :, 0].astype(np.uint16) * alpha // 255).astype(np.uint8)
    green = (array[:, :, 1].astype(np.uint16) * alpha // 255).astype(np.uint8)
    blue = (array[:, :, 2].astype(np.uint16) * alpha // 255).astype(np.uint8)
    bgra = np.dstack([blue, green, red, array[:, :, 3]])
    return np.flipud(bgra).tobytes()


class FlowBar:
    """The layered window, its own thread, and where on screen it sits."""

    def __init__(
        self, indicator, position: str = "bottom-centre", fps: int = FPS,
        point=None, on_click=None,
    ) -> None:
        self._indicator = indicator
        self._position = position if position in POSITIONS else "bottom-centre"
        self._fps = fps
        #: (centre_x, bottom_y) once dragged, else None to use `position`.
        self._point = list(point) if point else None
        #: Called with a strip item's id on click. Task 8 supplies it; this
        #: task only builds the channel, so None (the default) is a no-op.
        self._on_click = on_click
        self._movable = False
        self._hwnd = None
        self._width = int(wf.PILL_WIDTH)
        #: Whether the window is currently shown. Tracked separately from
        #: `Frame.visible` so `_draw` only calls `ShowWindow` on an actual
        #: transition, not every frame.
        self._shown = False
        self._stop = threading.Event()
        self._thread = None
        self._wndproc = None      # must outlive the window or Windows calls freed memory
        #: What was last drawn, for hit-testing the *next* poll's cursor
        #: sample against, and what the window procedure reads to dispatch a
        #: click. One frame stale, which at 60fps nobody can see.
        self._last_layout = None
        self._last_origin = (0.0, 0.0)
        self._hover = ""
        #: Whether the cursor was inside the panel as of the last draw that
        #: changed it -- so WS_EX_TRANSPARENT is only touched on a transition.
        self._interactive = False

    def open(self) -> None:
        """Start the render thread. Returns as soon as it is running."""
        self._thread = threading.Thread(
            target=self._run, name="vocal-advantage-flowbar", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # --- everything below runs on the render thread only --------------------

    def _run(self) -> None:
        try:
            self._create_window()
        except Exception as error:  # noqa: BLE001 - decoration must not be fatal
            warn(f"The Flow Bar could not be created, so there is no overlay: {error}")
            return

        interval = 1.0 / self._fps
        message = wintypes.MSG()
        next_frame = 0.0

        while not self._stop.is_set():
            # Pump, or Windows decides the window is hung. Layered windows are
            # not painted through WM_PAINT, so this loop is only housekeeping.
            while _user32.PeekMessageW(
                ctypes.byref(message), None, 0, 0, PM_REMOVE
            ):
                if message.message == WM_QUIT:
                    self._stop.set()
                    break
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))

            now = time.monotonic()
            if now >= next_frame:
                next_frame = now + interval
                try:
                    self._draw()
                except Exception as error:  # noqa: BLE001
                    warn(f"The Flow Bar stopped drawing: {error}")
                    break
            time.sleep(0.002)

        self._destroy_window()

    def _create_window(self) -> None:
        instance = ctypes.windll.kernel32.GetModuleHandleW(None)

        # Held on the instance: ctypes will garbage-collect the trampoline
        # otherwise, and Windows would call into freed memory on the first
        # message. A classic, and it crashes rather than misbehaves.
        self._wndproc = WNDPROC(_default_wndproc)

        cls = WNDCLASSEXW()
        cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = instance
        cls.lpszClassName = "VocalAdvantageFlowBar"
        # Re-registering the same class name fails with ERROR_CLASS_ALREADY_EXISTS,
        # which is fine and expected if the bar is ever reopened in one process.
        _user32.RegisterClassExW(ctypes.byref(cls))

        x, y = self._origin(self._width, int(wf.PILL_HEIGHT))

        self._hwnd = _user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            | WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
            "VocalAdvantageFlowBar",
            "Vocal Advantage",
            WS_POPUP,
            x, y, self._width, int(wf.PILL_HEIGHT),
            None, None, instance, None,
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        # So the shared window procedure can find this instance again.
        _WINDOWS[int(self._hwnd)] = self

        # Not shown here, and not unconditionally: nothing is on screen at
        # idle now, so whether the window is shown at all is decided every
        # draw from `frame.visible`, in `_draw` below -- which always uses
        # SW_SHOWNOACTIVATE, never SW_SHOW, for the same reason this used to
        # be here: the overlay must not take focus even once, or the first
        # paste of the session lands in our own process.

    def _draw(self) -> None:
        point = POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        hover = self._hover_for(float(point.x), float(point.y))
        # `and self._shown`: a hidden window's `_last_layout`/`_last_origin`
        # are stale geometry from wherever it last drew, and the cursor can
        # easily be sitting inside that stale rect while the window is off
        # screen and idle. Without this, that would drop click-through on a
        # window that is not there to click -- harmless to the user, who has
        # nothing to click, but it would leave `_interactive` wrong for the
        # moment the window is next shown.
        inside = self._contains(float(point.x), float(point.y)) and self._shown
        if inside != self._interactive:
            self._interactive = inside
            # Move bar mode owns this bit while it is on; never fight it.
            if not self.movable:
                style = _user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
                style = (
                    (style & ~WS_EX_TRANSPARENT) if inside
                    else (style | WS_EX_TRANSPARENT)
                )
                _user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, style)
        self._hover = hover

        frame = self._indicator.next_frame(hover=hover)
        width = int(round(frame.width))
        height = int(round(frame.height))
        if width != self._width:
            self._width = width
            self._reposition(width, height)

        image = render_frame(frame, width, height)
        bits = premultiplied_bgra(image)

        screen_dc = _user32.GetDC(None)
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)

        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = height          # positive: bottom-up, hence the flip
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB
        info = BITMAPINFO()
        info.bmiHeader = header

        pixels = ctypes.c_void_p()
        bitmap = _gdi32.CreateDIBSection(
            screen_dc, ctypes.byref(info), DIB_RGB_COLORS,
            ctypes.byref(pixels), None, 0,
        )
        old = _gdi32.SelectObject(mem_dc, bitmap)
        ctypes.memmove(pixels, bits, len(bits))

        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        source = POINT(0, 0)
        size = SIZE(width, height)
        _user32.UpdateLayeredWindow(
            self._hwnd, screen_dc, None, ctypes.byref(size),
            mem_dc, ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA,
        )

        # Every one of these leaks a GDI object per frame if missed. At 60fps
        # that is 3600 handles a minute and Windows caps a process at 10,000 --
        # the app would die after a few minutes, which is a horrible bug to
        # find later.
        _gdi32.SelectObject(mem_dc, old)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(None, screen_dc)

        if frame.visible != self._shown:
            self._shown = frame.visible
            # SW_SHOWNOACTIVATE, never SW_SHOW -- see `_create_window`.
            _user32.ShowWindow(
                self._hwnd, SW_SHOWNOACTIVATE if self._shown else SW_HIDE
            )

        # Recorded for the *next* draw's hit-test: `_hover_for`/`_contains`
        # must be pure, so they read this rather than the frame just drawn.
        self._last_origin = self._origin(width, height)
        self._last_layout = panel.layout(
            float(width), float(height), frame.radius, frame.open,
            flowbar.STATUS_TEXT.get(frame.state, ""), frame.strip,
        )

    def _hover_for(self, screen_x: float, screen_y: float) -> str:
        """Which strip item the cursor is over, in screen coordinates.

        Pure, so it is testable without a window or a cursor. Windows screen
        coordinates are already top-left origin, so -- unlike the macOS twin
        of this method -- no y-flip is needed.
        """
        placed = self._last_layout
        if placed is None:
            return ""
        origin_x, origin_y = self._last_origin
        return panel.hit_test(
            placed, screen_x - origin_x, screen_y - origin_y
        ) or ""

    def _contains(self, screen_x: float, screen_y: float) -> bool:
        """Whether a screen point falls inside the last drawn panel, AND the
        panel actually has something in it to click.

        This is what decides click-through, not `_hover_for`: the window
        should stop ignoring clicks as soon as the cursor is anywhere over
        it, not only over a strip item -- but only in a state that has
        controls at all. Without the `placed.items` check, the resting pill
        and the TRANSCRIBING panel (which opens but, by design, offers zero
        controls) both ate every click that crossed them, including ones
        meant for whatever sits underneath -- the taskbar, at the resting
        pill's position.

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

    def _origin(self, width: int, height: int):
        """Where the pill goes: a dragged point if there is one, else a preset."""
        screen_w = _user32.GetSystemMetrics(SM_CXSCREEN)
        screen_h = _user32.GetSystemMetrics(SM_CYSCREEN)
        if self._point is not None:
            return point_origin(self._point, width, height, screen_w, screen_h)
        return pill_origin(self._position, width, height, screen_w, screen_h)

    # -- move mode ----------------------------------------------------------

    @property
    def movable(self) -> bool:
        return self._movable

    def set_movable(self, movable: bool) -> None:
        """Move mode and click-through are one setting, not two.

        A window carrying WS_EX_TRANSPARENT never receives the mouse-down that
        would start a drag, so the bit has to come off for the duration. That
        is why this is an explicit menu item rather than something always on:
        while it is set, the pill really does intercept clicks.
        """
        self._movable = bool(movable)
        # So idle stays visible while there is something to drag -- see
        # `flowbar.Indicator.set_movable`.
        self._indicator.set_movable(self._movable)
        if not self._hwnd:
            return
        ex = _user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
        if self._movable:
            ex &= ~WS_EX_TRANSPARENT
        else:
            ex |= WS_EX_TRANSPARENT
        _user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, ex)

    def current_point(self):
        """Where the pill is now, as (centre_x, bottom_y), or None."""
        if not self._hwnd:
            return None
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(self._hwnd, ctypes.byref(rect)):
            return None
        return [(rect.left + rect.right) / 2.0, float(rect.bottom)]

    def _reposition(self, width: int, height: int) -> None:
        """Re-anchor as the pill widens for a message, so it does not drift.

        Deliberately missing the flag that would force the window on screen:
        that would fight the `ShowWindow` call in `_draw`, the sole authority
        on visibility, popping a hidden idle window back up the moment its
        size next changed by half a pixel. A resize is a resize, not a vote
        to be seen.
        """
        x, y = self._origin(width, height)
        _user32.SetWindowPos(
            self._hwnd, HWND_TOPMOST, x, y, width, height, SWP_NOACTIVATE,
        )

    def _destroy_window(self) -> None:
        if self._hwnd:
            _WINDOWS.pop(int(self._hwnd), None)
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = None


#: hwnd -> FlowBar. The window procedure is one module-level function shared by
#: every window, so it has no `self`; this is how a message finds its bar.
_WINDOWS: dict = {}


def _default_wndproc(hwnd, message, wparam, lparam):  # pragma: no cover - Windows
    if message == WM_LBUTTONDOWN:
        bar = _WINDOWS.get(int(hwnd) if hwnd else 0)
        if bar is not None:
            # A click on a hovered strip item takes priority and never starts
            # a drag. Task 8 wires the callback; until then `_on_click` is
            # None and this is a no-op, exactly like move mode being off.
            hover = getattr(bar, "_hover", "")
            callback = getattr(bar, "_on_click", None)
            if hover and callback is not None:
                callback(hover)
                return 0
            if bar.movable:
                # Hand the drag to Windows rather than tracking the mouse
                # ourselves: it runs its own modal move loop, so there is no
                # capture to leak, no timer, and it behaves like every other
                # window on the machine -- including snapping and Esc to
                # cancel.
                _user32.ReleaseCapture()
                _user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
                return 0
    return _user32.DefWindowProcW(hwnd, message, wparam, lparam)
