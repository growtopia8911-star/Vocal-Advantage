"""The paste sequence, minus the platform.

The order of operations and every delay below come from SPEC.md "Paste
sequence". The numbers were tuned by espanso and LocalFlow against real apps;
they are not guesses. Do not reorder or shrink them without re-testing against
real applications.

Nothing here touches an OS. A platform module supplies two things:

* a **backend** satisfying ``PasteBackend`` - five methods, all of the real
  clock, sleeping, key-state reading, clipboard writing and key injection;
* a **chord** - the key sequence that means "paste" there. Ctrl+V on Windows,
  Cmd+V on macOS.

Keeping the sequence in one place matters because the delays are the fragile
part: two copies would drift, and the symptom of a wrong delay is a paste that
works on your machine and silently drops text on someone else's.
"""

from __future__ import annotations

import threading
from typing import Iterable, Protocol

# The key hook drops every key event while this is set. ONE object, shared by
# both platforms: the hook and the paste module must watch the very same flag,
# or the hook reacts to our own injected keystrokes as though the user typed
# them. It is the load-bearing guard, not "we press a different modifier than
# you do" -- the hotkey is user-configurable, so the user may well have chosen
# the very key we inject.
injection_active = threading.Event()

# --- timings (SPEC.md "Paste sequence") -------------------------------------
MODIFIER_WAIT_S = 2.0  # cap on waiting for physically held modifiers
MODIFIER_POLL_S = 0.01
CLIPBOARD_ATTEMPTS = 5  # the clipboard can be held by another process
CLIPBOARD_RETRY_S = 0.05
CLIPBOARD_SETTLE_S = 0.1  # apps fetch the clipboard lazily
KEY_INTERVAL_S = 0.02
POST_PASTE_S = 0.06
#: How long the transcript stays on the clipboard after the chord before the
#: user's own contents are put back. Apps fetch the clipboard lazily and some
#: do it well after the keystroke; restoring immediately would race them and
#: paste the *old* clipboard into the document, which is far worse than the
#: clipboard being briefly wrong.
CLIPBOARD_RESTORE_S = 0.25


class PasteBackend(Protocol):
    """Everything the sequence needs from the outside world.

    Win32Backend and MacBackend are the real ones; tests pass a fake with a
    virtual clock.
    """

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def modifiers_down(self) -> bool: ...

    def set_clipboard(self, text: str) -> None:
        """Raise OSError if the clipboard could not be written."""

    def send_key(self, vk: int, down: bool) -> int:
        """Return the number of events actually inserted (0 = refused)."""


def wait_for_modifier_release(backend: PasteBackend) -> bool:
    """Block until no modifier is physically held, or MODIFIER_WAIT_S passes.

    Returns whether they actually released. We paste either way: a user who
    leans on Shift for two seconds should get a wrong-looking paste, not a
    frozen app.

    This step is not optional. Injected keystrokes combine with keys the user is
    still physically holding, and the hotkey itself is usually a modifier - so
    without this, letting go of the hotkey a fraction late turns the paste chord
    into a different shortcut entirely.
    """
    deadline = backend.monotonic() + MODIFIER_WAIT_S
    while backend.modifiers_down():
        if backend.monotonic() >= deadline:
            return False
        backend.sleep(MODIFIER_POLL_S)
    return True


def set_clipboard_with_retry(backend: PasteBackend, text: str) -> bool:
    for attempt in range(CLIPBOARD_ATTEMPTS):
        try:
            backend.set_clipboard(text)
            return True
        except OSError:
            if attempt == CLIPBOARD_ATTEMPTS - 1:
                return False
            backend.sleep(CLIPBOARD_RETRY_S)
    return False


def send_chord(backend: PasteBackend, chord: Iterable[tuple[int, bool]]) -> bool:
    """Inject the paste chord. True only if every event landed."""
    steps = tuple(chord)
    inserted_everything = True
    last = len(steps) - 1
    for index, (vk, down) in enumerate(steps):
        if backend.send_key(vk, down) == 0:
            inserted_everything = False
        if index < last:
            backend.sleep(KEY_INTERVAL_S)
    return inserted_everything


def read_clipboard(backend) -> str | None:
    """The clipboard's current contents, or None if they could not be read.

    None and "" are deliberately different answers. "" means the clipboard was
    genuinely empty and must be restored to empty; None means we do not know
    what was there, and the only safe response to that is to leave whatever we
    wrote in place rather than blank something we never saw.

    A backend need not implement this at all -- save/restore is best-effort.
    """
    reader = getattr(backend, "get_clipboard", None)
    if reader is None:
        return None
    try:
        value = reader()
    except Exception:  # noqa: BLE001 - unreadable is a normal outcome here
        return None
    return value if isinstance(value, str) else None


def restore_clipboard(backend, saved: str | None) -> None:
    """Put ``saved`` back. Never raises, never blanks on an unknown.

    Failing to restore is a papercut; raising here would turn a successful
    dictation into a failed one after the text has already been pasted.
    """
    if saved is None:
        return
    try:
        backend.set_clipboard(saved)
    except Exception:  # noqa: BLE001
        pass


def paste_with(
    text: str, backend: PasteBackend, chord: Iterable[tuple[int, bool]]
) -> bool:
    """Put text on the clipboard, paste it, and put the clipboard back.

    Returns False (never raises) when there is nothing to paste, when the
    clipboard could not be written, or when the OS refused the keystrokes. In
    the last case the text is still on the clipboard for a manual paste --
    which is why the restore waits for the chord to have been *sent* rather
    than to have succeeded.

    The restore is in a ``finally``: a refused chord (spec 10e) or an
    unexpected explosion must not leave the user's clipboard replaced by
    whatever they happened to dictate.
    """
    if not text.strip():
        return False

    injection_active.set()
    saved: str | None = None
    wrote_clipboard = False
    try:
        wait_for_modifier_release(backend)
        # Read before writing, or there is nothing left to read (10c).
        saved = read_clipboard(backend)
        if not set_clipboard_with_retry(backend, text):
            return False
        wrote_clipboard = True
        backend.sleep(CLIPBOARD_SETTLE_S)
        pasted = send_chord(backend, chord)
        # Keep the guard up a little longer so the hotkey hook sees and drops
        # our own injected key events before it starts listening again.
        backend.sleep(POST_PASTE_S)
        # Then give the receiving app time to actually fetch what we wrote,
        # before it is taken away again.
        backend.sleep(CLIPBOARD_RESTORE_S)
        return pasted
    finally:
        if wrote_clipboard:
            restore_clipboard(backend, saved)
        # Always, even on an unexpected exception: a stuck flag would make the
        # hotkey stop working entirely until restart.
        injection_active.clear()


def type_with(text: str, backend) -> bool:
    """Type the text straight in, never touching the clipboard.

    The clipboard route exists because Windows makes synthetic typing
    unreliable: characters get dropped, and a fake keypress is interpreted
    through the user's keyboard layout, so the same key yields different
    characters on different layouts. Neither applies when the platform lets you
    attach the literal text to the event, which macOS does. Then typing is both
    faster for ordinary dictation and leaves the user's clipboard alone -- and
    a clipboard silently replaced by whatever you just said is a genuinely
    unpleasant surprise.

    ``backend`` must additionally provide ``send_text(str) -> bool``.
    """
    if not text.strip():
        return False

    injection_active.set()
    try:
        # Not optional, and more important here than on the clipboard route:
        # the hotkey is usually a modifier, and typing while it is still
        # physically held turns every character into a keyboard shortcut.
        wait_for_modifier_release(backend)
        sent = backend.send_text(text)
        # Hold the guard a moment so the key hook drops our own events.
        backend.sleep(POST_PASTE_S)
        return bool(sent)
    finally:
        injection_active.clear()
