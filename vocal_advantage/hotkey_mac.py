"""The global keyboard hook (macOS). The twin of ``hotkey_win``.

Same three layers, same contract. The portable two - name translation and
``EdgeDetector`` - come from ``hotkey_events`` and are shared with Windows
verbatim. What is here is the macOS part: a ``CGEventTap``, the keycode table,
and the decoding.

**The macOS trap, and it is the mirror image of the Windows one.** Observed with
a live tap on this machine rather than taken from documentation:

* Modifier keys do **not** emit key-down/key-up. They emit ``flagsChanged``.
  Anything waiting for a keyDown on Right Option waits forever, in silence.
* Left and right modifiers **are** distinguishable (Right Option is 61, Left is
  58). Windows is the awkward one here: there the left-hand modifiers arrive
  unsided as plain "ctrl", which is what the ``_EQUIVALENTS`` table exists for.
* ``flagsChanged`` does not say which direction the key moved, and the flag mask
  cannot always tell you either: hold both Options, release one, and the option
  flag is still set. ``CGEventSourceKeyState`` - the exact twin of Windows'
  ``GetAsyncKeyState`` - answers for one specific key, so that is what decides.

The tap is created **listen-only**. It never suppresses, which is the same rule
the Windows side follows.

Test seam: Quartz is bound once, at import, to the module-level name ``Quartz``.
Tests replace it and call ``handle_event`` directly, so no tap is created and no
permission is needed.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterable

from vocal_advantage.hotkey_events import (  # noqa: F401
    CaptureSession,
    Edge,
    EdgeDetector,
    MODIFIER_KEYS,
    normalise_key_name,
    spec_key_for,
)
from vocal_advantage.hotkey_spec import HotkeySpec, parse_hotkey

try:  # pragma: no cover - absence is checked with Quartz patched
    import Quartz
except Exception:  # noqa: BLE001 - any import failure means "not on macOS"
    Quartz = None


class HotkeyPermissionError(RuntimeError):
    """macOS refused to give us a keyboard tap."""


PERMISSION_MESSAGE = (
    "macOS would not let this app watch the keyboard.\n"
    "  Open System Settings > Privacy & Security > Accessibility, and switch on\n"
    "  the app you started this from (your terminal, or your editor).\n"
    "  You may need to quit and reopen that app afterwards.\n"
    "  Nothing else about Vocal Advantage needs permission - the microphone and\n"
    "  the transcription are unaffected."
)

# --- keycodes ---------------------------------------------------------------
KEYCODE_RIGHT_OPTION = 61

# macOS Option is Alt and macOS Command shares the Windows key's slot in the
# shared vocabulary, so one config.json is portable between the two machines.
KEYCODE_TO_NAME: dict[int, str] = {
    # modifiers - these arrive as flagsChanged, never keyDown
    54: "right windows", 55: "left windows",
    56: "left shift", 60: "right shift",
    57: "caps lock",
    58: "left alt", 61: "right alt",
    59: "left ctrl", 62: "right ctrl",
    63: "fn",
    # letters
    0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c",
    9: "v", 11: "b", 12: "q", 13: "w", 14: "e", 15: "r", 16: "y", 17: "t",
    31: "o", 32: "u", 34: "i", 35: "p", 37: "l", 38: "j", 40: "k", 45: "n",
    46: "m",
    # digits
    18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 25: "9", 26: "7",
    28: "8", 29: "0",
    # punctuation and editing
    24: "=", 27: "-", 30: "]", 33: "[", 39: "'", 41: ";", 42: "\\", 43: ",",
    44: "/", 47: ".", 50: "`",
    36: "enter", 48: "tab", 49: "space", 51: "backspace", 53: "esc",
    117: "delete",
    # navigation
    115: "home", 119: "end", 116: "page up", 121: "page down",
    123: "left", 124: "right", 125: "down", 126: "up",
    # function row
    122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7",
    100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12",
}

NAME_TO_KEYCODE: dict[str, int] = {}
for _code, _name in KEYCODE_TO_NAME.items():
    NAME_TO_KEYCODE.setdefault(_name, _code)

MODIFIER_KEYCODES = frozenset({54, 55, 56, 57, 58, 59, 60, 61, 62, 63})

# "right ctrl" - the Windows default - does not exist on a MacBook keyboard at
# all. Right Option is the closest analogue: present on every Mac, does nothing
# on its own, and sits under the right thumb.
DEFAULT_HOTKEY = "right alt"


def decode_event(event_type: int, event) -> tuple[str, bool] | None:
    """Turn one raw macOS event into ``(our name, is_down)``, or None to ignore."""
    if Quartz is None:  # pragma: no cover - not macOS
        return None
    keycode = int(
        Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
    )
    # An unmapped keycode still has to count as "some other key", or the
    # cancel-on-other-key rule would quietly stop working for it.
    name = KEYCODE_TO_NAME.get(keycode, f"key {keycode}")

    if event_type == Quartz.kCGEventFlagsChanged:
        # The event says a modifier moved but not which way, and the flag mask
        # is ambiguous when both sides of a pair are involved. Ask about this
        # exact key instead.
        is_down = bool(
            Quartz.CGEventSourceKeyState(
                Quartz.kCGEventSourceStateHIDSystemState, keycode
            )
        )
        return name, is_down
    if event_type == Quartz.kCGEventKeyDown:
        return name, True
    if event_type == Quartz.kCGEventKeyUp:
        return name, False
    return None


def read_pressed_keys(names: Iterable[str]) -> frozenset[str]:
    """Of ``names``, which are physically held right now?

    The twin of Windows' ``read_pressed_keys``; feeds the same resync.
    """
    if Quartz is None:  # pragma: no cover - not macOS
        return frozenset()
    pressed = set()
    for name in names:
        keycode = NAME_TO_KEYCODE.get(name)
        if keycode is not None and Quartz.CGEventSourceKeyState(
            Quartz.kCGEventSourceStateHIDSystemState, keycode
        ):
            pressed.add(name)
    return frozenset(pressed)


def _injection_gate() -> threading.Event:
    """paste_mac's flag, imported late so this module loads without it."""
    from vocal_advantage.paste_mac import injection_active

    return injection_active


def _is_our_own(event) -> bool:
    """Did we post this event ourselves during a paste?

    The marker constant is shared with paste_mac, but the *reading* is done
    through this module's own Quartz binding rather than by calling into
    paste_mac. Two modules, two seams: routing the check through paste_mac
    would mean this module's behaviour depended on which Quartz object
    paste_mac happened to be holding, which is exactly the kind of coupling
    that makes a fake in one test silently not apply.
    """
    if Quartz is None:  # pragma: no cover - not macOS
        return False
    from vocal_advantage.paste_mac import INJECTED_MARKER

    value = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData)
    return int(value) == INJECTED_MARKER


def _event_mask() -> int:
    return (
        (1 << Quartz.kCGEventKeyDown)
        | (1 << Quartz.kCGEventKeyUp)
        | (1 << Quartz.kCGEventFlagsChanged)
    )


class HotkeyListener:
    """Installs the tap and forwards meaningful key events to ``on_event``.

    Every event that is not noise is forwarded, not just the hotkey's own - the
    controller needs to see other keys to cancel a recording that turned out to
    be the user reaching for a shortcut.

    The tap needs a CFRunLoop to deliver events, and this owns one on its own
    thread - matching the Windows listener, where the keyboard library runs its
    hook on a thread of its own.
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
        self._detector = EdgeDetector(
            spec, gate=self._gate, read_pressed=self._read_pressed
        )
        self._watch = frozenset(spec.keys) | MODIFIER_KEYS
        self._lock = threading.Lock()
        self._tap = None
        self._thread = None
        self._runloop = None

    def _read_pressed(self) -> frozenset[str]:
        return read_pressed_keys(self._watch)

    def handle_event(self, proxy, event_type, event, refcon):
        """The tap callback. ALWAYS returns the event - we never suppress."""
        try:
            if _is_our_own(event):
                # Our own paste keystrokes. Without this the hook would read
                # them as the user typing and start a second recording the
                # instant a dictation pastes.
                return event
            decoded = decode_event(event_type, event)
            if decoded is None:
                return event
            name, is_down = decoded
            with self._lock:
                key = self._detector.canonical(name)
                edge = self._detector.feed(name, is_down)
            if edge is not Edge.IGNORED:
                self._on_event(key, is_down)
        except Exception:  # noqa: BLE001 - never let the tap thread die
            import traceback

            traceback.print_exc()
        return event

    def start(self) -> None:
        if self._tap is not None:
            return
        if Quartz is None:  # pragma: no cover - not macOS
            raise HotkeyPermissionError(PERMISSION_MESSAGE)

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,  # never suppress
            _event_mask(),
            self.handle_event,
            None,
        )
        if tap is None:
            # CGEventTapCreate explains nothing on failure, and a dead hotkey
            # with no message is the worst outcome here.
            raise HotkeyPermissionError(PERMISSION_MESSAGE)

        self._tap = tap
        with self._lock:
            self._detector.resync(self._read_pressed())
        self._thread = threading.Thread(
            target=self._run, name="vocal-advantage-tap", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:  # pragma: no cover - needs a real run loop
        from CoreFoundation import (
            CFRunLoopAddSource,
            CFRunLoopGetCurrent,
            CFRunLoopRun,
            kCFRunLoopDefaultMode,
        )

        self._runloop = CFRunLoopGetCurrent()
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(self._runloop, source, kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(self._tap, True)
        CFRunLoopRun()

    def stop(self) -> None:
        if self._tap is None:
            return
        try:
            Quartz.CGEventTapEnable(self._tap, False)
        except Exception:  # noqa: BLE001
            pass
        if self._runloop is not None:  # pragma: no cover - needs a real run loop
            from CoreFoundation import CFRunLoopStop

            CFRunLoopStop(self._runloop)
        self._tap = None
        self._thread = None
        self._runloop = None


# --------------------------------------------------------------------------
# --set-hotkey capture
# --------------------------------------------------------------------------

_CaptureSession = CaptureSession  # the shared one; named for symmetry with _win


def capture_hotkey(
    timeout_s: float = 15.0,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> HotkeySpec:
    """Record the chord the user holds once, echo it, validate it, return it.

    Identical contract to the Windows twin: ONE shot, no prompt, no retry.
    ``HotkeyError`` and ``TimeoutError`` are how it reports failure, and
    ``main.run_set_hotkey`` owns the prompt and the loop.
    """
    if Quartz is None:  # pragma: no cover - not macOS
        raise HotkeyPermissionError(PERMISSION_MESSAGE)

    from CoreFoundation import (
        CFRunLoopAddSource,
        CFRunLoopGetCurrent,
        CFRunLoopRunInMode,
        kCFRunLoopDefaultMode,
    )

    session = CaptureSession()

    def on_event(proxy, event_type, event, refcon):
        decoded = decode_event(event_type, event)
        if decoded is not None:
            session.feed(*decoded)
        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        _event_mask(),
        on_event,
        None,
    )
    if tap is None:
        raise HotkeyPermissionError(PERMISSION_MESSAGE)

    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopDefaultMode)
    Quartz.CGEventTapEnable(tap, True)
    try:
        deadline = clock() + timeout_s
        while not session.done and clock() < deadline:
            # Pumping the run loop IS the wait here; `sleep` stays in the
            # signature so the timeout is testable the way Windows' is.
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, False)
    finally:
        Quartz.CGEventTapEnable(tap, False)

    if not session.done:
        raise TimeoutError(
            f"No key held within {timeout_s:.0f}s - hotkey left unchanged."
        )

    print(f"You held: {HotkeySpec(frozenset(session.largest))}")
    return parse_hotkey("+".join(sorted(session.largest)))
