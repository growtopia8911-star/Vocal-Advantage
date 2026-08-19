"""Parse, validate and normalise the hold-to-talk hotkey string.

Pure logic -- no Windows APIs, no keyboard hook, no threads. The one outside
dependency is the ``keyboard`` library's *name table*, which SPEC makes the
authority on what counts as a real key name ("a ``keyboard``-library key name,
or several joined by ``+``").
"""

from __future__ import annotations

from dataclasses import dataclass


class HotkeyError(ValueError):
    """A hotkey string we refuse to use. The message is shown to the user."""


# Canonical modifier names -- exactly the ``keyboard`` library's
# ``all_modifiers`` set, duplicated here as a literal so that
# ``is_modifier_only`` -- which the controller asks on every keypress during
# recording -- never triggers the lazy ``import keyboard`` down in
# ``_canonical``, and so ``HotkeySpec`` stays usable on a machine where
# ``keyboard`` cannot load. ``test_modifiers_matches_the_keyboard_library``
# keeps the two in step if the library is ever upgraded.
MODIFIERS: frozenset[str] = frozenset(
    {
        "alt",
        "alt gr",
        "ctrl",
        "shift",
        "windows",
        "left alt",
        "left ctrl",
        "left shift",
        "left windows",
        "right alt",
        "right ctrl",
        "right shift",
        "right windows",
    }
)

_WIN_REASON = (
    "letting it go opens the Start menu every single time -- pair it with "
    "another key instead, like ctrl+win"
)
_CAPS_REASON = (
    "Caps Lock only works as a hotkey if the app swallows the keypress, and "
    "this app never swallows keypresses"
)

#: Key name -> plain-English reason we refuse it. Keyed by the *canonical* name,
#: because that is what the parser produces: ``keyboard.normalize_name`` turns
#: "win" and "cmd" into "windows", and "capslock" into "caps lock".
BANNED: dict[str, str] = {
    "windows": _WIN_REASON,
    "left windows": _WIN_REASON,
    "right windows": _WIN_REASON,
    "caps lock": _CAPS_REASON,
}

# Caps Lock is refused anywhere it appears; a Windows key is refused only when
# it is the entire hotkey -- SPEC's table says bare ``win`` is out but
# ``ctrl+win`` is fine.
_BANNED_ANYWHERE = frozenset({"caps lock"})
_BANNED_ALONE = frozenset({"windows", "left windows", "right windows"})

# Display order: modifiers first in the order people say them, then everything
# else alphabetically. Keeps ``str(spec)`` stable even though ``keys`` is a set.
_MODIFIER_ORDER = (
    "ctrl",
    "left ctrl",
    "right ctrl",
    "alt",
    "left alt",
    "right alt",
    "alt gr",
    "shift",
    "left shift",
    "right shift",
    "windows",
    "left windows",
    "right windows",
)

# The library's canonical name for the Windows key is "windows"; SPEC and the
# README spell it "Win", so that is what we show. Display form only -- never
# compare these against key-event names.
_DISPLAY_OVERRIDES = {
    "windows": "Win",
    "left windows": "Left Win",
    "right windows": "Right Win",
}


def _display(name: str) -> str:
    return _DISPLAY_OVERRIDES.get(name, name.title())


def _sort_key(name: str) -> tuple[int, int, str]:
    if name in _MODIFIER_ORDER:
        return (0, _MODIFIER_ORDER.index(name), name)
    return (1, 0, name)


@dataclass(frozen=True)
class HotkeySpec:
    """A validated hold-to-talk hotkey: one key, or a set held together."""

    keys: frozenset[str]

    def __post_init__(self) -> None:
        if isinstance(self.keys, str):
            raise TypeError(
                "HotkeySpec.keys is a set of key names, not a string "
                "(a string would be split into single characters)"
            )
        if not isinstance(self.keys, frozenset):
            object.__setattr__(self, "keys", frozenset(self.keys))

    def __str__(self) -> str:
        return " + ".join(_display(k) for k in sorted(self.keys, key=_sort_key))

    @property
    def is_modifier_only(self) -> bool:
        """True when the hotkey CONTAINS at least one bare modifier key.

        SPEC, state machine: "Cancel-on-other-key applies only when the hotkey
        is (or contains) a bare modifier." So this is an ``any``, not an
        ``all``: with a modifier anywhere in the combo, a stray keypress means
        the user was typing a shortcut (Right Ctrl+C), not dictating, and the
        recording is cancelled. ``ctrl+alt+space`` and ``ctrl+f8`` are
        therefore True; ``f8`` and ``f8+space`` have no modifier at all, so the
        rule is off for them and typing while dictating is allowed.

        The name is kept from the published contract -- read it as "this is a
        modifier hotkey", not "every key in it is a modifier". An empty set
        contains no modifier, so it is False and cancelling stays off.
        """
        return any(k in MODIFIERS for k in self.keys)


def _canonical(cleaned: str, original: str) -> str:
    """Return the library's canonical name for one key, or raise HotkeyError.

    The library offers two candidate checks:

    * ``keyboard.parse_hotkey(text)`` -- splits on ``+`` *and* on ``,`` (it
      supports key *sequences* like ``"ctrl+c, ctrl+v"``, meaningless for
      hold-to-talk) and returns scan codes, throwing the names away.
    * ``keyboard.key_to_scan_codes(name)`` -- validates ONE name against the
      table and raises ``ValueError`` if it is not there.

    We split on ``+`` ourselves and use ``key_to_scan_codes`` so we can name the
    key that was wrong, and so we keep the canonical *name* -- which is the
    vocabulary the key hook reports back to us ("right ctrl", "left windows").

    Neither call installs a keyboard hook: the library's listener thread only
    starts when a handler is registered, so this is safe on a test runner. The
    import is deliberately lazy -- importing ``keyboard`` builds Win32 key
    tables and needs root on Linux/macOS, and modules that only want
    ``HotkeySpec`` (the controller, the config loader) must stay importable.
    """
    import keyboard

    name = keyboard.normalize_name(cleaned)
    try:
        keyboard.key_to_scan_codes(name)
    except ValueError as exc:
        where = "" if cleaned == original.strip().lower() else f" (in {original!r})"
        raise HotkeyError(
            f"{cleaned!r} is not a key name this app knows{where}. Run "
            "'python -m vocal_advantage --set-hotkey' and press the key you want "
            "instead of typing its name."
        ) from exc
    return name


def parse_hotkey(text: str) -> HotkeySpec:
    """Turn a config string like ``"ctrl + Win"`` into a validated HotkeySpec."""
    if not isinstance(text, str):
        raise HotkeyError(
            f"A hotkey has to be written as text, not {type(text).__name__}."
        )

    raw = text.strip()
    if not raw:
        raise HotkeyError(
            "No hotkey given. Try something like 'right ctrl', 'f8' or 'ctrl+win'."
        )
    if "," in raw:
        raise HotkeyError(
            f"{text!r} looks like a sequence of two shortcuts. This app holds one "
            "key or one combo, so join the parts with '+' and drop the ','."
        )

    # '+' is both our separator and the name of a real key, so a lone plus is
    # taken as that key rather than split into two empty halves.
    parts = ["+"] if raw == "+" else raw.split("+")

    keys: set[str] = set()
    for part in parts:
        cleaned = " ".join(part.lower().split())
        if not cleaned:
            raise HotkeyError(f"{text!r} has an empty key in it -- check the '+' signs.")
        keys.add(_canonical(cleaned, text))

    for key in sorted(keys):
        if key in _BANNED_ANYWHERE:
            raise HotkeyError(
                f"{_display(key)} cannot be part of the hotkey: {BANNED[key]}."
            )

    if len(keys) == 1:
        only = next(iter(keys))
        if only in _BANNED_ALONE:
            raise HotkeyError(
                f"{_display(only)} cannot be the hotkey on its own: {BANNED[only]}."
            )

    return HotkeySpec(frozenset(keys))
