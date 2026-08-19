"""The recording pill: a small always-on-top overlay that must never take focus.

This app pastes text into whatever window has focus. If the pill ever takes
focus, the paste lands in our own process instead of the user's editor and the
dictation is lost - silently, with the pill still looking perfect. So the two
focus guards below are not style points, they are the feature.

The module has two halves:

* `Indicator` - portable. A thread-safe queue plus a tick-driven state machine.
  Every public method only enqueues; `pump()` runs on the tkinter thread every
  50ms, drains the queue, and draws at most one frame.
* `_TkPill` - Windows. One tkinter Toplevel created at startup and never
  destroyed, carrying WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, shown and hidden
  only with SetWindowPos + SWP_NOACTIVATE.

Everything that needs Windows - `ctypes.wintypes`, `user32`, `shcore` - is
created inside the `sys.platform == "win32"` guard below, and `tkinter` is
imported in a try/except, so this file imports on any platform. That is what
lets tests/test_indicator_win.py drive the whole state machine headlessly.
"""
from __future__ import annotations

import ctypes
import queue
import sys
from dataclasses import dataclass

try:  # stdlib on the Windows Python this app targets
    import tkinter as tk
except ImportError:  # pragma: no cover - the portable half still imports
    tk = None


# --- timing -----------------------------------------------------------------
# SPEC.md fixes the pump interval (root.after(50, ...)) but only says "brief
# flash" and "animated dots", so the two below are chosen: 1.5s is long enough
# to read two words without being in the way, 350ms is a calm dot cycle.
PUMP_INTERVAL_MS = 50
FLASH_TICKS = 30                  # 30 * 50ms = 1.5s
PROCESSING_TICKS_PER_FRAME = 7    # 7 * 50ms = 350ms
PROCESSING_FRAMES = (".", "..", "...")


# --- Win32 constants --------------------------------------------------------
GWL_EXSTYLE = -20                 # index of the extended-style word
WS_EX_TOOLWINDOW = 0x00000080     # no taskbar button, no Alt+Tab entry
WS_EX_NOACTIVATE = 0x08000000     # Windows must never activate this window

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020         # required for a style change to take effect
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080

PROCESS_PER_MONITOR_DPI_AWARE = 2
_E_ACCESSDENIED = -2147024891     # 0x80070005: DPI awareness already set


if sys.platform == "win32":
    # Imported here, not at module top: ctypes.wintypes raises off Windows, and
    # every use of it is inside this block.
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shcore = ctypes.WinDLL("shcore", use_last_error=True)

    HWND_TOPMOST = wintypes.HWND(-1)

    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND

    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,   # hWnd
        wintypes.HWND,   # hWndInsertAfter
        ctypes.c_int,    # X
        ctypes.c_int,    # Y
        ctypes.c_int,    # cx
        ctypes.c_int,    # cy
        wintypes.UINT,   # uFlags
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL

    # On 64-bit Windows the style word is 64 bits wide and only the *Ptr*
    # variants are exported; on 32-bit only the plain ones exist (there, the
    # Ptr names are just C macros and would raise AttributeError here).
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _get_window_long = _user32.GetWindowLongPtrW
        _set_window_long = _user32.SetWindowLongPtrW
    else:  # pragma: no cover - 32-bit Python is not a target, but be correct
        _get_window_long = _user32.GetWindowLongW
        _set_window_long = _user32.SetWindowLongW

    _get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_window_long.restype = ctypes.c_ssize_t   # LONG_PTR
    _set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _set_window_long.restype = ctypes.c_ssize_t

    _shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    _shcore.SetProcessDpiAwareness.restype = ctypes.c_long  # HRESULT
else:  # pragma: no cover - lets the portable half import for the headless tests
    _user32 = None
    _shcore = None
    _get_window_long = None
    _set_window_long = None
    HWND_TOPMOST = None


def set_dpi_awareness() -> None:
    """Tell Windows we do our own scaling. Call once, before the first window.

    THIS MODULE NEVER CALLS IT FOR YOU. It must be called by the process
    entry point - Task 10's `run_app` - as the first statement of its
    tkinter section, before `tk.Tk()` exists. `tools/indicator_demo.py` shows
    the same ordering. Windows only honours process DPI awareness if it is set
    before the process creates its first window; called afterwards it is a
    silent no-op that answers E_ACCESSDENIED.

    Without it Windows bitmap-stretches the pill (blurry text) and our
    "bottom centre of the screen" pixel maths is wrong on any display above
    100% scaling. Calling it twice is harmless: Windows answers E_ACCESSDENIED
    the second time and we ignore that.
    """
    if _shcore is None:  # pragma: no cover - not Windows
        return
    hresult = _shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
    if hresult not in (0, _E_ACCESSDENIED):  # pragma: no cover - cosmetic only
        print(
            f"[indicator] SetProcessDpiAwareness failed "
            f"(0x{hresult & 0xFFFFFFFF:08X}); the pill may look blurry",
            file=sys.stderr,
        )


def _real_hwnd(widget) -> int:
    """The HWND Windows actually owns for a tkinter toplevel.

    `winfo_id()` returns tk's *inner* child window; the window that decides
    activation is its parent. Getting this wrong fails silently - the style bits
    land on a window that has no say over focus.
    """
    child = int(widget.winfo_id())
    parent = _user32.GetParent(child)
    return int(parent) if parent else child


def _apply_no_activate(hwnd: int) -> None:
    """Add the two focus-proofing style bits to an existing window."""
    # OR rather than assign: tk has already set WS_EX_LAYERED here for -alpha,
    # and clobbering it would make the window paint as a black rectangle.
    ex_style = _get_window_long(hwnd, GWL_EXSTYLE)
    _set_window_long(
        hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    )
    # An extended-style change is not live until the frame is recalculated.
    _user32.SetWindowPos(
        hwnd, None, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


@dataclass(frozen=True)
class Frame:
    """Everything the pill needs to draw itself, for one moment in time."""

    visible: bool
    dot: bool
    text: str


class _TkPill:
    """The actual window. One per process, built at startup, never destroyed."""

    HEIGHT = 34
    MIN_WIDTH = 96
    BOTTOM_MARGIN = 72          # pixels of clearance above the taskbar
    BACKGROUND = "#141414"
    DOT_COLOUR = "#e33b3b"
    TEXT_COLOUR = "#f0f0f0"
    DOT_RADIUS = 6

    def __init__(self, root) -> None:
        # Deliberately NOT calling set_dpi_awareness() here. By the time this
        # runs, tk.Tk() has already created a window and Windows has already
        # fixed this process's DPI awareness, so the call would silently do
        # nothing while looking like it covered us. The entry point owns it.
        self._win = win = tk.Toplevel(root)
        win.overrideredirect(True)          # no title bar, no border
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)       # invisible for the one map below
        win.configure(bg=self.BACKGROUND)
        win.geometry(f"{self.MIN_WIDTH}x{self.HEIGHT}+0+0")

        self._canvas = tk.Canvas(
            win, width=self.MIN_WIDTH, height=self.HEIGHT,
            bg=self.BACKGROUND, highlightthickness=0, bd=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._dot = self._canvas.create_oval(
            0, 0, 0, 0, fill=self.DOT_COLOUR, outline="", state="hidden"
        )
        self._text = self._canvas.create_text(
            0, 0, text="", fill=self.TEXT_COLOUR,
            font=("Segoe UI", 11), state="hidden",
        )

        # A tk window has no real HWND until it is mapped once, and we cannot
        # set the no-activate bit before there is an HWND to set it on. So map
        # it here, at startup, when nothing is being pasted into - "-alpha 0.0"
        # keeps that one map invisible. From this point on the window stays
        # mapped as far as tk is concerned and we show/hide it behind tk's back
        # with SetWindowPos, which is the only way to do it without activating.
        win.update_idletasks()
        self._hwnd = _real_hwnd(win)
        _apply_no_activate(self._hwnd)
        self._hide()
        win.attributes("-alpha", 1.0)

    def render(self, frame: Frame) -> None:
        if not frame.visible:
            self._hide()
            return
        width = max(self.MIN_WIDTH, 28 + 10 * len(frame.text))
        self._place(width)
        centre_x, centre_y = width / 2, self.HEIGHT / 2
        self._canvas.itemconfigure(
            self._dot, state="normal" if frame.dot else "hidden"
        )
        self._canvas.coords(
            self._dot,
            centre_x - self.DOT_RADIUS, centre_y - self.DOT_RADIUS,
            centre_x + self.DOT_RADIUS, centre_y + self.DOT_RADIUS,
        )
        self._canvas.itemconfigure(
            self._text, text=frame.text,
            state="normal" if frame.text else "hidden",
        )
        self._canvas.coords(self._text, centre_x, centre_y)
        self._show()

    def _place(self, width: int) -> None:
        screen_w = self._win.winfo_screenwidth()
        screen_h = self._win.winfo_screenheight()
        x = (screen_w - width) // 2
        y = screen_h - self.HEIGHT - self.BOTTOM_MARGIN
        # Move and resize through tk, not SetWindowPos: tk owns an inner child
        # window that has to be resized in step with the outer one, and only tk
        # knows about it. Moving a window does not make it visible, so this is
        # safe to call while the window is hidden.
        self._win.geometry(f"{width}x{self.HEIGHT}+{x}+{y}")
        self._canvas.configure(width=width, height=self.HEIGHT)

    def _show(self) -> None:
        # The single most important line in this file. SWP_SHOWWINDOW with
        # SWP_NOACTIVATE maps the window without giving it focus; tkinter's
        # deiconify() would activate it, and the next paste would go into our
        # own process instead of the user's editor. Passing HWND_TOPMOST here
        # (instead of SWP_NOZORDER) re-asserts always-on-top on every show,
        # which other topmost windows can otherwise knock us off.
        _user32.SetWindowPos(
            self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _hide(self) -> None:
        _user32.SetWindowPos(
            self._hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_HIDEWINDOW,
        )


_HIDDEN = "hidden"
_RECORDING = "recording"
_PROCESSING = "processing"
_FLASH = "flash"


class Indicator:
    """Thread-safe front end to the pill.

    Any thread may call show_recording / show_processing / hide / flash; those
    only put a command on a queue. `pump()` runs on the tkinter thread, drains
    the queue and draws at most one frame. tkinter fails in hard-to-debug ways
    when touched from another thread, and in this app it is the controller
    thread that knows when recording starts and stops - hence the queue.

    Time is counted in pumps rather than read off a clock. pump() is scheduled
    every PUMP_INTERVAL_MS, and while transcription runs (on the controller
    thread) the tk thread is idle, so ticks are an accurate stopwatch. It also
    means the state machine is deterministic with no clock to inject.
    """

    def __init__(self, root) -> None:
        self._root = root
        self._commands: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._mode = _HIDDEN
        self._text = ""
        self._ticks = 0
        self._pill = None
        self._last_frame = None

    # --- callable from any thread ------------------------------------------
    def show_recording(self) -> None:
        self._commands.put((_RECORDING, ""))

    def show_processing(self) -> None:
        self._commands.put((_PROCESSING, ""))

    def hide(self) -> None:
        self._commands.put((_HIDDEN, ""))

    def flash(self, message: str) -> None:
        """Show a short message, e.g. "nothing heard", then hide automatically.

        Callers must NOT follow this with hide() - that would cancel the flash
        before it is readable. The flash times itself out after FLASH_TICKS.
        """
        self._commands.put((_FLASH, message))

    # --- tkinter thread only -----------------------------------------------
    def pump(self) -> None:
        """Drain the queue and draw. Kick off once with root.after(50, pump)."""
        if self._pill is None:
            # Built here so it happens on the tk thread, and kept for the life
            # of the process: creating a window per dictation is what makes
            # overlays flicker and, worse, steal focus each time they appear.
            self._pill = self._new_pill()

        while True:
            try:
                self._mode, self._text = self._commands.get_nowait()
            except queue.Empty:
                break
            self._ticks = 0

        if self._mode == _FLASH and self._ticks >= FLASH_TICKS:
            self._mode, self._text = _HIDDEN, ""

        frame = self._frame()
        if frame != self._last_frame:
            self._pill.render(frame)
            self._last_frame = frame

        self._ticks += 1
        self._root.after(PUMP_INTERVAL_MS, self.pump)

    # --- the test seam ------------------------------------------------------
    def _new_pill(self):
        """Build the real window. Overridden in tests to run headless."""
        return _TkPill(self._root)

    def _frame(self) -> Frame:
        if self._mode == _RECORDING:
            return Frame(visible=True, dot=True, text="")
        if self._mode == _PROCESSING:
            step = (self._ticks // PROCESSING_TICKS_PER_FRAME) % len(PROCESSING_FRAMES)
            return Frame(visible=True, dot=False, text=PROCESSING_FRAMES[step])
        if self._mode == _FLASH:
            return Frame(visible=True, dot=False, text=self._text)
        return Frame(visible=False, dot=False, text="")
