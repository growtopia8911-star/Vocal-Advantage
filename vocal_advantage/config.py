"""Load and save ``config.json`` -- the per-machine settings file.

It lives at the repo root, is gitignored, and is created with ``DEFAULTS`` on
first run. Hand-editing it is a documented way to change the hotkey, so a bad
value in the file must warn and fall back -- never stop the app from starting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .hotkey_spec import HotkeyError, parse_hotkey

# __file__ is <repo>/vocal_advantage/config.py, so parent.parent is the repo
# root -- the same folder the user opens to edit the file by hand.
CONFIG_PATH: Path = Path(__file__).resolve().parent.parent / "config.json"

# Exactly the spec's "Config defaults" block. min_duration_s 0.4 is the first
# hallucination guard (a short tap never reaches the model); max_duration_s 300
# is the watchdog that force-stops a forgotten recording.
# "right ctrl" does not exist on a MacBook keyboard at all -- the right-hand
# side is Command, Option, arrows. Shipping it as the macOS default would hand a
# new Mac user a key they physically cannot press, and the app would look broken
# before they ever found --set-hotkey. Right Option is the closest analogue:
# present on every Mac, does nothing on its own, and maps onto the same shared
# key vocabulary ("right alt"), so one config.json stays portable between
# machines.
DEFAULT_HOTKEY: str = "right alt" if sys.platform == "darwin" else "right ctrl"

DEFAULTS: dict = {
    "hotkey": DEFAULT_HOTKEY,
    "language": "en",
    "model": "large-v3-turbo",
    "device": "auto",
    "min_duration_s": 0.4,
    "max_duration_s": 300,
}


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    """Write ``cfg`` to ``path`` as indented JSON with a trailing newline."""
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Return the settings, creating or completing the file as needed.

    - File missing: written with ``DEFAULTS`` and those defaults returned.
    - Keys missing: filled from ``DEFAULTS``.
    - Keys we do not recognise: preserved untouched.
    - Hotkey unusable: a warning on stderr and ``DEFAULTS["hotkey"]`` used for
      this run. The file itself is left alone so the user can still see what
      they typed and fix it.
    """
    if not path.exists():
        cfg = dict(DEFAULTS)
        save_config(cfg, path)
        return cfg

    stored = json.loads(path.read_text(encoding="utf-8"))

    # Defaults first, stored second: missing keys get filled in, and anything
    # in the file that we do not know about survives the merge.
    cfg = {**DEFAULTS, **stored}

    hotkey = cfg["hotkey"]
    try:
        if not isinstance(hotkey, str):
            # A hand-edit can produce a number or null; parse_hotkey only
            # promises to handle text, so reject it here in the same shape.
            raise HotkeyError(
                f'hotkey must be text such as "right ctrl", not '
                f"{type(hotkey).__name__}."
            )
        parse_hotkey(hotkey)
    except HotkeyError as exc:
        print(
            f"WARNING: {path}: {exc} "
            f"Falling back to the default hotkey {DEFAULTS['hotkey']!r} "
            f"for this run.",
            file=sys.stderr,
        )
        cfg["hotkey"] = DEFAULTS["hotkey"]

    return cfg
