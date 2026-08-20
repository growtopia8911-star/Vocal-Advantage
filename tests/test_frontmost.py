"""Matching the focused application against the skip-cleanup list.

`frontmost_app()` itself needs a real desktop and is checked by hand. `matches`
is plain text handling and carries the whole decision, so it is tested here.

The design being pinned: **case-insensitive substring matching**, so one
`config.json` works on both machines. macOS reports "Terminal" and Windows
reports a path ending "WindowsTerminal.exe"; the single entry "terminal"
has to cover both, and it is why this is not an equality check on bundle ids
or exe names.
"""

from __future__ import annotations

import pytest

from vocal_advantage.frontmost import (
    DEFAULT_SKIP_CLEANUP_IN,
    frontmost_app,
    matches,
)

MAC_TERMINAL = "Terminal com.apple.Terminal"
WIN_TERMINAL = r"C:\Program Files\WindowsApps\WindowsTerminal.exe"
MAC_VSCODE = "Code com.microsoft.VSCode"
WIN_VSCODE = r"C:\Users\growt\AppData\Local\Programs\Microsoft VS Code\Code.exe"
MAC_NOTES = "Notes com.apple.Notes"
WIN_NOTEPAD = r"C:\Windows\System32\notepad.exe"


def test_one_entry_covers_both_machines():
    # The whole point of substring matching. If this ever needs two entries,
    # config.json has stopped being portable.
    assert matches(MAC_TERMINAL, ["terminal"])
    assert matches(WIN_TERMINAL, ["terminal"])


def test_the_same_holds_for_an_editor():
    assert matches(MAC_VSCODE, ["code"])
    assert matches(WIN_VSCODE, ["code"])


def test_matching_ignores_case():
    assert matches("TERMINAL", ["terminal"])
    assert matches("terminal", ["TERMINAL"])


def test_an_app_that_is_not_listed_does_not_match():
    assert not matches(MAC_NOTES, DEFAULT_SKIP_CLEANUP_IN)
    assert not matches(WIN_NOTEPAD, DEFAULT_SKIP_CLEANUP_IN)


def test_an_unknown_app_does_not_match():
    # None means "could not tell". The safe failure is running cleanup where it
    # was not wanted, never skipping it everywhere.
    assert not matches(None, DEFAULT_SKIP_CLEANUP_IN)
    assert not matches("", DEFAULT_SKIP_CLEANUP_IN)


def test_an_empty_list_matches_nothing():
    assert not matches(MAC_TERMINAL, [])


def test_blank_entries_are_ignored():
    # An empty string is a substring of everything, so a stray "" in a
    # hand-edited list would silently switch cleanup off in every app.
    assert not matches(MAC_NOTES, ["", "   "])


def test_non_text_entries_are_ignored():
    assert matches(MAC_TERMINAL, [None, 42, "terminal"])
    assert not matches(MAC_NOTES, [None, 42])


def test_surrounding_whitespace_in_an_entry_is_forgiven():
    assert matches(MAC_TERMINAL, ["  terminal  "])


@pytest.mark.parametrize("app", [MAC_TERMINAL, WIN_TERMINAL, MAC_VSCODE, WIN_VSCODE])
def test_the_shipped_defaults_catch_the_obvious_cases(app):
    assert matches(app, DEFAULT_SKIP_CLEANUP_IN)


def test_the_defaults_do_not_catch_ordinary_writing_apps():
    # A false positive here is worse than a miss: it silently switches off
    # filler removal in the place you most want it.
    for app in (MAC_NOTES, WIN_NOTEPAD, "Mail com.apple.mail",
                "Slack com.tinyspeck.slackmacgap", "Obsidian md.obsidian"):
        assert not matches(app, DEFAULT_SKIP_CLEANUP_IN), app


def test_frontmost_app_never_raises():
    # It runs on the paste path. Whatever the desktop is doing, it answers.
    result = frontmost_app()
    assert result is None or isinstance(result, str)
