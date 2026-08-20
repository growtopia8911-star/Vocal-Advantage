"""Clipboard + synthetic Cmd+V text injection (macOS).

The twin of ``paste_win``. The sequence and its delays live in ``paste_core``
and are shared; this file supplies the two things that are macOS: a backend
that talks to Quartz and NSPasteboard, and the chord that means "paste" here.

**What macOS does better than Windows.** On Windows there is no way to tell our
own injected Ctrl+V from the user's keystrokes, so the hook has to be gated for
the whole paste and then resynced from GetAsyncKeyState afterwards. macOS lets
us write a magic value into every event we post
(``kCGEventSourceUserData``), so the key hook can recognise and skip exactly our
own events -- see ``is_injected``. That removes the race rather than working
around it. The shared ``injection_active`` gate is still set, as a second line
of defence and because ``paste_core`` owns the sequence for both platforms.

Test seam: Quartz is bound once, at import, to the module-level name ``Quartz``,
and the pasteboard is reached through ``_general_pasteboard()``. Tests replace
both, so the real logic runs with no windows, no permissions and no clipboard --
the same seam ``recorder.py`` uses for sounddevice.
"""

from __future__ import annotations

import time

# Re-exported so callers and the key hook share one flag and one set of delays.
from vocal_advantage.paste_core import (  # noqa: F401
    CLIPBOARD_ATTEMPTS,
    type_with,
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

try:  # pragma: no cover - absence is checked with Quartz patched to None
    import Quartz
except Exception:  # noqa: BLE001 - any import failure means "not on macOS"
    Quartz = None


# --- keycodes ---------------------------------------------------------------
# Observed with a live event tap on this machine rather than taken from a table.
KEYCODE_COMMAND = 55        # left Command
KEYCODE_RIGHT_COMMAND = 54
KEYCODE_SHIFT = 56
KEYCODE_RIGHT_SHIFT = 60
KEYCODE_OPTION = 58         # left Option
KEYCODE_RIGHT_OPTION = 61   # the default hotkey
KEYCODE_CONTROL = 59
KEYCODE_RIGHT_CONTROL = 62
KEYCODE_V = 9

# How many characters ride on one synthetic event. Kept modest deliberately:
# very long unicode payloads are handled inconsistently by some apps, and the
# cost of another event is negligible.
TYPE_CHUNK_CHARS = 20

# The stamp that tells our own key hook "this was us, ignore it". Any value
# works; it just has to be one nothing else would plausibly write.
INJECTED_MARKER = 0x564F4341  # "VOCA"

# --- pasteboard types -------------------------------------------------------
PASTEBOARD_TYPE_STRING = "public.utf8-plain-text"
# The convention clipboard managers honour for "do not record this entry". The
# macOS analogue of the three Windows privacy formats: privacy is the product,
# so a dictation must not end up in a clipboard history app.
PASTEBOARD_TYPE_CONCEALED = "org.nspasteboard.ConcealedType"


# --- the macOS paste chord --------------------------------------------------
CMD_V_SEQUENCE = (
    (KEYCODE_COMMAND, True),
    (KEYCODE_V, True),
    (KEYCODE_V, False),
    (KEYCODE_COMMAND, False),
)


def _modifier_flag(keycode: int) -> int:
    """The flag bit a modifier keycode contributes, or 0 for a normal key."""
    if Quartz is None:  # pragma: no cover - not macOS
        return 0
    return {
        KEYCODE_COMMAND: Quartz.kCGEventFlagMaskCommand,
        KEYCODE_RIGHT_COMMAND: Quartz.kCGEventFlagMaskCommand,
        KEYCODE_SHIFT: Quartz.kCGEventFlagMaskShift,
        KEYCODE_RIGHT_SHIFT: Quartz.kCGEventFlagMaskShift,
        KEYCODE_CONTROL: Quartz.kCGEventFlagMaskControl,
        KEYCODE_RIGHT_CONTROL: Quartz.kCGEventFlagMaskControl,
        KEYCODE_OPTION: Quartz.kCGEventFlagMaskAlternate,
        KEYCODE_RIGHT_OPTION: Quartz.kCGEventFlagMaskAlternate,
    }.get(keycode, 0)


def _all_modifier_mask() -> int:
    if Quartz is None:  # pragma: no cover - not macOS
        return 0
    return (
        Quartz.kCGEventFlagMaskCommand
        | Quartz.kCGEventFlagMaskShift
        | Quartz.kCGEventFlagMaskControl
        | Quartz.kCGEventFlagMaskAlternate
    )


def is_injected(event) -> bool:
    """True if this event is one we posted ourselves.

    The key hook calls this and drops the event. Without it, our own Cmd+V looks
    exactly like the user pressing Cmd+V, and the symptom is a second recording
    starting the instant a dictation pastes.
    """
    if Quartz is None:  # pragma: no cover - not macOS
        return False
    value = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData)
    return int(value) == INJECTED_MARKER


def _general_pasteboard():
    """The system pasteboard. A function so tests can replace it."""
    from AppKit import NSPasteboard

    return NSPasteboard.generalPasteboard()


def _set_clipboard(text: str) -> None:
    """Put text on the pasteboard, marked so clipboard managers skip it.

    Raises OSError on refusal so paste_core's retry loop can have another go -
    the pasteboard can be momentarily owned by another process.
    """
    board = _general_pasteboard()
    board.clearContents()
    if not board.setString_forType_(text, PASTEBOARD_TYPE_STRING):
        raise OSError("NSPasteboard refused the text")
    # Presence is what counts for the concealed marker, not the value.
    board.setString_forType_("", PASTEBOARD_TYPE_CONCEALED)


class MacBackend:
    """The real PasteBackend for macOS.

    Tracks the modifier flags it has pressed so far, because posting a
    Command-down event does NOT implicitly flag the events that follow it: the V
    keystroke has to carry the Command mask itself or it arrives as a plain "v"
    and types a letter into the user's document.
    """

    def __init__(self) -> None:
        self._flags = 0

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def modifiers_down(self) -> bool:
        """True while any Command/Shift/Control/Option key is physically held."""
        if Quartz is None:  # pragma: no cover - not macOS
            return False
        state = Quartz.CGEventSourceFlagsState(
            Quartz.kCGEventSourceStateHIDSystemState
        )
        return bool(state & _all_modifier_mask())

    def set_clipboard(self, text: str) -> None:
        _set_clipboard(text)

    def send_text(self, text: str) -> bool:
        """Type the text in directly, bypassing the keyboard layout entirely.

        The keycode is irrelevant once a unicode string is attached, so 0 is
        used by convention. Flags are cleared explicitly: a stale Command flag
        would turn the whole transcript into a run of keyboard shortcuts.
        """
        if Quartz is None:  # pragma: no cover - not macOS
            return False
        for start in range(0, len(text), TYPE_CHUNK_CHARS):
            chunk = text[start : start + TYPE_CHUNK_CHARS]
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                if event is None:
                    return False
                Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
                Quartz.CGEventSetFlags(event, 0)
                Quartz.CGEventSetIntegerValueField(
                    event, Quartz.kCGEventSourceUserData, INJECTED_MARKER
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return True

    def send_key(self, keycode: int, down: bool) -> int:
        """Post one key event, stamped as ours. Returns 1 if it was posted.

        macOS gives no delivery confirmation the way SendInput does, so this
        cannot detect a refused injection. The equivalent failure - Accessibility
        permission missing - is caught far earlier, when the key hook fails to
        create its event tap, and reported there.
        """
        if Quartz is None:  # pragma: no cover - not macOS
            return 0
        flag = _modifier_flag(keycode)
        if flag:
            if down:
                self._flags |= flag
            else:
                self._flags &= ~flag
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        if event is None:
            return 0
        Quartz.CGEventSetFlags(event, self._flags)
        Quartz.CGEventSetIntegerValueField(
            event, Quartz.kCGEventSourceUserData, INJECTED_MARKER
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return 1


_DEFAULT_BACKEND: PasteBackend | None = None


def _default_backend() -> PasteBackend:
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = MacBackend()
    return _DEFAULT_BACKEND


def paste_text(text: str, *, backend: PasteBackend | None = None) -> bool:
    """Deliver the transcript to the focused app, without using the clipboard.

    macOS lets a synthetic key event carry the literal text
    (CGEventKeyboardSetUnicodeString), which sidesteps both reasons Windows
    needs the clipboard -- dropped characters and keyboard-layout translation --
    and leaves whatever the user had copied exactly where it was.

    For ordinary dictation this is also the quicker route: the clipboard path
    pays a fixed ~0.22s in settle and hold delays regardless of length, while
    this pays only per chunk of 20 characters. A long passage would eventually
    overtake it; see paste_via_clipboard, kept for exactly that case.
    """
    if backend is None:
        backend = _default_backend()
    return type_with(text, backend)


def paste_via_clipboard(text: str, *, backend: PasteBackend | None = None) -> bool:
    """The clipboard + Cmd+V route. Replaces the user's clipboard.

    Kept because it costs the same no matter how much was said, so it is the
    better choice for a long dictation, and because it is the route Windows
    uses -- one implementation, tested on both.
    """
    if backend is None:
        backend = _default_backend()
    return paste_with(text, backend, CMD_V_SEQUENCE)
