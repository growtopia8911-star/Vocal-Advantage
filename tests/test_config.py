"""Tests for vocal_advantage.config.

Every test that touches disk writes into pytest's ``tmp_path``. The real
config.json at the repo root is never read or written by this file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from vocal_advantage.config import (
    CONFIG_PATH,
    DEFAULT_HOTKEY,
    DEFAULTS,
    load_config,
    save_config,
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_config_path_is_config_json_at_the_repo_root():
    # The file sits next to the package, not inside it: the spec documents
    # hand-editing config.json as a supported way to change the hotkey.
    assert CONFIG_PATH.name == "config.json"
    assert CONFIG_PATH.parent == Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("hotkey", DEFAULT_HOTKEY),
        ("language", "en"),
        ("model", "base"),
        ("clean_speech", True),
        ("ai_cleanup", False),
        ("device", "auto"),
        ("min_duration_s", 0.4),
        ("max_duration_s", 300),
    ],
)
def test_defaults_match_the_spec(key, value):
    assert DEFAULTS[key] == value


def test_defaults_has_exactly_the_spec_keys():
    assert set(DEFAULTS) == {
        "hotkey",
        "language",
        "model",
        "device",
        "min_duration_s",
        "max_duration_s",
        "clean_speech",
        "flow_bar",
        "flow_bar_position",
        "ai_cleanup",
    }


def test_missing_file_is_created_with_defaults(tmp_path):
    path = tmp_path / "config.json"

    cfg = load_config(path)

    assert cfg == DEFAULTS
    assert path.exists(), "first run must write the file, not just return defaults"
    assert read_json(path) == DEFAULTS


def test_partial_file_is_filled_from_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "f8"}), encoding="utf-8")

    cfg = load_config(path)

    assert cfg["hotkey"] == "f8", "what the user set must win over the default"
    assert cfg["model"] == DEFAULTS["model"]
    assert cfg["min_duration_s"] == DEFAULTS["min_duration_s"]
    assert set(cfg) == set(DEFAULTS)


def test_unknown_keys_are_preserved(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"hotkey": "f9", "future_option": {"a": 1}}), encoding="utf-8"
    )

    cfg = load_config(path)

    assert cfg["future_option"] == {"a": 1}
    assert cfg["language"] == DEFAULTS["language"]


# "win" is only unusable on Windows, where releasing it opens the Start menu;
# on macOS the same key is Command and is perfectly good as a hotkey. The other
# three are refused on every platform.
_BAD_HOTKEYS = ["nonsense", "caps lock", ""]
if sys.platform != "darwin":
    _BAD_HOTKEYS.append("win")


@pytest.mark.parametrize("bad", _BAD_HOTKEYS)
def test_invalid_hotkey_falls_back_to_the_default_and_warns(tmp_path, capsys, bad):
    # Spec: on startup an unusable hotkey warns loudly, falls back to the
    # platform default, and the app keeps running. It must never crash.
    #
    # Each case exercises a different refusal in Task 2's parse_hotkey:
    # "nonsense" is not a key name; "win" is a bare Windows key (Task 2
    # canonicalises "win" -> "windows" before checking the ban list, which is
    # why that list is keyed on "windows" there and still catches "win" here);
    # "caps lock" is banned anywhere in a combo; "" is an empty hotkey.
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": bad, "language": "en"}), encoding="utf-8")

    cfg = load_config(path)

    assert cfg["hotkey"] == DEFAULT_HOTKEY
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert DEFAULT_HOTKEY in err
    assert str(path) in err, "the warning must name the file the user has to fix"
    # The file is left exactly as typed, so the user can see and fix their typo.
    assert read_json(path)["hotkey"] == bad


def test_valid_hotkey_is_left_alone_and_prints_nothing(tmp_path, capsys):
    # Returned verbatim, not canonicalised: config.json stays the user's file.
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "ctrl+win"}), encoding="utf-8")

    cfg = load_config(path)

    assert cfg["hotkey"] == "ctrl+win"
    assert capsys.readouterr().err == ""


def test_non_string_hotkey_falls_back_instead_of_crashing(tmp_path, capsys):
    # A hand-edit can easily produce a number or null. Starting the app must
    # still work.
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": 42}), encoding="utf-8")

    cfg = load_config(path)

    assert cfg["hotkey"] == DEFAULT_HOTKEY
    assert "WARNING" in capsys.readouterr().err


def test_loading_does_not_mutate_defaults(tmp_path):
    cfg = load_config(tmp_path / "config.json")

    cfg["hotkey"] = "f12"
    cfg["injected"] = True

    assert DEFAULTS["hotkey"] == DEFAULT_HOTKEY
    assert "injected" not in DEFAULTS


def test_save_config_round_trips(tmp_path):
    path = tmp_path / "config.json"
    cfg = dict(DEFAULTS)
    cfg["hotkey"] = "f8"
    cfg["notes"] = "kept"

    save_config(cfg, path)

    assert read_json(path) == cfg
    assert load_config(path) == cfg


def test_save_config_writes_readable_json(tmp_path):
    # The file is documented as hand-editable, so it must not be one long line.
    path = tmp_path / "config.json"

    save_config(dict(DEFAULTS), path)

    text = path.read_text(encoding="utf-8")
    assert text.count("\n") >= len(DEFAULTS)
    assert text.endswith("\n")


def test_the_default_hotkey_matches_the_platform():
    """Right Ctrl does not exist on a MacBook keyboard at all.

    Shipping it as the macOS default would hand every new Mac user a key they
    physically cannot press, and the app would look broken before they ever
    reached --set-hotkey. Right Option is the closest analogue: present on every
    Mac, does nothing on its own.
    """
    import sys

    from vocal_advantage.config import DEFAULT_HOTKEY

    expected = "right alt" if sys.platform == "darwin" else "right ctrl"
    assert DEFAULT_HOTKEY == expected
    assert DEFAULTS["hotkey"] == DEFAULT_HOTKEY


# ---------------------------------------------------------------------------
# The Flow Bar's two settings
# ---------------------------------------------------------------------------
#
# Same contract as the hotkey: config.json is hand-edited, so a bad value warns
# and falls back FOR THIS RUN only, and the file is left exactly as typed so the
# mistake is still visible and fixable.


def test_defaults_include_the_flow_bar_settings():
    assert DEFAULTS["flow_bar"] is True
    assert DEFAULTS["flow_bar_position"] == "bottom-centre"


def test_a_fresh_config_has_the_flow_bar_settings(tmp_path):
    cfg = load_config(tmp_path / "config.json")
    assert cfg["flow_bar"] is True
    assert cfg["flow_bar_position"] == "bottom-centre"


def test_an_existing_config_gains_the_new_keys(tmp_path):
    # Upgrading in place: someone already has a config.json from before the
    # Flow Bar existed, and must not have to delete it.
    path = tmp_path / "config.json"
    path.write_text('{"hotkey": "right alt"}', encoding="utf-8")
    cfg = load_config(path)
    assert cfg["hotkey"] == "right alt"
    assert cfg["flow_bar"] is True
    assert cfg["flow_bar_position"] == "bottom-centre"


def test_the_flow_bar_can_be_switched_off(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"flow_bar": false}', encoding="utf-8")
    assert load_config(path)["flow_bar"] is False


@pytest.mark.parametrize(
    "position", ["bottom-centre", "bottom-left", "bottom-right"]
)
def test_every_documented_position_is_accepted(tmp_path, position):
    path = tmp_path / "config.json"
    path.write_text(f'{{"flow_bar_position": "{position}"}}', encoding="utf-8")
    assert load_config(path)["flow_bar_position"] == position


def test_the_american_spelling_is_accepted_too(tmp_path):
    # The muscle memory is real, and rejecting it would be a papercut that
    # taught nobody anything.
    path = tmp_path / "config.json"
    path.write_text('{"flow_bar_position": "bottom-center"}', encoding="utf-8")
    assert load_config(path)["flow_bar_position"] == "bottom-centre"


def test_an_unknown_position_warns_and_falls_back(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text('{"flow_bar_position": "middle"}', encoding="utf-8")
    cfg = load_config(path)
    assert cfg["flow_bar_position"] == "bottom-centre"
    assert "middle" in capsys.readouterr().err


def test_a_non_text_position_warns_and_falls_back(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text('{"flow_bar_position": 3}', encoding="utf-8")
    assert load_config(path)["flow_bar_position"] == "bottom-centre"
    assert capsys.readouterr().err != ""


def test_a_bad_position_does_not_rewrite_the_file(tmp_path):
    # The user must still be able to see what they typed.
    path = tmp_path / "config.json"
    original = '{"flow_bar_position": "middle"}'
    path.write_text(original, encoding="utf-8")
    load_config(path)
    assert path.read_text(encoding="utf-8") == original


def test_a_non_boolean_flow_bar_warns_and_falls_back(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text('{"flow_bar": "yes"}', encoding="utf-8")
    assert load_config(path)["flow_bar"] is True
    assert capsys.readouterr().err != ""


def test_a_bad_position_does_not_stop_the_app_starting(tmp_path):
    # The rule for this whole file: decoration never blocks dictation.
    path = tmp_path / "config.json"
    path.write_text(
        '{"hotkey": "right alt", "flow_bar_position": "nowhere"}',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg["hotkey"] == "right alt"
