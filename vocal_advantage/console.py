"""Printing that survives having no console.

Once the app has a tray icon it is launched with no terminal: `pythonw.exe` on
Windows, a `.app` bundle on macOS. In both, ``sys.stdout`` and ``sys.stderr``
are ``None`` -- not closed files, ``None`` -- and a bare ``print()`` raises
``AttributeError: 'NoneType' object has no attribute 'write'``.

That kills the app at launch, on the first status line, before the hotkey is
ever hooked, and the only symptom is that nothing happens. So every message in
this project goes through here.

It lives in its own module rather than in ``main.py`` so ``config.py`` can use
it without an import cycle; ``main.py`` re-exports both names.

`python -m vocal_advantage` still prints to the console exactly as before.
"""

from __future__ import annotations

import sys


def say(message: str, *, error: bool = False) -> None:
    """Print one line, or do nothing at all if there is nowhere to print it.

    ``sys.stdout`` is looked up on every call, never bound at import: the
    stream can be replaced after this module loads -- pytest's capture does
    exactly that -- and caching it would send output somewhere nobody is
    reading.
    """
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        stream.write(f"{message}\n")
    except Exception:  # noqa: BLE001 - a broken pipe is not worth an app for
        return
    # Separately guarded: a stream can accept writes and still have no flush
    # (or fail in it), and losing the flush must not lose the write above.
    try:
        stream.flush()
    except Exception:  # noqa: BLE001
        pass


def warn(message: str) -> None:
    """Say something on stderr. The same no-console guarantees apply."""
    say(message, error=True)
