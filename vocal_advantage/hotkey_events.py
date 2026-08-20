"""Key-name translation and edge detection. No OS calls, no threads.

This is the half of the key hook that has nothing platform-specific in it:

* ``normalise_key_name`` / ``spec_key_for`` - translation between whatever
  vocabulary the OS hands us and the names in a ``HotkeySpec``.
* ``EdgeDetector`` - which keys are held, and which raw event is the "hotkey
  went down" / "hotkey went up" edge.

It lived in ``hotkey_win.py`` until the macOS port needed it too. Keeping one
copy matters more here than usual: this is the trickiest logic in the project,
and two drifting copies of it would be very hard to debug from the symptoms
(a hotkey that silently never fires, or one that never stops).

Both ``hotkey_win`` and ``hotkey_mac`` import from here. Anything that needs a
Windows or macOS API belongs in those files, not this one.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import Callable, Iterable

from vocal_advantage.hotkey_spec import HotkeySpec

# keyboard.KEY_DOWN / keyboard.KEY_UP are these literals. Spelling them out
# means this module imports fine without the keyboard package installed.
KEY_DOWN = "down"
KEY_UP = "up"

# --------------------------------------------------------------------------
# Name translation
# --------------------------------------------------------------------------

# Spellings that mean the same key. Left side: anything the keyboard library, a
# macOS keycode table, or a hand-edited config.json might say. Right side: the
# name we work in. macOS Option is Alt, which is why the Mac's Right Option maps
# onto "right alt" and one config.json is portable between the two machines.
_ALIAS_TO_CANONICAL = {
    "control": "ctrl",
    "left control": "left ctrl",
    "right control": "right ctrl",
    "left menu": "left alt",  # Windows calls the Alt keys "menu"
    "right menu": "right alt",
    "option": "alt",
    "left option": "left alt",
    "right option": "right alt",
    "win": "windows",
    "left win": "left windows",
    "right win": "right windows",
    "cmd": "windows",
    "command": "windows",
    "left command": "left windows",
    "right command": "right windows",
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
# This is the trap on Windows. There the hook names a key from GetKeyNameText,
# and Windows does NOT say "left" for the left-hand modifiers: the left Ctrl
# arrives as "ctrl", the left Shift as "shift", the left Alt as "alt", while the
# right-hand ones arrive as "right ctrl"/"right shift"/"right alt" and both
# Windows keys are sided. So a config of "left ctrl" must still be matched by an
# event named "ctrl", and a config of "ctrl" (meaning either Ctrl) must be
# matched by "right ctrl" too.
#
# macOS does not have this problem - its event taps report left and right as
# distinct keycodes - but the table is harmless there and keeps one vocabulary.
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


MODIFIER_KEYS = frozenset({
    "ctrl", "left ctrl", "right ctrl",
    "shift", "left shift", "right shift",
    "alt", "left alt", "right alt", "alt gr",
    "windows", "win", "left windows", "left win", "right windows", "right win",
})


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
    """Tracks held keys and classifies each raw key event. No OS, no threads.

    ``gate`` is the paste module's ``injection_active`` in the real app: while
    it is set we are injecting the paste keystroke ourselves, so every event is
    ignored. Because we were blind during that window, the first event
    afterwards triggers a resync of the held set from ``read_pressed``  -
    otherwise our own injected modifier stays "held" and the next dictation
    starts instantly or never.

    (On macOS the paste module also stamps its synthetic events so the tap can
    skip exactly those, which makes the gate a belt-and-braces second defence
    there rather than the only one.)
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


class CaptureSession:
    """Remembers the largest set of keys held at the same time.

    "Largest simultaneously-held set", not "last chord": the user rarely
    releases both halves of a combo at the same instant, so the moment of
    maximum overlap is the honest reading of what they meant.

    Portable - the platform modules decode their own events and call ``feed``.
    """

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
