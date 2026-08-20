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
from PIL import Image, ImageDraw

from vocal_advantage import waveform as wf
from vocal_advantage.console import warn

# --- Win32 constants --------------------------------------------------------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020      # the click-through bit
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

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

SM_CXSCREEN = 0
SM_CYSCREEN = 1

PROCESS_PER_MONITOR_DPI_AWARE = 2
_E_ACCESSDENIED = -2147024891

FPS = 60
SIDE_MARGIN = 24
MESSAGE_FONT_SIZE = 11

#: Matches flowbar_mac's palette exactly. If these two ever drift the pill will
#: look like a different app on the other machine.
PILL_FILL_RGB = (247, 246, 241)
BAR_RGB = (0, 0, 0)
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

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long, wintypes.HWND, wintypes.UINT,
        wintypes.WPARAM, wintypes.LPARAM,
    )

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
    WNDPROC = WNDCLASSEXW = None


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


def pill_origin(position: str, width: float, screen_width: int, screen_height: int):
    """Top-left corner of the pill, in Windows screen coordinates (y is down).

    The macOS twin measures from the bottom up, because that is how AppKit's
    coordinates run. Same result on screen, opposite arithmetic -- which is
    exactly the sort of thing that silently puts the bar off the top of one
    machine, hence a test for each.
    """
    if position == "bottom-left":
        x = SIDE_MARGIN
    elif position == "bottom-right":
        x = screen_width - width - SIDE_MARGIN
    else:
        x = (screen_width - width) / 2.0
    return int(round(x)), int(round(screen_height - wf.SCREEN_MARGIN - wf.PILL_HEIGHT))


def render_frame(frame, width: int, height: int) -> Image.Image:
    """One `flowbar.Frame` as an RGBA image. Pure: no Win32, no window.

    Kept importable and callable on any platform on purpose -- it is the half of
    this file that can be looked at from the Mac, by saving the result to a PNG.
    """
    scale = SUPERSAMPLE
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    alpha = int(round(_clamp01(frame.pill_alpha) * 255))
    radius = height * scale / 2.0      # fully rounded ends

    # Fill only, no outline, on the full bounds -- matching flowbar_mac. The
    # edge of the fill is what defines the shape now.
    draw.rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=radius,
        fill=PILL_FILL_RGB + (alpha,),
    )

    bar_alpha = int(round(_clamp01(frame.bar_alpha) * 255))
    if bar_alpha > 2:
        centre_y = height * scale / 2.0
        max_half = (height / 2.0 - wf.BAR_MARGIN_Y) * scale
        bar_width = wf.BAR_WIDTH * scale
        for x, normalised in zip(
            wf.bar_layout(width * scale, len(frame.heights), bar_width,
                          wf.BAR_GAP * scale),
            frame.heights,
        ):
            half = normalised * max_half
            # Never shorter than it is wide, so the round caps stay circular
            # rather than squashing into ellipses at rest.
            total = max(bar_width, half * 2.0)
            draw.rounded_rectangle(
                (x - bar_width / 2.0, centre_y - total / 2.0,
                 x + bar_width / 2.0, centre_y + total / 2.0),
                radius=bar_width / 2.0,
                fill=BAR_RGB + (bar_alpha,),
            )

    return _mirrored(image).resize((width, height), Image.LANCZOS)


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
        self, indicator, position: str = "bottom-centre", fps: int = FPS
    ) -> None:
        self._indicator = indicator
        self._position = position if position in POSITIONS else "bottom-centre"
        self._fps = fps
        self._hwnd = None
        self._width = int(wf.PILL_WIDTH)
        self._stop = threading.Event()
        self._thread = None
        self._wndproc = None      # must outlive the window or Windows calls freed memory

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

        screen_w = _user32.GetSystemMetrics(SM_CXSCREEN)
        screen_h = _user32.GetSystemMetrics(SM_CYSCREEN)
        x, y = pill_origin(self._position, self._width, screen_w, screen_h)

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

        # ShowWindow with SW_SHOWNOACTIVATE, never SW_SHOW: the overlay must
        # not take focus even once, or the first paste of the session lands in
        # our own process.
        _user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)

    def _draw(self) -> None:
        frame = self._indicator.next_frame()
        width = int(round(frame.width))
        height = int(wf.PILL_HEIGHT)
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

    def _reposition(self, width: int, height: int) -> None:
        """Re-anchor as the pill widens for a message, so it does not drift."""
        screen_w = _user32.GetSystemMetrics(SM_CXSCREEN)
        screen_h = _user32.GetSystemMetrics(SM_CYSCREEN)
        x, y = pill_origin(self._position, width, screen_w, screen_h)
        _user32.SetWindowPos(
            self._hwnd, HWND_TOPMOST, x, y, width, height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _destroy_window(self) -> None:
        if self._hwnd:
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = None


def _default_wndproc(hwnd, message, wparam, lparam):  # pragma: no cover - Windows
    return _user32.DefWindowProcW(hwnd, message, wparam, lparam)
