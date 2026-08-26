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
    # How long the hotkey must be held before a release means "stop" rather
    # than "keep going". Below this a press-and-release toggles recording on,
    # and a second press turns it off; at or above it the key behaves as
    # push-to-talk. 0.3s is a claim about a person's hands rather than a fact
    # about the software, which is exactly why it is a setting.
    "tap_threshold_s": 0.3,
    # Trailing silence that auto-stops a recording, so a toggle left on by
    # accident does not run until the max-duration watchdog notices. 0 disables
    # it. Silence before you have said anything does not count -- pressing the
    # key and thinking is normal.
    "silence_timeout_s": 2.5,
    # The rolling window transcribed while you are still speaking, and how far
    # each window reaches back into the one before it. The overlap is what
    # stops a word landing on a boundary and being heard as half a word twice;
    # see chunker.py.
    #
    # 15s, not the 2s this shipped with, and the reason is measured. Whisper
    # uses up to 30 seconds of context, so short windows hear worse: on the
    # eight clips in tests/fixtures/accuracy, 2s windows score 16.1% word error
    # against 9.4% for a single pass over the whole utterance. That is a large
    # penalty.
    #
    # It was worth paying only while transcription was slow enough that doing
    # it after the key release hurt. On Metal it is not: `small` runs at 0.06x
    # real time, so a five-second utterance costs 0.3s to transcribe in one
    # go -- about what the chunked path costs anyway, because that still has a
    # final window to do. Chunking was solving a problem the GPU already
    # solved.
    #
    # So: a window long enough that ordinary dictation never reaches it and
    # gets whole-utterance accuracy, but short enough that a very long one
    # still has its latency bounded. Lower it if you dictate on a slow CPU,
    # where a whole-utterance pass after the key release is genuinely felt.
    "chunk_s": 15.0,
    "overlap_s": 0.25,
    # Filler words and stutters are dropped before anything is typed. Set
    # false for the raw transcript. Not in the original spec: added once a
    # real dictation came back as "Um, so I think we should ship it on
    # Friday." -- Whisper punctuates well but keeps every "um".
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
    # Keep the bar on screen even when nothing is happening. Off by default:
    # the bar appears while you dictate and is gone the rest of the time. On,
    # it rests dimmed in its usual place, the way it did before 2026-08-25.
    "flow_bar_always_visible": False,
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
    # Print the per-stage millisecond breakdown after every dictation. On by
    # default: it is five lines, and "it felt slow" is not diagnosable without
    # them.
    "timings": True,
}

#: Settings that must be a positive number. Zero is rejected for all of them:
#: a zero chunk length divides by zero, and a zero tap threshold would make
#: every press a hold and put the toggle out of reach.
_POSITIVE_NUMBERS: tuple[str, ...] = ("tap_threshold_s", "chunk_s")
#: Settings that must be a number of zero or more. Zero is meaningful here --
#: no silence watchdog, and no overlap between windows.
_NON_NEGATIVE_NUMBERS: tuple[str, ...] = ("silence_timeout_s", "overlap_s")

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
    cfg["flow_bar_always_visible"] = _checked_bool(
        "flow_bar_always_visible", cfg["flow_bar_always_visible"], path
    )
    cfg["flow_bar_position"] = _checked_position(cfg["flow_bar_position"], path)
    cfg["flow_bar_point"] = _checked_point(cfg["flow_bar_point"], path)
    cfg["skip_cleanup_in"] = _checked_skip_list(cfg["skip_cleanup_in"], path)
    for key in ("sounds", "sound_on_start", "history", "timings"):
        cfg[key] = _checked_bool(key, cfg[key], path)
    for key in _POSITIVE_NUMBERS:
        cfg[key] = _checked_number(key, cfg[key], path, minimum=None)
    for key in _NON_NEGATIVE_NUMBERS:
        cfg[key] = _checked_number(key, cfg[key], path, minimum=0.0)
    cfg["overlap_s"] = _checked_overlap(cfg["overlap_s"], cfg["chunk_s"], path)

    return cfg


def _checked_number(key: str, value: object, path: Path, *, minimum: float | None):
    """A usable number, or the default with a warning.

    ``minimum=None`` means "must be above zero"; ``minimum=0.0`` means "zero or
    more". bool is excluded explicitly because it is an int subclass in Python,
    and `"chunk_s": true` should be rejected rather than read as one second.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        warn(
            f"WARNING: {path}: {key} must be a number, not {value!r}. "
            f"Using {DEFAULTS[key]!r} for this run."
        )
        return float(DEFAULTS[key])
    number = float(value)
    if minimum is None and number <= 0:
        warn(
            f"WARNING: {path}: {key} must be greater than zero, not {value!r}. "
            f"Using {DEFAULTS[key]!r} for this run."
        )
        return float(DEFAULTS[key])
    if minimum is not None and number < minimum:
        warn(
            f"WARNING: {path}: {key} cannot be negative. "
            f"Using {DEFAULTS[key]!r} for this run."
        )
        return float(DEFAULTS[key])
    return number


def _checked_overlap(overlap: float, chunk: float, path: Path) -> float:
    """An overlap shorter than the chunk it sits inside.

    An overlap as long as the window would mean every window contained the
    previous one whole, and the chunker's cursor would never advance through
    the audio -- the same two seconds transcribed over and over.
    """
    if overlap < chunk:
        return overlap
    warn(
        f"WARNING: {path}: overlap_s ({overlap}) must be shorter than chunk_s "
        f"({chunk}). Using {DEFAULTS['overlap_s']!r} for this run."
    )
    return float(DEFAULTS["overlap_s"])


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
