"""The Windows tray menu's contract with pystray.

None of this needs a tray, an icon or a screen -- it is argument counting --
which is the whole point: the bug it exists for took the entire app down at
startup on Windows and could not be seen from the Mac, where `tray_mac` and
NSStatusItem are used instead and pystray is never imported.
"""

from __future__ import annotations

import inspect

import pytest

from vocal_advantage.tray_win import _click


def test_the_menu_callback_takes_no_more_than_two_arguments():
    """pystray's `_assert_action` accepts 0, 1 or 2 arguments and raises
    `ValueError(action)` for anything else.

    The obvious way to bind the action -- `lambda _icon=None, _item=None,
    a=action: a()` -- makes it a *third* parameter. A default does not save it:
    `inspect.signature` still counts three. This shipped, and pystray raised
    from inside `Icon.run()`, so the app printed "Ready." and then died before
    the icon ever appeared.
    """
    callback = _click(lambda: None)

    assert len(inspect.signature(callback).parameters) <= 2


def test_the_menu_callback_actually_calls_the_action():
    """Arity is not the only thing that matters: a callback with the right
    shape that does nothing would pass the test above and break every menu
    item."""
    calls = []
    callback = _click(lambda: calls.append("clicked"))

    callback(None, None)

    assert calls == ["clicked"]


def test_the_action_is_not_called_while_the_menu_is_merely_built():
    """Menu items are constructed at startup; clicking is what should run
    them. A callback invoked eagerly would quit the app while building the
    menu that offers Quit."""
    calls = []

    _click(lambda: calls.append("clicked"))

    assert calls == []


def test_pystray_itself_accepts_the_callback():
    """The rule above is pystray's, so where pystray is installed, let it be
    the judge rather than trusting this file's reading of it. Skipped on the
    Mac, which has no pystray -- exactly the gap that let the bug through."""
    pystray = pytest.importorskip("pystray")

    # Raises ValueError on a callback of the wrong shape.
    pystray.MenuItem("Move bar", _click(lambda: None))
