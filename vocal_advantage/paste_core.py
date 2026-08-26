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
#: How often to ask the focused element whether the text has arrived, and how
#: long to keep asking. 10ms is well under the cost of the AX call it replaces;
#: 300ms is generous -- a paste that has not landed by then is not going to.
CONFIRM_POLL_S = 0.01
CONFIRM_TIMEOUT_S = 0.30


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


#: Guards the two lines below. Held only to read/write them, never across a
#: sleep or an OS call.
_restore_lock = threading.Lock()
#: Bumped by every paste. A restore that finds the counter has moved on knows a
#: newer dictation owns the clipboard now and quietly does nothing.
_paste_generation = 0
#: What the user actually had, while a restore is still owed. Dictate twice in
#: quick succession and the second paste reads a clipboard holding the *first
#: dictation's* text -- preserving that would hand the user their own transcript
#: back instead of what they had copied. So while a restore is pending, the
#: pending value is carried forward rather than re-read. The blocking version of
#: this code could not hit the problem, because the restore always finished
#: before the next paste began.
_pending_saved: str | None = None
_restore_pending = False


def snapshot_safely(confirm) -> object | None:
    """The confirmer's reading of the focused element, or None. Never raises.

    None means "cannot tell", and the caller then behaves exactly as it did
    before confirmation existed. Accessibility is an optimisation; it must
    never be the reason a dictation fails.
    """
    if confirm is None:
        return None
    try:
        return confirm.snapshot()
    except Exception:  # noqa: BLE001
        return None


def wait_until_pasted(backend, confirm, before) -> bool | None:
    """Poll the focused element until the text lands. None if we cannot tell.

    Returns True the moment the element changes size, False if it never does
    within CONFIRM_TIMEOUT_S, and None when there is nothing to observe -- no
    confirmer, no baseline reading, or an Accessibility call that failed.

    That three-way answer is the point. True and False are both *knowledge*;
    None is the absence of it, and only None may fall back to sleeping and
    assuming success. Collapsing None into False would report every Electron
    app's paste as a failure.
    """
    if confirm is None or before is None:
        return None
    deadline = backend.monotonic() + CONFIRM_TIMEOUT_S
    while backend.monotonic() < deadline:
        backend.sleep(CONFIRM_POLL_S)
        try:
            if confirm.changed(before):
                return True
        except Exception:  # noqa: BLE001 - lost the element mid-paste
            return None
    return False


def _spawn(work) -> None:
    """Run the restore on a daemon thread, so the dictation does not wait.

    Daemon on purpose: a restore in flight must never hold the app open at
    shutdown. Losing it costs the user a clipboard they can re-copy; a process
    that will not quit costs them a Force Quit.
    """
    threading.Thread(target=work, name="clipboard-restore", daemon=True).start()


def restore_clipboard_later(backend, saved: str | None, generation: int) -> None:
    """Wait for the app to take the text, then put the clipboard back.

    The wait is still necessary -- apps fetch the clipboard lazily and some do
    it well after the keystroke, so restoring immediately races the paste and
    puts the *old* clipboard into the document. What changed is who pays for
    it: this runs off the critical path, so the user's dictation is finished
    while the wait happens.

    The generation check is what makes that safe. Dictate twice quickly and the
    first restore comes due after the second paste has already written its own
    text; restoring blindly would wipe the newer dictation off the clipboard.
    """
    global _restore_pending

    backend.sleep(CLIPBOARD_RESTORE_S)
    with _restore_lock:
        if generation != _paste_generation:
            return  # a newer paste owns the clipboard; its restore will run
        _restore_pending = False
    restore_clipboard(backend, saved)


def paste_with(
    text: str,
    backend: PasteBackend,
    chord: Iterable[tuple[int, bool]],
    *,
    schedule=_spawn,
    confirm=None,
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

    global _paste_generation, _pending_saved, _restore_pending

    injection_active.set()
    saved: str | None = None
    generation = 0
    wrote_clipboard = False
    try:
        wait_for_modifier_release(backend)
        # Before anything is written: this is the baseline the paste is
        # measured against, so it has to predate our own clipboard write.
        before = snapshot_safely(confirm)
        # Read before writing, or there is nothing left to read (10c). Outside
        # the lock: this is an OS call and the lock guards two variables.
        current = read_clipboard(backend)
        with _restore_lock:
            _paste_generation += 1
            generation = _paste_generation
            # If a restore is still owed, the clipboard currently holds our own
            # previous transcript -- carry the real value forward instead.
            saved = _pending_saved if _restore_pending else current
            _pending_saved = saved
            _restore_pending = True
        if not set_clipboard_with_retry(backend, text):
            return False
        wrote_clipboard = True
        backend.sleep(CLIPBOARD_SETTLE_S)
        pasted = send_chord(backend, chord)

        landed = wait_until_pasted(backend, confirm, before)
        if landed is None:
            # Nothing to observe -- the app publishes no text, or this is
            # Windows. Keep the guard up a little longer so the hotkey hook
            # sees and drops our own injected key events before it starts
            # listening again, and assume the paste worked, as before.
            backend.sleep(POST_PASTE_S)
            return pasted
        # We watched it happen, or watched it not happen. Either way the
        # injected events are long delivered by now.
        return pasted and landed
    finally:
        if wrote_clipboard:
            # Off the critical path: the dictation is done, and the wait for
            # the receiving app to take the text happens on its own thread.
            schedule(lambda: restore_clipboard_later(backend, saved, generation))
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
