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


def paste_with(
    text: str, backend: PasteBackend, chord: Iterable[tuple[int, bool]]
) -> bool:
    """Put text on the clipboard and paste it into the focused window.

    Returns False (never raises) when there is nothing to paste, when the
    clipboard could not be written, or when the OS refused the keystrokes. In
    the last case the text is still on the clipboard for a manual paste.
    """
    if not text.strip():
        return False

    injection_active.set()
    try:
        wait_for_modifier_release(backend)
        if not set_clipboard_with_retry(backend, text):
            return False
        backend.sleep(CLIPBOARD_SETTLE_S)
        pasted = send_chord(backend, chord)
        # Keep the guard up a little longer so the hotkey hook sees and drops
        # our own injected key events before it starts listening again.
        backend.sleep(POST_PASTE_S)
        return pasted
    finally:
        # Always, even on an unexpected exception: a stuck flag would make the
        # hotkey stop working entirely until restart.
        injection_active.clear()
