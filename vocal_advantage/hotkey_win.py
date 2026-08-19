"""The global keyboard hook (Windows).

Three layers, deliberately separated so the tricky part is testable:

* ``normalise_key_name`` / ``spec_key_for`` - pure name translation between the
  ``keyboard`` library's vocabulary and the names in a ``HotkeySpec``.
* ``EdgeDetector`` - pure state: which keys are held, and which raw key event
  is the "hotkey went down" / "hotkey went up" edge. No Windows calls, no
  threads, so tests drive it with synthetic events.
* ``HotkeyListener`` - the thin Windows layer: installs ``keyboard.hook``,
  feeds the detector, forwards surviving events to ``on_event``.

We never pass ``suppress=True``. Suppression is where the keyboard library's
bugs live (issues #442/#666), and the spec rules it out for v0.1.

This file imports on any platform. Only ``read_pressed_keys`` needs Windows,
and its ``user32`` binding is guarded below - the two pure layers are where the
real logic lives, and they are what the tests exercise.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from enum import Enum
from typing import Callable, Iterable

from vocal_advantage.hotkey_spec import HotkeySpec, parse_hotkey

# keyboard.KEY_DOWN / keyboard.KEY_UP are these literals. Spelling them out
# means this module imports fine without the keyboard package installed.
KEY_DOWN = "down"
KEY_UP = "up"

# --------------------------------------------------------------------------
# Name translation
# --------------------------------------------------------------------------

# Spellings that mean the same key. Left side: anything the keyboard library or
# a hand-edited config.json might say. Right side: the name we work in.
_ALIAS_TO_CANONICAL = {
    "control": "ctrl",
    "left control": "left ctrl",
    "right control": "right ctrl",
    "left menu": "left alt",  # Windows calls the Alt keys "menu"
    "right menu": "right alt",
    "win": "windows",
    "left win": "left windows",
    "right win": "right windows",
    "cmd": "windows",
    "command": "windows",
    "escape": "esc",
    "return": "enter",
    "capslock": "caps lock",
    "spacebar": "space",
    "space bar": "space",
    "pgup": "page up",
    "pageup": "page up",
    "pgdown": "page down",
    "pagedown": "page down",
    "ins": "insert",
    "del": "delete",
    "prtscn": "print screen",
    "snapshot": "print screen",
    "numlock": "num lock",
    "scrlk": "scroll lock",
}


def normalise_key_name(name: str | None) -> str | None:
    """Fold one key name into our vocabulary. ``None`` for a nameless event.

    The hook occasionally reports events with no name (unmapped scan codes);
    those are dropped rather than tracked under a bogus name.
    """
    if not name:
        return None
    text = " ".join(str(name).split()).lower()
    if not text:
        return None
    return _ALIAS_TO_CANONICAL.get(text, text)


# Which spec names a given event name is allowed to satisfy, best match first.
#
# This is the trap in this file. On Windows the hook names a key from
# GetKeyNameText, and Windows does NOT say "left" for the left-hand modifiers:
# the left Ctrl arrives as "ctrl", the left Shift as "shift", the left Alt as
# "alt", while the right-hand ones arrive as "right ctrl"/"right shift"/
# "right alt" and both Windows keys are sided. So a config of "left ctrl" must
# still be matched by an event named "ctrl", and a config of "ctrl" (meaning
# either Ctrl) must be matched by "right ctrl" too.
_EQUIVALENTS = {
    "ctrl": ("ctrl", "left ctrl"),
    "left ctrl": ("left ctrl", "ctrl"),
    "right ctrl": ("right ctrl", "ctrl"),
    "shift": ("shift", "left shift"),
    "left shift": ("left shift", "shift"),
    "right shift": ("right shift", "shift"),
    "alt": ("alt", "left alt"),
    "left alt": ("left alt", "alt"),
    "right alt": ("right alt", "alt"),
    "alt gr": ("alt gr", "right alt", "alt"),
    "windows": ("windows", "win", "left windows", "left win"),
    "left windows": ("left windows", "left win", "windows", "win"),
    "right windows": ("right windows", "right win", "windows", "win"),
}


def spec_key_for(name: str, spec_keys: Iterable[str]) -> str:
    """Return the name to file this event under, given the configured keys.

    If the event matches one of the configured keys (directly or through
    ``_EQUIVALENTS``) we return the configured spelling, so the controller can
    compare it against ``HotkeySpec.keys`` with plain ``in``. Otherwise the
    event's own name comes back and it counts as "some other key".
    """
    keys = set(spec_keys)
    for candidate in _EQUIVALENTS.get(name, (name,)):
        if candidate in keys:
            return candidate
    return name


# --------------------------------------------------------------------------
# Reading the real keyboard state (used to resync after a paste)
# --------------------------------------------------------------------------

# Guarded, because ``ctypes.WinDLL`` does not exist off Windows -- calling it at
# module scope would make this file unimportable on a Mac and take the two pure
# layers above down with it. Same shape as the guards in ``hotkey_spec`` and
# ``recorder``. On Windows nothing about this changes.
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

MODIFIER_KEYS = frozenset({
    "ctrl", "left ctrl", "right ctrl",
    "shift", "left shift", "right shift",
    "alt", "left alt", "right alt", "alt gr",
    "windows", "win", "left windows", "left win", "right windows", "right win",
})


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
# The pure edge detector
# --------------------------------------------------------------------------


class Edge(Enum):
    """What one raw key event means."""

    IGNORED = "ignored"          # autorepeat, gated by a paste, or noise
    HOTKEY_DOWN = "hotkey_down"  # last key of the combo went down: start
    HOTKEY_UP = "hotkey_up"      # a key of the combo went up: stop
    COMBO_DOWN = "combo_down"    # combo member down, combo not complete yet
    COMBO_UP = "combo_up"        # combo member up, but we were not armed
    OTHER_DOWN = "other_down"    # a key outside the combo went down
    OTHER_UP = "other_up"        # a key outside the combo went up


class EdgeDetector:
    """Tracks held keys and classifies each raw key event. No Windows, no threads.

    ``gate`` is ``paste_win.injection_active`` in the real app: while it is set
    we are injecting Ctrl+V ourselves, so every event is ignored. Because we
    were blind during that window, the first event afterwards triggers a resync
    of the held set from ``read_pressed`` - otherwise our own injected Ctrl
    stays "held" and the next dictation starts instantly or never.
    """

    def __init__(
        self,
        spec: HotkeySpec,
        *,
        gate: threading.Event | None = None,
        read_pressed: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._combo = frozenset(spec.keys)
        self._gate = gate if gate is not None else threading.Event()
        self._read_pressed = read_pressed
        self._held: set[str] = set()
        self._armed = False
        self._lost_sync = False

    @property
    def held(self) -> frozenset[str]:
        return frozenset(self._held)

    @property
    def armed(self) -> bool:
        """True between a HOTKEY_DOWN and its HOTKEY_UP."""
        return self._armed

    def canonical(self, name: str) -> str:
        return spec_key_for(name, self._combo)

    def resync(self, pressed: Iterable[str]) -> None:
        """Replace the held set with what is physically down."""
        self._held = {self.canonical(n) for n in (normalise_key_name(p) for p in pressed) if n}
        self._armed = bool(self._combo) and self._combo.issubset(self._held)
        self._lost_sync = False

    def feed(self, name: str, is_down: bool) -> Edge:
        key = self.canonical(name)

        if self._gate.is_set():
            self._lost_sync = True
            return Edge.IGNORED

        if self._lost_sync:
            pressed = self._read_pressed() if self._read_pressed is not None else ()
            self.resync(pressed)
            # The event in hand has already happened, so undo it before
            # classifying: a key we are being told went down must not already
            # be in the held set, or we would dismiss it as autorepeat.
            if is_down:
                self._held.discard(key)
            else:
                self._held.add(key)
            self._armed = bool(self._combo) and self._combo.issubset(self._held)

        in_combo = key in self._combo

        if is_down:
            if key in self._held:
                return Edge.IGNORED  # OS autorepeat while the key stays down
            self._held.add(key)
            if in_combo:
                if not self._armed and self._combo.issubset(self._held):
                    self._armed = True
                    return Edge.HOTKEY_DOWN
                return Edge.COMBO_DOWN
            return Edge.OTHER_DOWN

        if key not in self._held:
            return Edge.IGNORED  # an up with no matching down
        self._held.discard(key)
        if in_combo:
            if self._armed:
                self._armed = False
                return Edge.HOTKEY_UP
            return Edge.COMBO_UP
        return Edge.OTHER_UP


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


class _CaptureSession:
    """Remembers the largest set of keys held at the same time."""

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._largest: frozenset[str] = frozenset()

    @property
    def largest(self) -> frozenset[str]:
        return self._largest

    @property
    def done(self) -> bool:
        return bool(self._largest) and not self._held

    def feed(self, name: str | None, is_down: bool) -> None:
        key = normalise_key_name(name)
        if key is None:
            return
        if is_down:
            self._held.add(key)
            if len(self._held) > len(self._largest):
                self._largest = frozenset(self._held)
        else:
            self._held.discard(key)

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
