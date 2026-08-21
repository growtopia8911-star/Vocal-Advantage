"""Load and save ``config.json`` -- the per-machine settings file.

It lives at the repo root, is gitignored, and is created with ``DEFAULTS`` on
first run. Hand-editing it is a documented way to change the hotkey, so a bad
value in the file must warn and fall back -- never stop the app from starting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .console import warn
from .frontmost import DEFAULT_SKIP_CLEANUP_IN
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
    # `small`, not `base`, because it was measured: 9.9% word error against
    # base's 15.4% on eight known sentences, and `medium` was no better typed
    # while being 3x slower. See "Accuracy, finally measured" in the project
    # note.
    #
    # This lives here rather than in a config.json because DEFAULTS is the
    # only place a decision can travel between the Mac and the PC -- the
    # config file is gitignored, which is why the two machines silently ran
    # different models for a day.
    "model": "small",
    "device": "auto",
    "min_duration_s": 0.4,
    "max_duration_s": 300,
    # Filler words and stutters are dropped before anything is typed. Set
    # false for the raw transcript. Not in the original spec: added once a
    # real dictation came back as "Um, so I think we should ship it on
    # Friday." -- Whisper punctuates well but keeps every "um".
    # macOS only -- Windows types the whole transcript on key release and has
    # no live preview to switch off. True keeps the behaviour every existing
    # config already had; set false to keep a bigger model affordable, since
    # each live pass re-transcribes the sentence from the start.
    "live_typing": True,
    "clean_speech": True,
    # An extra pass through a local Ollama model (qwen3:4b) that collapses
    # self-corrections and breaks run-on speech into sentences. Off by
    # default: it needs Ollama running, it costs up to 6s after you release
    # the key, and it pauses the word-by-word preview -- the model can only
    # clean a finished sentence. Filler removal above does not depend on it.
    "ai_cleanup": False,
    # The always-on-screen waveform pill. Set false to keep the tray icon and
    # the hotkey with no overlay at all; dictation is unaffected either way.
    "flow_bar": True,
    # Where it sits. See FLOW_BAR_POSITIONS.
    "flow_bar_position": "bottom-centre",
    # Where it was last dragged to, as [centre_x, bottom_y] in screen
    # coordinates, or null to use flow_bar_position instead. Written by "Move
    # bar" in the tray menu; delete it by hand to go back to a preset.
    #
    # Centre-x rather than left-x on purpose: the pill widens to show a
    # message, and anchoring the centre keeps it growing evenly in both
    # directions instead of walking sideways every time one appears.
    "flow_bar_point": None,
    # Applications where the cleanup pass is left switched off. Filler removal
    # is right for prose and wrong for a shell -- it will happily turn a
    # command into something that does not run, and you find out when you press
    # Enter. Matched as case-insensitive substrings, so one list works on both
    # machines: "terminal" catches macOS Terminal and Windows Terminal alike.
    #
    # The personal dictionary still applies here. Skipping *cleanup* is not the
    # same as agreeing to spell your own name wrong.
    "skip_cleanup_in": list(DEFAULT_SKIP_CLEANUP_IN),
    # Short generated tones on finishing and on failure, so you know what
    # happened without looking at the screen.
    "sounds": True,
    # Separate from "sounds" because it carries a risk the others do not: it
    # plays while the microphone is open, so on speakers it goes back into the
    # recording and Whisper transcribes something for it. Safe on headphones.
    "sound_on_start": False,
    # Every dictation appended to logs/history.jsonl, so a transcript that
    # pasted into the wrong window is still recoverable. Never leaves the
    # machine; gitignored.
    "history": True,
}

#: The positions the Flow Bar understands. "bottom-center" is accepted as an
#: alias and normalised to the British spelling the rest of the project uses --
#: rejecting a spelling would be a papercut that taught nobody anything.
FLOW_BAR_POSITIONS: tuple[str, ...] = (
    "bottom-centre", "bottom-left", "bottom-right",
)
_POSITION_ALIASES = {"bottom-center": "bottom-centre"}


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
    - Flow Bar settings unusable: same treatment. A mistyped overlay position
      must never be the reason dictation will not start.
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
        warn(
            f"WARNING: {path}: {exc} "
            f"Falling back to the default hotkey {DEFAULTS['hotkey']!r} "
            f"for this run."
        )
        cfg["hotkey"] = DEFAULTS["hotkey"]

    cfg["flow_bar"] = _checked_bool("flow_bar", cfg["flow_bar"], path)
    cfg["flow_bar_position"] = _checked_position(cfg["flow_bar_position"], path)
    cfg["flow_bar_point"] = _checked_point(cfg["flow_bar_point"], path)
    cfg["skip_cleanup_in"] = _checked_skip_list(cfg["skip_cleanup_in"], path)
    for key in ("sounds", "sound_on_start", "history"):
        cfg[key] = _checked_bool(key, cfg[key], path)

    return cfg


def _checked_bool(key: str, value: object, path: Path) -> bool:
    """A real bool, or the default with a warning.

    Deliberately not ``bool(value)``: that reads the string "false" as True,
    which is the mistake someone hand-editing JSON is most likely to make.
    """
    if isinstance(value, bool):
        return value
    warn(
        f"WARNING: {path}: {key} must be true or false, not {value!r}. "
        f"Using {DEFAULTS[key]!r} for this run."
    )
    return DEFAULTS[key]


def _checked_skip_list(value: object, path: Path) -> list:
    """A list of text, or the default with a warning.

    An empty list is legitimate and means "always clean", so it is passed
    through rather than treated as missing.
    """
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    warn(
        f"WARNING: {path}: skip_cleanup_in should be a list of text, not "
        f"{value!r}. Using the defaults for this run."
    )
    return list(DEFAULTS["skip_cleanup_in"])


def _checked_point(value: object, path: Path) -> list | None:
    """A pair of numbers, or None with a warning.

    None is the normal state, not an error: it means "use flow_bar_position".
    A monitor that has since been unplugged can leave coordinates pointing off
    every screen, so `flowbar_mac` clamps them at use rather than here -- the
    numbers are still valid, they are just no longer reachable.
    """
    if value is None:
        return None
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        # bool is an int subclass, and [true, false] should not read as a point.
        and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in value)
    ):
        return [float(value[0]), float(value[1])]
    warn(
        f"WARNING: {path}: flow_bar_point should be [x, y] or null, not "
        f"{value!r}. Using flow_bar_position for this run."
    )
    return None


def _checked_position(value: object, path: Path) -> str:
    """A known position, or the default with a warning."""
    if isinstance(value, str):
        normalised = _POSITION_ALIASES.get(value.strip().lower(), value.strip().lower())
        if normalised in FLOW_BAR_POSITIONS:
            return normalised
    warn(
        f"WARNING: {path}: flow_bar_position {value!r} is not one of "
        f"{', '.join(FLOW_BAR_POSITIONS)}. "
        f"Using {DEFAULTS['flow_bar_position']!r} for this run."
    )
    return DEFAULTS["flow_bar_position"]
