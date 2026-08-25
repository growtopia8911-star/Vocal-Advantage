"""Clipboard + synthetic Ctrl+V text injection (Windows).

Why not type the transcript out character by character: synthetic typing drops
characters in Windows Terminal (microsoft/terminal#12977) and produces the
wrong glyphs on non-US keyboard layouts. Clipboard + Ctrl+V is layout-
independent and lands as one atomic edit.

The order of operations and every delay below come from SPEC.md "Paste
sequence". The numbers were tuned by espanso and LocalFlow against real apps;
they are not guesses. Do not reorder or shrink them without re-testing in
Windows Terminal, VS Code and a browser text box.

Only Win32Backend touches Windows. Everything above it is plain Python, so the
sequencing is testable with a fake and this module imports on any OS.
"""

from __future__ import annotations

import ctypes
import time

# Re-exported, not redefined: the sequence and its delays are identical on both
# platforms and live in paste_core, which also owns the ONE injection flag the
# key hook watches. Existing callers import these names from here.
from vocal_advantage.paste_core import (  # noqa: F401
    CLIPBOARD_ATTEMPTS,
    CLIPBOARD_RETRY_S,
    CLIPBOARD_SETTLE_S,
    KEY_INTERVAL_S,
    MODIFIER_POLL_S,
    MODIFIER_WAIT_S,
    POST_PASTE_S,
    PasteBackend,
    injection_active,
    paste_with,
)

# --- Win32 constants --------------------------------------------------------
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MAPVK_VK_TO_VSC = 0

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LCONTROL = 0xA2
VK_V = 0x56

# VK_CONTROL/VK_SHIFT/VK_MENU are the "either side" virtual keys, so this
# covers left and right variants; the Win keys have no combined VK.
MODIFIER_VKS = (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)

# Registered (not predefined) clipboard formats that ask Windows to keep this
# clipboard entry out of Win+V history and out of cloud sync. Privacy is the
# product, so a dictation must not end up in either.
PRIVACY_FORMATS = (
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
)

# Each privacy format takes a 4-byte DWORD payload of 0. For the two
# "Can..." formats 0 literally means "no"; for the Exclude format the value is
# ignored and merely being present is what counts.
DWORD_ZERO = (0).to_bytes(4, "little")

# --- ctypes structures ------------------------------------------------------
# Fixed-width aliases rather than ctypes.wintypes: wintypes cannot be imported
# off Windows, and c_ulong is 8 bytes on 64-bit Linux, which would silently
# change these layouts. These give the exact Windows sizes everywhere.
_LONG = ctypes.c_int32
_DWORD = ctypes.c_uint32
_WORD = ctypes.c_uint16
_ULONG_PTR = ctypes.c_size_t  # pointer-sized unsigned int, as ULONG_PTR is


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", _LONG),
        ("dy", _LONG),
        ("mouseData", _DWORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", _WORD),
        ("wScan", _WORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", _DWORD),
        ("wParamL", _WORD),
        ("wParamH", _WORD),
    ]


class _INPUTUNION(ctypes.Union):
    # MOUSEINPUT is the largest member; it is what fixes sizeof(INPUT) at 40 on
    # x64. Declaring only `ki` would give a 32-byte INPUT and SendInput would
    # reject every event.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    # _anonymous_ must be set before _fields_; it is what makes `inp.ki` work.
    _anonymous_ = ("u",)
    _fields_ = [("type", _DWORD), ("u", _INPUTUNION)]


# --- the Windows layer ------------------------------------------------------
_USER32 = None
_KERNEL32 = None


def _win_error(message: str) -> OSError:
    """Wrap the current Win32 last-error in an OSError the caller can retry.

    ``ctypes.get_last_error`` is Windows-only. Reaching for it unguarded makes
    every failure path here raise AttributeError instead of the OSError the
    retry logic is built to catch, which would also make those paths
    untestable off Windows -- and this module's whole point is that everything
    above Win32Backend runs anywhere. On Windows the behaviour is unchanged.
    """
    get_last_error = getattr(ctypes, "get_last_error", None)
    code = get_last_error() if get_last_error is not None else 0
    return OSError(0, f"{message} (WinError {code})", None, code)


def _load_win32():
    """Load user32/kernel32 once and pin every argtype/restype we depend on.

    Pinning is not cosmetic: without it ctypes assumes a 32-bit int for every
    argument and return value, which truncates 64-bit handles and pointers.
    The failures that causes are silent, not loud.
    """
    global _USER32, _KERNEL32
    if _USER32 is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_int
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = ctypes.c_int
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = ctypes.c_int
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
        user32.RegisterClipboardFormatW.restype = ctypes.c_uint
        # Reading the clipboard back, for the save/restore around a paste.
        # GlobalLock/GlobalUnlock are declared with the allocation calls below.
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
        user32.MapVirtualKeyW.restype = ctypes.c_uint
        user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = ctypes.c_uint

        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = ctypes.c_int
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p

        _USER32, _KERNEL32 = user32, kernel32
    return _USER32, _KERNEL32


def _modifiers_down(user32) -> bool:
    """True while any Ctrl/Shift/Alt/Win key is physically held.

    Bit 0x8000 is "down right now"; bit 0x0001 is "pressed since the last
    call" and must be ignored. The value arrives as a signed SHORT, so a held
    key reads as a negative number - masking is mandatory.
    """
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_VKS)


def _alloc_global(kernel32, payload: bytes):
    """GlobalAlloc a movable block and copy payload into it."""
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise _win_error("GlobalAlloc failed")
    address = kernel32.GlobalLock(handle)
    if not address:
        kernel32.GlobalFree(handle)
        raise _win_error("GlobalLock failed")
    ctypes.memmove(address, payload, len(payload))
    kernel32.GlobalUnlock(handle)
    return handle


def _set_format(user32, kernel32, fmt: int, payload: bytes) -> None:
    handle = _alloc_global(kernel32, payload)
    if not user32.SetClipboardData(fmt, handle):
        # Ownership only transfers to the system on success, so a failed call
        # leaves the block ours to free.
        kernel32.GlobalFree(handle)
        raise _win_error(f"SetClipboardData(format {fmt}) failed")


def _set_clipboard(user32, kernel32, text: str) -> None:
    """Put text on the clipboard as CF_UNICODETEXT, marked private.

    Raises OSError on any failure so the caller can retry: OpenClipboard
    genuinely races the Win+V clipboard-history process and returns
    WinError 5 (access denied) while that process holds the clipboard.
    """
    if not user32.OpenClipboard(None):
        raise _win_error("OpenClipboard failed")
    try:
        if not user32.EmptyClipboard():
            raise _win_error("EmptyClipboard failed")
        # CF_UNICODETEXT wants a NUL-terminated UTF-16LE string.
        _set_format(
            user32, kernel32, CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00"
        )
        for name in PRIVACY_FORMATS:
            fmt = user32.RegisterClipboardFormatW(name)
            if fmt == 0:
                raise _win_error(f"RegisterClipboardFormatW({name}) failed")
            _set_format(user32, kernel32, fmt, DWORD_ZERO)
    except OSError:
        # The text may already be on the clipboard but not yet marked private,
        # and history snapshots the session at CloseClipboard. Wipe it rather
        # than leak a dictation into Win+V.
        user32.EmptyClipboard()
        raise
    finally:
        user32.CloseClipboard()


def _get_clipboard(user32, kernel32) -> str | None:
    """The clipboard's current text, or None if there is none to be had.

    None covers every "we do not know" case -- the clipboard is locked by
    another process, or holds an image rather than text. The caller treats None
    as "leave it alone", which is the only safe reading: blanking a clipboard
    whose contents we could not see would destroy data we never had.

    Non-text clipboards are a real loss here. Restoring text-only means a
    copied image does not survive a dictation. Handling every format would mean
    enumerating and round-tripping arbitrary HGLOBALs, which is a great deal of
    fragile code for a rare case; text is what people paste after dictating.
    """
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.c_wchar_p(pointer).value
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:  # noqa: BLE001 - unreadable is a normal outcome
        return None
    finally:
        user32.CloseClipboard()


def _send_key(user32, vk: int, down: bool) -> int:
    """Inject one key event. Returns the number of events actually inserted.

    0 means Windows refused. The usual cause is UIPI: a process at normal
    integrity cannot inject input into a window owned by an elevated process.
    That is documented Windows behaviour, not a bug to fix - the transcript
    stays on the clipboard for a manual Ctrl+V.
    """
    event = INPUT(type=INPUT_KEYBOARD)
    event.ki.wVk = vk
    # Some apps read the scan code rather than the virtual key, so fill both.
    event.ki.wScan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    event.ki.dwFlags = 0 if down else KEYEVENTF_KEYUP
    event.ki.time = 0  # 0 = let Windows timestamp it
    event.ki.dwExtraInfo = 0
    # ctypes.pointer rather than byref: it keeps `event` alive for the call and
    # lets tests read the struct back out.
    return user32.SendInput(1, ctypes.pointer(event), ctypes.sizeof(INPUT))


class Win32Backend:
    """The real implementation of PasteBackend. Windows only."""

    def __init__(self) -> None:
        self._user32, self._kernel32 = _load_win32()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def modifiers_down(self) -> bool:
        return _modifiers_down(self._user32)

    def get_clipboard(self) -> str | None:
        return _get_clipboard(self._user32, self._kernel32)

    def set_clipboard(self, text: str) -> None:
        _set_clipboard(self._user32, self._kernel32, text)

    def send_key(self, vk: int, down: bool) -> int:
        return _send_key(self._user32, vk, down)


# --- the Windows paste chord ------------------------------------------------
CTRL_V_SEQUENCE = (
    # Left Ctrl specifically: Right Ctrl is an extended key and some apps treat
    # the two differently, while Left Ctrl is what every paste handler expects.
    (VK_LCONTROL, True),
    (VK_V, True),
    (VK_V, False),
    (VK_LCONTROL, False),
)

_DEFAULT_BACKEND: PasteBackend | None = None


def _default_backend() -> PasteBackend:
    """Build the real backend on first use, not at import time."""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = Win32Backend()
    return _DEFAULT_BACKEND


def paste_text(text: str, *, backend: PasteBackend | None = None) -> bool:
    """Put text on the clipboard and paste it into the focused window.

    Returns False (never raises) when there is nothing to paste, when the
    clipboard could not be written, or when Windows refused the keystrokes.
    In the last case the text is still on the clipboard for a manual Ctrl+V -
    that is the documented outcome for elevated target windows (UIPI).
    """
    if backend is None:
        backend = _default_backend()
    return paste_with(text, backend, CTRL_V_SEQUENCE)
