"""Tests for the settings the fast pipeline added (spec 4d, 6a-6c, 7e, 11).

Same rule as the rest of config.py: a bad value in a hand-edited file warns and
falls back for the run, and never stops the app starting. Someone editing JSON
by hand at midnight should get a working app and a sentence telling them what
they got wrong.
"""

from __future__ import annotations

import json

import pytest

from vocal_advantage.config import DEFAULTS, load_config


def write(tmp_path, **overrides):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(overrides), encoding="utf-8")
    return path


# --- the new keys exist with sane defaults ----------------------------------


@pytest.mark.parametrize(
    "key, expected",
    [
        ("tap_threshold_s", 0.3),   # 4d
        ("silence_timeout_s", 2.5),  # 6b
        ("chunk_s", 15.0),           # 7e
        ("overlap_s", 0.25),         # 7e
        ("timings", True),           # 11
    ],
)
def test_the_new_settings_have_defaults(key, expected):
    assert DEFAULTS[key] == expected


def test_a_config_missing_the_new_keys_is_filled_in(tmp_path):
    """An existing config.json from before this rework must still work."""
    path = write(tmp_path, hotkey="right ctrl", model="small")
    cfg = load_config(path)
    for key in ("tap_threshold_s", "silence_timeout_s", "chunk_s", "overlap_s"):
        assert key in cfg


def test_live_typing_is_gone(tmp_path):
    """Removed with the feature. A stale key in a file is preserved, not read."""
    assert "live_typing" not in DEFAULTS


def test_a_stale_live_typing_key_does_not_break_startup(tmp_path):
    path = write(tmp_path, live_typing=True)
    cfg = load_config(path)
    assert cfg["live_typing"] is True  # preserved untouched, like any unknown key


# --- values are honoured ----------------------------------------------------


def test_a_custom_tap_threshold_is_used(tmp_path):
    cfg = load_config(write(tmp_path, tap_threshold_s=0.5))
    assert cfg["tap_threshold_s"] == 0.5


def test_a_custom_silence_timeout_is_used(tmp_path):
    cfg = load_config(write(tmp_path, silence_timeout_s=1.5))
    assert cfg["silence_timeout_s"] == 1.5


def test_zero_silence_timeout_is_legitimate_and_survives(tmp_path):
    """6c: zero means "never auto-stop", not "missing"."""
    cfg = load_config(write(tmp_path, silence_timeout_s=0))
    assert cfg["silence_timeout_s"] == 0.0


def test_custom_chunk_and_overlap_are_used(tmp_path):
    cfg = load_config(write(tmp_path, chunk_s=5.0, overlap_s=0.5))
    assert cfg["chunk_s"] == 5.0
    assert cfg["overlap_s"] == 0.5


def test_timings_can_be_switched_off(tmp_path):
    cfg = load_config(write(tmp_path, timings=False))
    assert cfg["timings"] is False


# --- bad values warn and fall back ------------------------------------------


@pytest.mark.parametrize(
    "key", ["tap_threshold_s", "silence_timeout_s", "chunk_s", "overlap_s"]
)
@pytest.mark.parametrize("bad", ["fast", None, [], {}, True])
def test_a_non_numeric_value_falls_back_with_a_warning(tmp_path, capsys, key, bad):
    cfg = load_config(write(tmp_path, **{key: bad}))
    assert cfg[key] == DEFAULTS[key]
    assert "WARNING" in capsys.readouterr().err


@pytest.mark.parametrize("key", ["tap_threshold_s", "chunk_s"])
def test_a_value_of_zero_or_less_falls_back(tmp_path, capsys, key):
    """A zero chunk length would divide by zero; a zero tap threshold would
    make every press a hold and the toggle unreachable."""
    cfg = load_config(write(tmp_path, **{key: 0}))
    assert cfg[key] == DEFAULTS[key]
    assert "WARNING" in capsys.readouterr().err


def test_a_negative_silence_timeout_falls_back(tmp_path, capsys):
    cfg = load_config(write(tmp_path, silence_timeout_s=-1))
    assert cfg["silence_timeout_s"] == DEFAULTS["silence_timeout_s"]
    assert "WARNING" in capsys.readouterr().err


def test_an_overlap_of_zero_is_allowed(tmp_path):
    """Zero overlap is a legitimate choice: chunks simply butt up."""
    cfg = load_config(write(tmp_path, overlap_s=0))
    assert cfg["overlap_s"] == 0.0


def test_an_overlap_longer_than_the_chunk_is_rejected(tmp_path, capsys):
    """It would mean each window contained the last one whole, and the cursor
    would never advance through the audio."""
    cfg = load_config(write(tmp_path, chunk_s=2.0, overlap_s=3.0))
    assert cfg["overlap_s"] < cfg["chunk_s"]
    assert "WARNING" in capsys.readouterr().err


def test_a_non_boolean_timings_value_falls_back(tmp_path, capsys):
    cfg = load_config(write(tmp_path, timings="yes"))
    assert cfg["timings"] is DEFAULTS["timings"]
    assert "WARNING" in capsys.readouterr().err


def test_an_integer_is_accepted_where_a_float_is_expected(tmp_path):
    """JSON has one number type; 2 and 2.0 must mean the same thing."""
    cfg = load_config(write(tmp_path, chunk_s=3))
    assert cfg["chunk_s"] == 3.0


def test_a_bad_value_does_not_stop_the_app_starting(tmp_path):
    load_config(write(tmp_path, chunk_s="nonsense", overlap_s=[], timings=7))
