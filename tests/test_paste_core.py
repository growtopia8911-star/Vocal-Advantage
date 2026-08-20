"""Tests for the portable half of the paste sequence.

The order and the delays -- wait for held modifiers, retry the clipboard, let it
settle, send the chord, hold the gate a little longer -- are identical on
Windows and macOS. Only the backend and the chord differ. This module holds the
sequence; `paste_win` and `paste_mac` supply the platform.

The sequence's behaviour is covered exhaustively by tests/test_paste_win.py.
What is pinned here is the split: that the portable half really is portable, and
that both platforms share one injection flag rather than two that can disagree.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PORTABILITY_SCRIPT = """
import sys
import vocal_advantage.paste_core as core

for forbidden in ("vocal_advantage.paste_win", "vocal_advantage.paste_mac"):
    assert forbidden not in sys.modules, "paste_core dragged in %s" % forbidden
assert core.CLIPBOARD_ATTEMPTS == 5
print("OK")
"""


def test_the_portable_sequence_does_not_pull_in_either_platform():
    result = subprocess.run(
        [sys.executable, "-c", PORTABILITY_SCRIPT],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_both_platforms_share_one_injection_flag():
    """Two flags would be a silent disaster.

    The key hook checks this object to know it is looking at our own injected
    keystrokes. If the paste module set one flag and the hook watched another,
    the hook would react to our own paste as though the user had typed it.
    """
    from vocal_advantage import paste_core, paste_win

    assert isinstance(paste_core.injection_active, threading.Event)
    assert paste_win.injection_active is paste_core.injection_active


def test_the_timings_live_in_one_place():
    from vocal_advantage import paste_core, paste_win

    for name in (
        "MODIFIER_WAIT_S", "MODIFIER_POLL_S", "CLIPBOARD_ATTEMPTS",
        "CLIPBOARD_RETRY_S", "CLIPBOARD_SETTLE_S", "KEY_INTERVAL_S", "POST_PASTE_S",
    ):
        assert getattr(paste_win, name) == getattr(paste_core, name), name
