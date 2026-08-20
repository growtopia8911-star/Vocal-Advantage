"""The global keyboard hook (Windows).

The portable half of this file - name translation and edge detection - now
lives in ``hotkey_events.py`` so ``hotkey_mac`` can share it. What is left here
is the part that genuinely is Windows:

* ``VK_CODES`` / ``read_pressed_keys`` - GetAsyncKeyState, used to resync the
  held set after a paste.
* ``HotkeyListener`` - installs ``keyboard.hook``, feeds the detector, forwards
  surviving events to ``on_event``.
* ``capture_hotkey`` - the ``--set-hotkey`` capture.

The moved names are re-exported below, so every existing import of
``hotkey_win`` keeps working unchanged.

We never pass ``suppress=True``. Suppression is where the keyboard library's
bugs live (issues #442/#666), and the spec rules it out for v0.1.

This file imports on any platform. Only ``read_pressed_keys`` needs Windows,
and its ``user32`` binding is guarded below.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable, Iterable

from vocal_advantage.hotkey_spec import HotkeySpec, parse_hotkey

# Re-exported, not redefined: one copy of this logic, shared with hotkey_mac.
# Existing callers and tests import these names from here and must keep working.
from vocal_advantage.hotkey_events import (  # noqa: F401
    KEY_DOWN,
    CaptureSession,
    KEY_UP,
    MODIFIER_KEYS,
    Edge,
    EdgeDetector,
    normalise_key_name,
    spec_key_for,
)

# --------------------------------------------------------------------------
# Reading the real keyboard state (used to resync after a paste)
# --------------------------------------------------------------------------

# Guarded, because ``ctypes.WinDLL`` does not exist off Windows -- calling it at
# module scope would make this file unimportable on a Mac. Same shape as the
# guards in ``hotkey_spec`` and ``recorder``. On Windows nothing changes.
if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
else:  # pragma: no cover - the Windows branch is what ships
    _user32 = None

VK_CODES: dict[str, int] = {
    "ctrl": 0x11, "left ctrl": 0xA2, "right ctrl": 0xA3,
    "shift": 0x10, "left shift": 0xA0, "right shift": 0xA1,
    "alt": 0x12, "left alt": 0xA4, "right alt": 0xA5, "alt gr": 0xA5,
    "windows": 0x5B, "win": 0x5B, "left windows": 0x5B, "left win": 0x5B,
    "right windows": 0x5C, "right win": 0x5C,
    "caps lock": 0x14, "num lock": 0x90, "scroll lock": 0x91, "pause": 0x13,
    "esc": 0x1B, "tab": 0x09, "enter": 0x0D, "backspace": 0x08, "space": 0x20,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page up": 0x21, "page down": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "print screen": 0x2C, "menu": 0x5D,
}
VK_CODES.update({f"f{i}": 0x6F + i for i in range(1, 25)})  # F1 is 0x70
VK_CODES.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"})
VK_CODES.update({d: ord(d) for d in "0123456789"})


def read_pressed_keys(names: Iterable[str]) -> frozenset[str]:
    """Of ``names``, which are physically held right now?

    GetAsyncKeyState's high bit means "down". Names we have no virtual-key code
    for are reported as not held - safe, because a stale entry in the held set
    is what breaks the next dictation. Off Windows there is nothing to ask, so
    nothing is held; the resync it feeds is a Windows-only concern anyway.
    """
    if _user32 is None:
        return frozenset()
    pressed = set()
    for name in names:
        vk = VK_CODES.get(name)
        if vk is not None and _user32.GetAsyncKeyState(vk) & 0x8000:
            pressed.add(name)
    return frozenset(pressed)


# --------------------------------------------------------------------------
# The listener
# --------------------------------------------------------------------------


def _injection_gate() -> threading.Event:
    """paste_win's flag, imported late so this module loads without it."""
    from vocal_advantage.paste_win import injection_active

    return injection_active


class HotkeyListener:
    """Installs the raw hook and forwards meaningful key events to ``on_event``.

    Every event that is not noise is forwarded, not just the hotkey's own -
    the controller needs to see other keys to cancel a recording that turned
    out to be Right Ctrl+C.
    """

    def __init__(
        self,
        spec: HotkeySpec,
        on_event: Callable[[str, bool], None],
        *,
        gate: threading.Event | None = None,
    ) -> None:
        self._spec = spec
        self._on_event = on_event
        self._gate = gate if gate is not None else _injection_gate()
        self._detector = EdgeDetector(spec, gate=self._gate, read_pressed=self._read_pressed)
        self._watch = frozenset(spec.keys) | MODIFIER_KEYS
        self._lock = threading.Lock()
        self._keyboard = None
        self._hook = None

    def _read_pressed(self) -> frozenset[str]:
        return read_pressed_keys(self._watch)

    def start(self) -> None:
        if self._hook is not None:
            return
        import keyboard

        self._keyboard = keyboard
        with self._lock:
            self._detector.resync(self._read_pressed())
        # No suppress= argument, ever: the hotkey must still reach the app.
        self._hook = keyboard.hook(self._handle_event)

    def stop(self) -> None:
        if self._hook is None:
            return
        self._keyboard.unhook(self._hook)
        self._hook = None

    def _handle_event(self, event) -> None:
        event_type = getattr(event, "event_type", None)
        if event_type not in (KEY_DOWN, KEY_UP):
            return
        name = normalise_key_name(getattr(event, "name", None))
        if name is None:
            return
        is_down = event_type == KEY_DOWN
        with self._lock:
            key = self._detector.canonical(name)
            edge = self._detector.feed(name, is_down)
        if edge is not Edge.IGNORED:
            self._on_event(key, is_down)


# --------------------------------------------------------------------------
# --set-hotkey capture
# --------------------------------------------------------------------------


class _CaptureSession(CaptureSession):
    """CaptureSession plus the keyboard library's event shape."""

    def feed_event(self, event) -> None:
        event_type = getattr(event, "event_type", None)
        if event_type not in (KEY_DOWN, KEY_UP):
            return
        self.feed(getattr(event, "name", None), event_type == KEY_DOWN)


def capture_hotkey(
    timeout_s: float = 15.0,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> HotkeySpec:
    """Record the chord the user holds once, echo it, validate it, return it.

    ONE shot. This function does not print the prompt and does not retry:
    main.run_set_hotkey prints "Hold the key or combo you want, then release
    it." and owns the retry loop, so exactly one layer decides when to give up.
    Two exceptions are how failure is reported, and both are part of the
    contract:

    * ``HotkeyError`` - the chord is banned or unknown (Caps Lock, bare Win, an
      unrecognised name). Raised by ``parse_hotkey`` and deliberately not caught
      here; the caller prints the reason and asks again.
    * ``TimeoutError`` - nothing was held within ``timeout_s``; the caller
      leaves config.json untouched.

    ``clock`` and ``sleep`` are injection seams so the timeout can be tested in
    no time at all instead of really waiting 15 seconds.
    """
    import keyboard

    session = _CaptureSession()
    handle = keyboard.hook(session.feed_event)  # never suppressed
    try:
        deadline = clock() + timeout_s
        while not session.done and clock() < deadline:
            sleep(0.02)
    finally:
        keyboard.unhook(handle)

    if not session.done:
        raise TimeoutError(
            f"No key held within {timeout_s:.0f}s - hotkey left unchanged."
        )

    # Echo before validating (SPEC: "echoes it back in plain English, validates
    # it"), so a refused chord still tells the user what we heard. HotkeySpec's
    # __str__ is the project's only key-name display format - "Ctrl + Win", not
    # "Ctrl + Windows" - so this line and main.py's "Hotkey set to ..." agree.
    print(f"You held: {HotkeySpec(frozenset(session.largest))}")
    return parse_hotkey("+".join(sorted(session.largest)))
