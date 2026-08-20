"""Tests for the portable half of the key-event logic.

`normalise_key_name`, `spec_key_for`, `Edge` and `EdgeDetector` contain no
Windows at all -- they were merely stranded in `hotkey_win.py`. They live here
so `hotkey_mac` can share them rather than keep a second copy of the trickiest
logic in the project.

The behaviour itself is covered exhaustively by tests/test_hotkey_win.py, which
still imports these names through `hotkey_win`. What is pinned here is the split
itself: that the portable layer really is portable, and that moving it did not
break the Windows side.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PORTABILITY_SCRIPT = """
import sys
import vocal_advantage.hotkey_events as ev

# The whole point of the split: this module must stand alone.
assert "vocal_advantage.hotkey_win" not in sys.modules, (
    "hotkey_events dragged in the Windows module: %r" % (
        sorted(n for n in sys.modules if "vocal_advantage" in n),
    )
)
assert "ctypes" not in dir(ev), "no Windows bindings belong in the portable half"

# And it has to actually work, not just import.
d = ev.EdgeDetector.__init__ is not None
assert ev.normalise_key_name("Right Ctrl") == "right ctrl"
assert ev.spec_key_for("ctrl", {"left ctrl"}) == "left ctrl"
print("OK")
"""


def test_the_portable_layer_does_not_pull_in_the_windows_module():
    result = subprocess.run(
        [sys.executable, "-c", PORTABILITY_SCRIPT],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_hotkey_win_still_re_exports_every_moved_name():
    """Windows must not notice the tidying.

    tests/test_hotkey_win.py imports all of these from `hotkey_win` and is left
    untouched by the move; this asserts they are the very same objects, not
    copies that could drift apart.
    """
    from vocal_advantage import hotkey_events, hotkey_win

    for name in ("normalise_key_name", "spec_key_for", "Edge", "EdgeDetector"):
        assert getattr(hotkey_win, name) is getattr(hotkey_events, name), name


def test_the_windows_only_names_stayed_behind():
    """VK codes and GetAsyncKeyState genuinely are Windows; they do not move."""
    from vocal_advantage import hotkey_events, hotkey_win

    assert hasattr(hotkey_win, "VK_CODES")
    assert hasattr(hotkey_win, "read_pressed_keys")
    assert not hasattr(hotkey_events, "VK_CODES")
    assert not hasattr(hotkey_events, "read_pressed_keys")
