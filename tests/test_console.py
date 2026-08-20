"""Console output that survives having no console.

The app's whole premise once it has a tray icon is that it launches with no
terminal: `pythonw.exe` on Windows, a `.app` bundle on macOS. In both,
``sys.stdout`` and ``sys.stderr`` are ``None``, and a bare ``print()`` raises
``AttributeError: 'NoneType' object has no attribute 'write'``.

That is not a cosmetic problem. It happens on the first status line, before the
hotkey is ever hooked, so the app dies at launch and the only symptom is that
nothing appears. Every one of these tests is guarding that.
"""

from __future__ import annotations

import io

import pytest

from vocal_advantage.console import say, warn


class Exploding:
    """A stream that fails on use, like a closed pipe."""

    def write(self, _text):
        raise OSError("the pipe went away")

    def flush(self):
        raise OSError("the pipe went away")


def test_say_writes_to_stdout(capsys):
    say("ready")
    assert capsys.readouterr().out == "ready\n"


def test_warn_writes_to_stderr(capsys):
    warn("that did not work")
    captured = capsys.readouterr()
    assert captured.err == "that did not work\n"
    assert captured.out == ""


def test_say_with_error_writes_to_stderr(capsys):
    say("that did not work", error=True)
    assert capsys.readouterr().err == "that did not work\n"


def test_say_survives_stdout_being_none(monkeypatch):
    # pythonw.exe and a launched .app bundle, exactly.
    monkeypatch.setattr("sys.stdout", None)
    say("ready")


def test_warn_survives_stderr_being_none(monkeypatch):
    monkeypatch.setattr("sys.stderr", None)
    warn("that did not work")


def test_say_survives_both_streams_being_none(monkeypatch):
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    say("ready")
    say("bad", error=True)
    warn("bad")


def test_say_survives_a_stream_that_raises(monkeypatch):
    # A closed pipe: the app must not die because something stopped reading.
    monkeypatch.setattr("sys.stdout", Exploding())
    say("ready")


def test_say_survives_a_stream_missing_flush(monkeypatch):
    class NoFlush:
        def __init__(self):
            self.text = ""

        def write(self, text):
            self.text += text

    stream = NoFlush()
    monkeypatch.setattr("sys.stdout", stream)
    say("ready")
    assert stream.text == "ready\n"


def test_say_is_looked_up_at_call_time(monkeypatch):
    # Binding sys.stdout at import would defeat every test above, and would
    # also break pytest's own capture. This is what proves it is not cached.
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", first)
    say("one")
    monkeypatch.setattr("sys.stdout", second)
    say("two")
    assert first.getvalue() == "one\n"
    assert second.getvalue() == "two\n"


def test_say_flushes_so_output_is_not_lost_on_exit():
    class Counting(io.StringIO):
        flushes = 0

        def flush(self):
            type(self).flushes += 1

    stream = Counting()
    import sys

    original, sys.stdout = sys.stdout, stream
    try:
        say("ready")
    finally:
        sys.stdout = original
    assert Counting.flushes == 1


@pytest.mark.parametrize("message", ["", "   ", "ünïcödé", "a" * 5000])
def test_say_handles_awkward_messages(message, capsys):
    say(message)
    assert capsys.readouterr().out == message + "\n"
