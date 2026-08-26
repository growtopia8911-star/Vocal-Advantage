"""Reading and writing ``config.json`` on behalf of the settings page.

No HTTP here and no browser -- paths in, plain dicts out. The server module is
a thin shell around this, which is what keeps the rules below testable at full
speed and keeps sockets out of the part that can corrupt a config file.

**Strict, where `config.py` is forgiving.** ``load_config`` warns and falls back
on a bad value, because a mistyped setting must never be the reason dictation
will not start. A save is the opposite situation: the user is sitting there
looking at the form, so a bad value is refused and said out loud, and nothing
reaches the file.

Two properties matter more than any individual setting:

* **A hand-edit is never clobbered.** ``config.json`` is documented as editable
  in a text editor, and a page that has been open for an hour holds a stale
  copy. So a save re-reads the file, applies only the fields it was actually
  given, and writes that -- never the dict the page started with.
* **Unknown keys survive.** A key from a newer version, or a stale one like
  ``live_typing``, comes back out untouched. Dropping what the page does not
  recognise is how a settings window quietly downgrades a file.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import (
    CONFIG_PATH,
    DEFAULTS,
    FLOW_BAR_POSITIONS,
    _NON_NEGATIVE_NUMBERS,
    _POSITIVE_NUMBERS,
    load_config,
    save_config,
)
from .hotkey_spec import HotkeyError, parse_hotkey


class SettingsError(ValueError):
    """A save the page asked for that must not happen. Message is user-facing."""


#: Which tier each setting sits in, and therefore how many clicks away it is.
#:
#: The organising rule from `docs/plans/2026-08-25-interface-design.md`: depth
#: is decided by *why* someone is changing a setting, not by how advanced it
#: sounds. Hands in front, machine one disclosure back, task inside a profile.
#:
#: It lives here rather than in the page so the split is data the UI is handed,
#: not a layout the UI invents -- and so the test that every key lands in
#: exactly one tier can exist at all.
TIERS: dict[str, tuple[str, ...]] = {
    # Things you change because of how you work.
    "hands": (
        "hotkey",
        "tap_threshold_s",
        "flow_bar",
        "flow_bar_always_visible",
        "flow_bar_position",
        "sounds",
        "sound_on_start",
    ),
    # Things you change because of the machine you are on.
    "machine": (
        "model",
        "device",
        "chunk_s",
        "overlap_s",
        "min_duration_s",
        "max_duration_s",
        "silence_timeout_s",
        "history",
        "timings",
    ),
    # Things you change because of what you are writing. These become a
    # profile's contents once profiles exist (gate 6); until then they are
    # global, and the page says so.
    "task": (
        "clean_speech",
        "ai_cleanup",
        "language",
        "skip_cleanup_in",
    ),
}

#: Written by dragging the bar, so it has no control and cannot be set here.
#: Refused rather than ignored: a silent no-op would look like a bug.
NO_CONTROL = frozenset({"flow_bar_point"})

_BOOLS = frozenset(
    {
        "flow_bar",
        "flow_bar_always_visible",
        "sounds",
        "sound_on_start",
        "history",
        "timings",
        "clean_speech",
        "ai_cleanup",
    }
)
#: Durations that are simply "a number of seconds, at least zero". The two
#: tighter families come from config.py so there is one definition of each.
_PLAIN_SECONDS = frozenset({"min_duration_s", "max_duration_s"})


def read_settings(path: Path = CONFIG_PATH) -> dict:
    """Everything the page needs to draw itself.

    ``load_config`` is reused deliberately: it fills missing keys, preserves
    unknown ones, and creates the file on first run. The page should see
    exactly what the app sees.
    """
    return {"values": load_config(path), "tiers": {k: list(v) for k, v in TIERS.items()}}


def write_settings(path: Path = CONFIG_PATH, updates: dict | None = None) -> dict:
    """Validate `updates`, merge them into the file, and report what moved.

    Every field is checked before anything is written, so a form with one bad
    value leaves the file exactly as it was rather than half-applying the rest.
    """
    updates = dict(updates or {})
    for key, value in updates.items():
        _check(key, value)

    # Re-read rather than trusting whatever the page was handed when it opened.
    # This is the whole reason a save is not just `save_config(page_state)`.
    current = load_config(path)
    changed = {k: v for k, v in updates.items() if current.get(k) != v}
    if not changed:
        return {}

    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    stored.update(changed)
    save_config(stored, path)
    return changed


def handle(message: object, path: Path = CONFIG_PATH) -> dict:
    """Answer one message from the settings page.

    This is the edge of the app. WKWebView hands over whatever JavaScript
    posted, so nothing arriving here is trusted and nothing here raises -- an
    exception would surface as a window that quietly stops responding, with the
    traceback printed somewhere the user is not looking.

    Every reply is ``{"ok": bool}`` plus either ``data`` or ``error``, and the
    error text is written to be shown to a person rather than logged.
    """
    try:
        # The page posts JSON text, not an object. WKWebView bridges a
        # JavaScript object to an ObjC NSDictionary proxy, which is not a
        # Python dict -- so an object arrives here failing `isinstance(.., dict)`
        # and the entire page comes back "unreadable" with no controls drawn.
        # Text has exactly one meaning on both sides of that bridge.
        if isinstance(message, (str, bytes)):
            try:
                message = json.loads(message)
            except (ValueError, TypeError):
                return _no("The settings page sent something unreadable.")
        if not isinstance(message, dict):
            return _no("The settings page sent something unreadable.")
        action = message.get("action")

        if action == "read":
            return _yes(read_settings(path))

        if action == "save":
            updates = message.get("updates")
            if not isinstance(updates, dict):
                return _no("That save had nothing in it.")
            if not updates:
                return _no("That save had nothing in it.")
            return _yes({"changed": write_settings(path, updates)})

        return _no(f"Unknown action {action!r}.")
    except SettingsError as exc:
        return _no(str(exc))
    except Exception as exc:  # noqa: BLE001 - the window must keep answering
        return _no(f"Could not save: {exc}")


def _yes(data: dict) -> dict:
    return {"ok": True, "data": data}


def _no(error: str) -> dict:
    return {"ok": False, "error": error}


def _check(key: str, value: object) -> None:
    """Raise `SettingsError` unless `value` is a usable setting for `key`."""
    if key in NO_CONTROL:
        raise SettingsError(
            f"{key} is set by dragging the bar, not from this page."
        )
    if key not in DEFAULTS:
        raise SettingsError(f"There is no setting called {key!r}.")

    if key == "hotkey":
        if not isinstance(value, str):
            raise SettingsError('The hotkey must be text, such as "right ctrl".')
        try:
            parse_hotkey(value)
        except HotkeyError as exc:
            raise SettingsError(str(exc)) from exc
        return

    if key in _BOOLS:
        if not isinstance(value, bool):
            raise SettingsError(f"{key} is on or off, not {value!r}.")
        return

    if key == "flow_bar_position":
        if value not in FLOW_BAR_POSITIONS:
            raise SettingsError(
                f"{key} must be one of {', '.join(FLOW_BAR_POSITIONS)}."
            )
        return

    if key == "skip_cleanup_in":
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise SettingsError(f"{key} must be a list of application names.")
        return

    if key in _POSITIVE_NUMBERS or key in _PLAIN_SECONDS:
        _number(key, value, minimum=None)
        return

    if key in _NON_NEGATIVE_NUMBERS:
        _number(key, value, minimum=0.0)
        return

    # model, device, language: free text the backend resolves. Refusing a name
    # this module has not heard of would mean updating it every time a model
    # ships, and `backends.choose_backend` already reports an unusable one.
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{key} must be a name, not {value!r}.")


def _number(key: str, value: object, *, minimum: float | None) -> None:
    # bool is an int in Python, and True would sail through as 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{key} must be a number, not {value!r}.")
    if minimum is None and value <= 0:
        raise SettingsError(f"{key} must be greater than zero.")
    if minimum is not None and value < minimum:
        raise SettingsError(f"{key} cannot be negative.")
