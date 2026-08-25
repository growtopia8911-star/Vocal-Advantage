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
