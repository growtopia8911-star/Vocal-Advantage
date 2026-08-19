"""Tests for vocal_advantage.config.

Every test that touches disk writes into pytest's ``tmp_path``. The real
config.json at the repo root is never read or written by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vocal_advantage.config import CONFIG_PATH, DEFAULTS, load_config, save_config


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
        ("hotkey", "right ctrl"),
        ("language", "en"),
        ("model", "large-v3-turbo"),
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


@pytest.mark.parametrize("bad", ["nonsense", "win", "caps lock", ""])
def test_invalid_hotkey_falls_back_to_right_ctrl_and_warns(tmp_path, capsys, bad):
    # Spec: on startup an unusable hotkey warns loudly, falls back to
    # "right ctrl", and the app keeps running. It must never crash.
    #
    # Each case exercises a different refusal in Task 2's parse_hotkey:
    # "nonsense" is not a key name; "win" is a bare Windows key (Task 2
    # canonicalises "win" -> "windows" before checking the ban list, which is
    # why that list is keyed on "windows" there and still catches "win" here);
    # "caps lock" is banned anywhere in a combo; "" is an empty hotkey.
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": bad, "language": "en"}), encoding="utf-8")

    cfg = load_config(path)

    assert cfg["hotkey"] == "right ctrl"
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "right ctrl" in err
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

    assert cfg["hotkey"] == "right ctrl"
    assert "WARNING" in capsys.readouterr().err


def test_loading_does_not_mutate_defaults(tmp_path):
    cfg = load_config(tmp_path / "config.json")

    cfg["hotkey"] = "f12"
    cfg["injected"] = True

    assert DEFAULTS["hotkey"] == "right ctrl"
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
