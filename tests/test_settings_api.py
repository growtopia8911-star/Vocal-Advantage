"""Reading and writing config.json on behalf of the settings page.

Gate 4 of `docs/plans/2026-08-25-interface-design.md`, the half that has no
HTTP in it. `settings_api` is pure: paths in, dicts out, nothing about sockets
or browsers, so the rules below are testable at full speed.

The two that matter most are not about the UI at all:

* **A hand-edit must never be silently reverted.** config.json is documented as
  editable in a text editor. A settings page that read the file once at open
  and wrote the whole dict back on save would throw away anything changed in
  between -- `main._save_flow_bar_point` already establishes re-reading first,
  for exactly this reason.
* **Unknown keys must survive.** A config written by a newer version, or a
  stale key like `live_typing`, has to come back out untouched. Dropping keys
  the UI does not recognise is how a settings window quietly downgrades a file.
"""

from __future__ import annotations

import json

import pytest

from vocal_advantage.config import DEFAULTS
from vocal_advantage.settings_api import (
    TIERS,
    SettingsError,
    handle,
    read_settings,
    write_settings,
)


def write_config(tmp_path, **overrides):
    path = tmp_path / "config.json"
    data = dict(DEFAULTS)
    data.update(overrides)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- reading ----------------------------------------------------------------

def test_reading_returns_every_default_key(tmp_path):
    values = read_settings(write_config(tmp_path))["values"]
    assert set(values) >= set(DEFAULTS)


def test_reading_reflects_what_is_actually_in_the_file(tmp_path):
    path = write_config(tmp_path, model="tiny", sounds=False)
    values = read_settings(path)["values"]
    assert values["model"] == "tiny"
    assert values["sounds"] is False


def test_a_missing_file_reads_as_the_defaults(tmp_path):
    """Same contract as `load_config`: the app starts, it does not fail."""
    values = read_settings(tmp_path / "nope.json")["values"]
    assert values["hotkey"] == DEFAULTS["hotkey"]


def test_every_key_is_labelled_with_the_tier_it_belongs_to(tmp_path):
    """The three-tier split is the whole organising idea, so it is data the
    page is handed rather than a layout the page invents."""
    tiers = read_settings(write_config(tmp_path))["tiers"]
    assert set(tiers) == {"hands", "machine", "task"}
    placed = {key for keys in tiers.values() for key in keys}
    # flow_bar_point has no control: it is written by dragging the bar.
    assert placed == set(DEFAULTS) - {"flow_bar_point"}


def test_no_key_is_in_two_tiers_at_once():
    seen = [key for keys in TIERS.values() for key in keys]
    assert len(seen) == len(set(seen)), "a key appears in more than one tier"


# --- writing ----------------------------------------------------------------

def test_writing_changes_only_what_was_sent(tmp_path):
    path = write_config(tmp_path, model="small")
    write_settings(path, {"model": "tiny"})
    saved = load(path)
    assert saved["model"] == "tiny"
    assert saved["hotkey"] == DEFAULTS["hotkey"]


def test_a_hand_edit_made_while_the_page_was_open_survives(tmp_path):
    """Gate 4e. The page was opened before this edit and knows nothing of it."""
    path = write_config(tmp_path, model="small")
    read_settings(path)                       # page opens, reads

    hand = load(path)                         # user edits the file meanwhile
    hand["max_duration_s"] = 42
    path.write_text(json.dumps(hand, indent=2) + "\n", encoding="utf-8")

    write_settings(path, {"model": "tiny"})   # page saves one unrelated field

    saved = load(path)
    assert saved["max_duration_s"] == 42, "the hand edit was clobbered"
    assert saved["model"] == "tiny"


def test_an_unknown_key_is_left_alone(tmp_path):
    """Gate 4f. A newer version's key, or a stale one, must come back out."""
    path = write_config(tmp_path)
    data = load(path)
    data["live_typing"] = True
    data["from_the_future"] = {"nested": 1}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    write_settings(path, {"sounds": False})

    saved = load(path)
    assert saved["live_typing"] is True
    assert saved["from_the_future"] == {"nested": 1}


def test_the_file_stays_in_the_documented_format(tmp_path):
    """Indented JSON with a trailing newline -- it is still hand-editable."""
    path = write_config(tmp_path)
    write_settings(path, {"sounds": False})
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "\n  " in text, "not indented"


# --- validation -------------------------------------------------------------

def test_a_bad_hotkey_is_refused_and_nothing_is_written(tmp_path):
    path = write_config(tmp_path, hotkey="f8")
    with pytest.raises(SettingsError) as caught:
        write_settings(path, {"hotkey": "caps lock"})
    assert "caps lock" in str(caught.value).lower()
    assert load(path)["hotkey"] == "f8", "the file was touched anyway"


def test_a_refused_save_leaves_every_other_field_alone(tmp_path):
    """One bad field must not half-apply the rest of the form."""
    path = write_config(tmp_path, model="small")
    with pytest.raises(SettingsError):
        write_settings(path, {"model": "tiny", "hotkey": "caps lock"})
    assert load(path)["model"] == "small"


@pytest.mark.parametrize(
    "key,bad",
    [
        ("chunk_s", 0),
        ("chunk_s", -1),
        ("tap_threshold_s", 0),
        ("silence_timeout_s", -0.5),
        ("overlap_s", -1),
    ],
)
def test_numbers_that_would_break_the_pipeline_are_refused(tmp_path, key, bad):
    path = write_config(tmp_path)
    with pytest.raises(SettingsError):
        write_settings(path, {key: bad})


def test_a_key_the_app_does_not_have_is_refused(tmp_path):
    """The page can only set things that exist. Anything else is a bug or a
    poke from something that is not the page."""
    path = write_config(tmp_path)
    with pytest.raises(SettingsError):
        write_settings(path, {"drop_tables": True})


def test_flow_bar_point_cannot_be_set_through_the_page(tmp_path):
    """It is written by dragging the bar, and has no control by design."""
    path = write_config(tmp_path)
    with pytest.raises(SettingsError):
        write_settings(path, {"flow_bar_point": [1.0, 2.0]})


# --- reaching it from the tray ----------------------------------------------

class _StubChanger:
    def request(self):
        pass


def menu(monkeypatch, platform_name, tmp_path):
    """(title, action) pairs, with callable titles resolved."""
    from vocal_advantage import main as va_main

    monkeypatch.setattr(va_main.sys, "platform", platform_name)
    items = va_main._menu_items(None, _StubChanger(), tmp_path / "config.json")
    return [(t() if callable(t) else t, a) for t, a in items]


def settings_action(pairs):
    for title, action in pairs:
        if "Settings" in title:
            return action
    return "absent"


def test_the_tray_offers_settings_on_a_mac(monkeypatch, tmp_path):
    assert callable(settings_action(menu(monkeypatch, "darwin", tmp_path)))


def test_settings_does_nothing_on_windows_where_there_is_no_window(
    monkeypatch, tmp_path
):
    """There is no settings_win yet. The action is None, which is how
    `_menu_items` says "leave this out" -- the same mechanism that hides
    "Move bar" when there is no bar. TrayIcon does the dropping."""
    assert settings_action(menu(monkeypatch, "win32", tmp_path)) is None


def test_a_none_action_really_is_dropped_from_the_menu(monkeypatch, tmp_path):
    """The half of that contract that lives in TrayIcon, asserted so the two
    halves cannot drift apart."""
    from vocal_advantage.tray_mac import TrayIcon

    tray = TrayIcon(None, lambda: None, menu(monkeypatch, "win32", tmp_path))
    titles = [t() if callable(t) else t for t, _ in tray._extra]
    assert not any("Settings" in title for title in titles)


def test_settings_is_the_first_thing_in_the_menu(monkeypatch, tmp_path):
    """Above "Move bar" and "Change hotkey", both of which it will absorb."""
    assert "Settings" in menu(monkeypatch, "darwin", tmp_path)[0][0]


# --- the bridge the page talks over -----------------------------------------
#
# WKWebView hands Python whatever JavaScript posted, so `handle` is the edge of
# the app: everything past it is trusted, nothing before it is. It never raises
# -- an exception here would surface as a settings window that silently stops
# responding, with the traceback nowhere the user can see it.

def test_a_message_arriving_as_json_text_is_understood(tmp_path):
    """What WKWebView actually delivers.

    `message.body()` bridges a JavaScript object to an ObjC NSDictionary proxy,
    which is not a Python dict -- `isinstance(body, dict)` is False and the
    whole page came back "unreadable" with no controls drawn. The page posts a
    JSON string instead, so what crosses the bridge is text and there is one
    obvious thing to do with it.
    """
    reply = handle(json.dumps({"action": "read"}), write_config(tmp_path))
    assert reply["ok"] is True
    assert reply["data"]["values"]["model"] == DEFAULTS["model"]


def test_a_json_save_applies(tmp_path):
    path = write_config(tmp_path, model="small")
    reply = handle(json.dumps({"action": "save", "updates": {"model": "tiny"}}), path)
    assert reply["ok"] is True
    assert load(path)["model"] == "tiny"


def test_text_that_is_not_json_is_refused_politely(tmp_path):
    reply = handle("{not json at all", write_config(tmp_path))
    assert reply["ok"] is False
    assert reply["error"]


def test_read_hands_back_values_and_tiers(tmp_path):
    reply = handle({"action": "read"}, write_config(tmp_path))
    assert reply["ok"] is True
    assert reply["data"]["values"]["model"] == DEFAULTS["model"]
    assert set(reply["data"]["tiers"]) == {"hands", "machine", "task"}


def test_save_applies_and_reports_what_moved(tmp_path):
    path = write_config(tmp_path, model="small")
    reply = handle({"action": "save", "updates": {"model": "tiny"}}, path)
    assert reply["ok"] is True
    assert reply["data"]["changed"] == {"model": "tiny"}
    assert load(path)["model"] == "tiny"


def test_a_refused_save_comes_back_as_a_message_not_a_crash(tmp_path):
    path = write_config(tmp_path, hotkey="f8")
    reply = handle({"action": "save", "updates": {"hotkey": "caps lock"}}, path)
    assert reply["ok"] is False
    assert "caps lock" in reply["error"].lower()
    assert load(path)["hotkey"] == "f8"


@pytest.mark.parametrize(
    "message",
    [
        {},                                   # no action
        {"action": "drop_database"},          # not an action we have
        {"action": "save"},                   # save with nothing to save
        {"action": "save", "updates": "nope"},  # updates is not an object
        "not even a dict",
        None,
    ],
)
def test_a_malformed_message_is_answered_not_raised(tmp_path, message):
    reply = handle(message, write_config(tmp_path))
    assert reply["ok"] is False
    assert reply["error"]


def test_a_malformed_message_never_touches_the_file(tmp_path):
    path = write_config(tmp_path, model="small")
    before = path.read_text(encoding="utf-8")
    handle({"action": "save", "updates": {"model": 7}}, path)
    assert path.read_text(encoding="utf-8") == before


def test_a_valid_save_reports_what_it_changed(tmp_path):
    path = write_config(tmp_path, model="small", sounds=True)
    changed = write_settings(path, {"model": "tiny", "sounds": True})
    # sounds was already true, so only the model actually moved.
    assert changed == {"model": "tiny"}
