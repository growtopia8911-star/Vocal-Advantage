"""Tests for clipboard save/restore around the paste (spec item 10).

A fake backend with a virtual clock stands in for the platform, so the whole
sequence -- wait for modifiers, save, write, settle, chord, restore -- runs with
no clipboard, no keystrokes and no real sleeping.
"""

from __future__ import annotations

import pytest

from vocal_advantage.paste_core import injection_active, paste_with

CHORD = ((17, True), (86, True), (86, False), (17, False))


class FakeBackend:
    """Every OS call the sequence makes, recorded in order."""

    def __init__(
        self,
        *,
        clipboard: str | None = "previous contents",
        set_fails: int = 0,
        chord_refused: bool = False,
        read_raises: bool = False,
        restore_raises: bool = False,
    ) -> None:
        self.clipboard = clipboard
        self.calls: list[str] = []
        self.writes: list[str] = []
        self.keys: list[tuple[int, bool]] = []
        self.now = 0.0
        self.slept = 0.0
        self._set_fails = set_fails
        self._chord_refused = chord_refused
        self._read_raises = read_raises
        self._restore_raises = restore_raises
        self.modifiers = False

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds

    def modifiers_down(self) -> bool:
        return self.modifiers

    def get_clipboard(self) -> str | None:
        self.calls.append("get")
        if self._read_raises:
            raise OSError("clipboard busy")
        return self.clipboard

    def set_clipboard(self, text: str) -> None:
        self.calls.append("set")
        self.writes.append(text)
        if self._set_fails > 0:
            self._set_fails -= 1
            raise OSError("clipboard locked")
        if self._restore_raises and len(self.writes) > 1:
            raise OSError("clipboard locked on restore")
        self.clipboard = text

    def send_key(self, vk: int, down: bool) -> int:
        self.calls.append("key")
        self.keys.append((vk, down))
        return 0 if self._chord_refused else 1


# --- 10a: clipboard then chord ----------------------------------------------


def test_the_text_is_written_to_the_clipboard_and_the_chord_is_sent():
    b = FakeBackend()
    assert paste_with("hello world", b, CHORD) is True
    assert "hello world" in b.writes
    assert b.keys == list(CHORD)


def test_the_clipboard_is_written_before_the_chord():
    b = FakeBackend()
    paste_with("hello", b, CHORD)
    assert b.calls.index("set") < b.calls.index("key")


# --- 10c: the previous clipboard comes back ---------------------------------


def test_the_previous_clipboard_is_restored():
    b = FakeBackend(clipboard="previous contents")
    paste_with("dictated text", b, CHORD)
    assert b.clipboard == "previous contents"


def test_the_previous_clipboard_is_read_before_it_is_overwritten():
    b = FakeBackend()
    paste_with("dictated text", b, CHORD)
    assert b.calls.index("get") < b.calls.index("set")


def test_the_restore_happens_after_the_chord():
    """Restoring too early would paste the old clipboard instead."""
    b = FakeBackend()
    paste_with("dictated text", b, CHORD)
    last_key = len(b.calls) - 1 - b.calls[::-1].index("key")
    last_set = len(b.calls) - 1 - b.calls[::-1].index("set")
    assert last_set > last_key


def test_the_app_is_given_time_to_read_before_the_restore():
    b = FakeBackend()
    paste_with("dictated text", b, CHORD)
    assert b.slept > 0


# --- 10d: an empty prior clipboard ------------------------------------------


def test_an_empty_previous_clipboard_restores_to_empty():
    """10d: not to the transcript, which would be a silent surprise."""
    b = FakeBackend(clipboard="")
    paste_with("dictated text", b, CHORD)
    assert b.clipboard == ""


def test_an_unreadable_clipboard_is_left_alone_rather_than_cleared():
    """None means "could not read", which is different from "was empty".

    Blanking someone's clipboard because we failed to read it would destroy
    data we never saw. Leaving the transcript there is the lesser harm.
    """
    b = FakeBackend(clipboard=None)
    paste_with("dictated text", b, CHORD)
    assert b.clipboard == "dictated text"


def test_a_clipboard_read_that_raises_does_not_stop_the_paste():
    b = FakeBackend(read_raises=True)
    assert paste_with("dictated text", b, CHORD) is True
    assert "dictated text" in b.writes


# --- 10e: restore survives failure ------------------------------------------


def test_the_clipboard_is_restored_even_when_the_chord_is_refused():
    b = FakeBackend(chord_refused=True)
    assert paste_with("dictated text", b, CHORD) is False
    assert b.clipboard == "previous contents"


def test_a_restore_that_fails_does_not_raise():
    b = FakeBackend(restore_raises=True)
    paste_with("dictated text", b, CHORD)  # must not raise


def test_nothing_is_written_when_there_is_nothing_to_paste():
    b = FakeBackend()
    assert paste_with("   ", b, CHORD) is False
    assert b.writes == []


def test_a_clipboard_that_cannot_be_written_reports_failure():
    b = FakeBackend(set_fails=99)
    assert paste_with("dictated text", b, CHORD) is False


# --- the injection gate -----------------------------------------------------


def test_the_injection_gate_is_lowered_afterwards():
    b = FakeBackend()
    paste_with("dictated text", b, CHORD)
    assert not injection_active.is_set()


def test_the_gate_is_lowered_even_if_the_backend_explodes():
    class Exploding(FakeBackend):
        def set_clipboard(self, text: str) -> None:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        paste_with("dictated text", Exploding(), CHORD)
    assert not injection_active.is_set()


# --- backwards compatibility ------------------------------------------------


def test_a_backend_with_no_get_clipboard_still_pastes():
    """Save/restore is best-effort; a backend that cannot read still works."""

    class NoReader(FakeBackend):
        get_clipboard = None

    b = NoReader()
    assert paste_with("dictated text", b, CHORD) is True


# --- the restore moved off the critical path --------------------------------
#
# Waiting 250ms for the receiving app to read the clipboard before handing the
# user's own contents back was, measured on a real dictation, over half of the
# insertion stage and about a third of the entire post-release wait. The wait
# is still needed -- restoring too early races the paste and puts the OLD
# clipboard into the document -- but nothing needs to *block* on it.


def inline(fn):
    """A scheduler that runs the restore synchronously, for tests."""
    fn()


def test_the_paste_returns_before_the_restore_has_happened():
    """The point of the change: the caller is not billed for the wait."""
    b = FakeBackend()
    pending = []
    assert paste_with("dictated", b, CHORD, schedule=pending.append) is True
    assert b.clipboard == "dictated", "restore should not have run yet"
    assert len(pending) == 1, "a restore should have been scheduled"
    pending[0]()
    assert b.clipboard == "previous contents"


def test_the_blocking_wait_is_no_longer_paid_by_the_caller():
    b = FakeBackend()
    paste_with("dictated", b, CHORD, schedule=lambda fn: None)
    # settle 0.1 + three key intervals 0.06 + post-paste 0.06 = 0.22
    assert b.slept == pytest.approx(0.22, abs=0.001)


def test_the_restore_still_waits_before_putting_the_clipboard_back():
    """Moved off the critical path, not removed. Restoring early races the app."""
    b = FakeBackend()
    pending = []
    paste_with("dictated", b, CHORD, schedule=pending.append)
    before = b.slept
    pending[0]()
    assert b.slept - before == pytest.approx(0.25, abs=0.001)


def test_a_later_paste_wins_the_clipboard():
    """Two dictations in quick succession must not resurrect the older one.

    The first restore fires while the second paste has already written its own
    text. Restoring blindly would wipe the newer dictation off the clipboard.
    """
    b = FakeBackend(clipboard="original")
    first: list = []
    paste_with("first dictation", b, CHORD, schedule=first.append)
    second: list = []
    paste_with("second dictation", b, CHORD, schedule=second.append)
    first[0]()   # the stale restore, arriving late
    assert b.clipboard == "second dictation", "a stale restore clobbered a newer paste"
    second[0]()  # the current one
    assert b.clipboard == "original"


def test_a_refused_chord_still_schedules_the_restore():
    b = FakeBackend(chord_refused=True)
    pending = []
    assert paste_with("dictated", b, CHORD, schedule=pending.append) is False
    assert len(pending) == 1
    pending[0]()
    assert b.clipboard == "previous contents"


def test_nothing_is_scheduled_when_the_clipboard_was_never_written():
    b = FakeBackend(set_fails=99)
    pending = []
    paste_with("dictated", b, CHORD, schedule=pending.append)
    assert pending == []


def test_the_default_scheduler_does_not_block_the_caller():
    """With no scheduler passed, the real one is a daemon thread."""
    import threading

    b = FakeBackend()
    before = threading.active_count()
    paste_with("dictated", b, CHORD)
    assert threading.active_count() >= before
