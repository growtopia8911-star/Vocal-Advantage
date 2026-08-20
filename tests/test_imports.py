"""Every module must import on whichever machine this is running on.

This is the cheapest possible guard against the most expensive cross-platform
mistake in this project: a module that reaches for a platform-only library at
import time. It costs nothing to write and it fails loudly on the machine that
cannot cope, which is exactly the machine you are not sitting at.

The project is full of imports that only exist on one side -- `Quartz`, `AppKit`
and `objc` on macOS, `keyboard`, `pywin32` and `pystray` on Windows -- and every
module holding one guards it, so the *portable* half still loads. That guarding
is easy to write and easy to forget, and forgetting it is invisible on the
machine you wrote it on.

Two specific ways it has already tried to go wrong here:

* `import keyboard` on macOS does not raise, it **aborts the interpreter**
  (SIGABRT, exit 134) from a CoreFoundation assertion. No `except` can catch a
  C-level abort, so the guard has to be on `sys.platform` before the import,
  never a try/except around it. That is why `keyboard` is a win32-only
  dependency rather than merely an optional one.
* `ctypes.wintypes` raises on anything that is not Windows, so every structure
  built from it has to live inside a platform check.

Run under CI on both windows-latest and macos-latest, which is the point: a
change made on the Mac that breaks the Windows import surface fails within a
couple of minutes rather than the next time the PC is switched on.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import vocal_advantage

#: Nothing is excluded. If a module cannot be imported on one of the two
#: supported platforms, that is the bug this file exists to find.
MODULES = sorted(
    name
    for _, name, _ in pkgutil.iter_modules(
        vocal_advantage.__path__, prefix="vocal_advantage."
    )
)


def test_the_package_has_modules_to_check():
    # Guards the guard: a broken discovery expression would make every test
    # below vacuously pass and this file would protect nothing.
    assert len(MODULES) > 10


@pytest.mark.parametrize("module_name", MODULES)
def test_every_module_imports_on_this_platform(module_name):
    importlib.import_module(module_name)


@pytest.mark.parametrize(
    "module_name",
    [
        # The platform-specific pairs. Both halves of each must import on both
        # machines, because main.platform_modules() is the only place allowed
        # to choose, and it can only choose between things that exist.
        "vocal_advantage.flowbar_mac",
        "vocal_advantage.flowbar_win",
        "vocal_advantage.tray_mac",
        "vocal_advantage.tray_win",
        "vocal_advantage.hotkey_mac",
        "vocal_advantage.hotkey_win",
        "vocal_advantage.paste_mac",
        "vocal_advantage.paste_win",
    ],
)
def test_both_halves_of_every_platform_pair_import(module_name):
    importlib.import_module(module_name)


def test_importing_main_does_not_need_a_gui():
    # main.py is imported by the tests constantly. If it ever pulled in AppKit
    # or pystray at module level it would stop importing on the other machine,
    # and every test file that touches it would fail at collection.
    importlib.import_module("vocal_advantage.main")
