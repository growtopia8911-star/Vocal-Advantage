"""Hotkey parsing/validation -- SPEC's "The hotkey setting" table."""

import pytest

from vocal_advantage.hotkey_spec import (
    BANNED,
    MODIFIERS,
    HotkeyError,
    HotkeySpec,
    parse_hotkey,
)


def test_hotkey_error_is_a_value_error():
    assert issubclass(HotkeyError, ValueError)


ACCEPTED = [
    ("right ctrl", {"right ctrl"}),
    ("Right Ctrl", {"right ctrl"}),
    ("RIGHT CTRL", {"right ctrl"}),
    ("   right ctrl   ", {"right ctrl"}),
    ("right_ctrl", {"right ctrl"}),
    ("right  ctrl", {"right ctrl"}),
    ("f8", {"f8"}),
    ("F8", {"f8"}),
    ("ctrl+win", {"ctrl", "windows"}),
    ("ctrl + win", {"ctrl", "windows"}),
    ("CTRL  +  WIN", {"ctrl", "windows"}),
    ("ctrl+alt+space", {"ctrl", "alt", "space"}),
    ("control+cmd", {"ctrl", "windows"}),
    ("scroll lock", {"scroll lock"}),
    ("pause", {"pause"}),
    ("right alt", {"right alt"}),
    ("escape", {"esc"}),
    ("+", {"+"}),
    ("ctrl+ctrl", {"ctrl"}),
]


@pytest.mark.parametrize("text,expected", ACCEPTED, ids=[t for t, _ in ACCEPTED])
def test_accepted_hotkeys(text, expected):
    assert parse_hotkey(text).keys == frozenset(expected)


def test_win_and_cmd_canonicalise_to_windows():
    """The name every other module must use for the Windows key is "windows".

    Task 6's hook and Task 9's controller compare incoming key-event names
    against ``spec.keys``, so if this ever changed they would silently stop
    matching. Pinned here rather than left implicit.
    """
    assert parse_hotkey("ctrl+win").keys == frozenset({"ctrl", "windows"})
    assert parse_hotkey("ctrl+cmd").keys == frozenset({"ctrl", "windows"})
    assert parse_hotkey("shift+left win").keys == frozenset({"shift", "left windows"})


def test_case_and_spacing_normalise_to_one_spec():
    """SPEC test plan, verbatim: the three spellings all normalise the same."""
    variants = ["Right Ctrl", "right ctrl", "RIGHT CTRL"]
    specs = [parse_hotkey(v) for v in variants]
    assert len(set(specs)) == 1, specs
    assert specs[0].keys == frozenset({"right ctrl"})


def test_spaces_around_plus_give_a_combo_of_two():
    """SPEC test plan, verbatim: "ctrl + win" -> combo of two."""
    spec = parse_hotkey("ctrl + win")
    assert len(spec.keys) == 2
    assert spec == parse_hotkey("ctrl+win")


REJECTED = [
    ("", "no hotkey given"),
    ("     ", "no hotkey given"),
    ("nonsense", "not a key name"),
    ("f27", "not a key name"),
    ("ctrl+nonsense", "not a key name"),
    ("win", "start menu"),
    ("Win", "start menu"),
    ("windows", "start menu"),
    ("cmd", "start menu"),
    ("left win", "start menu"),
    ("right windows", "start menu"),
    ("caps lock", "swallows"),
    ("Caps Lock", "swallows"),
    ("capslock", "swallows"),
    ("ctrl+caps lock", "swallows"),
    ("ctrl+", "empty key"),
    ("+ctrl", "empty key"),
    ("ctrl++win", "empty key"),
    ("ctrl+c, ctrl+v", "sequence of two shortcuts"),
]


@pytest.mark.parametrize("text,fragment", REJECTED, ids=[repr(t) for t, _ in REJECTED])
def test_rejected_hotkeys(text, fragment):
    with pytest.raises(HotkeyError) as caught:
        parse_hotkey(text)
    assert fragment in str(caught.value).lower(), str(caught.value)


def test_non_string_is_refused_not_crashed():
    with pytest.raises(HotkeyError):
        parse_hotkey(None)
    with pytest.raises(HotkeyError):
        parse_hotkey(8)


def test_ctrl_win_is_allowed_even_though_bare_win_is_not():
    """SPEC: bare `win` is out because releasing it opens Start; combos are fine."""
    with pytest.raises(HotkeyError):
        parse_hotkey("win")
    assert parse_hotkey("ctrl+win").keys == frozenset({"ctrl", "windows"})


def test_banned_reasons_reach_the_user():
    for reason in BANNED.values():
        assert reason and reason == reason.strip()
    with pytest.raises(HotkeyError) as caught:
        parse_hotkey("win")
    assert BANNED["windows"] in str(caught.value)
    with pytest.raises(HotkeyError) as caught:
        parse_hotkey("caps lock")
    assert BANNED["caps lock"] in str(caught.value)


# SPEC, state machine: cancel-on-other-key applies when the hotkey "is (or
# CONTAINS) a bare modifier". So a combo with a modifier anywhere in it is True,
# even when it also has a normal key; only a hotkey with no modifier at all is
# False. Task 9's controller branches on exactly this.
CONTAINS_MODIFIER = [
    ("right ctrl", True),
    ("left ctrl", True),
    ("ctrl", True),
    ("ctrl+win", True),
    ("shift+alt", True),
    ("right alt", True),
    ("alt gr", True),
    ("ctrl+alt+space", True),
    ("ctrl+f8", True),
    ("shift+f8", True),
    ("f8", False),
    ("space", False),
    ("f8+space", False),
]


@pytest.mark.parametrize(
    "text,expected", CONTAINS_MODIFIER, ids=[t for t, _ in CONTAINS_MODIFIER]
)
def test_is_modifier_only_is_true_when_a_bare_modifier_is_present(text, expected):
    assert parse_hotkey(text).is_modifier_only is expected


def test_empty_spec_is_not_modifier_only():
    """An empty hotkey has no modifier in it, so cancelling stays off."""
    assert HotkeySpec(frozenset()).is_modifier_only is False


DISPLAY = [
    ({"ctrl", "windows"}, "Ctrl + Win"),
    ({"right ctrl"}, "Right Ctrl"),
    ({"f8"}, "F8"),
    ({"ctrl", "alt", "space"}, "Ctrl + Alt + Space"),
    ({"space", "ctrl"}, "Ctrl + Space"),
    ({"shift", "alt"}, "Alt + Shift"),
    ({"a", "f8"}, "A + F8"),
    ({"left windows", "shift"}, "Shift + Left Win"),
    ({"caps lock"}, "Caps Lock"),
]


@pytest.mark.parametrize("keys,expected", DISPLAY, ids=[e for _, e in DISPLAY])
def test_display_form(keys, expected):
    assert str(HotkeySpec(frozenset(keys))) == expected


def test_display_order_is_stable_whatever_order_the_keys_arrive_in():
    a = HotkeySpec(frozenset({"ctrl", "alt", "space"}))
    b = HotkeySpec(frozenset(["space", "alt", "ctrl"]))
    assert str(a) == str(b) == "Ctrl + Alt + Space"


@pytest.mark.parametrize(
    "text", ["right ctrl", "f8", "ctrl+win", "ctrl+alt+space", "left win+shift", "+"]
)
def test_display_form_parses_back_to_the_same_spec(text):
    """--set-hotkey echoes str(spec) and writes it to config.json, so the
    display form has to survive a round trip through parse_hotkey."""
    spec = parse_hotkey(text)
    assert parse_hotkey(str(spec)) == spec


def test_spec_is_hashable_and_compares_by_keys():
    assert HotkeySpec(frozenset({"ctrl"})) == HotkeySpec(frozenset({"ctrl"}))
    assert len({HotkeySpec(frozenset({"ctrl"})), HotkeySpec(frozenset({"ctrl"}))}) == 1


def test_keys_given_as_a_bare_string_is_a_loud_error():
    """frozenset("f8") is {'f', '8'} -- silently wrong, so refuse it."""
    with pytest.raises(TypeError):
        HotkeySpec("f8")


def test_keys_given_as_any_iterable_is_coerced_to_a_frozenset():
    assert HotkeySpec(["ctrl", "windows"]).keys == frozenset({"ctrl", "windows"})


def test_modifiers_matches_the_keyboard_library():
    """Our literal copy must not drift from the library's own modifier set."""
    keyboard = pytest.importorskip("keyboard")
    assert MODIFIERS == frozenset(keyboard.all_modifiers)


def test_parsing_does_not_start_a_keyboard_hook():
    """SPEC never suppresses keys; a hook installed by a *parse* would be a
    surprise, and would make the test suite grab global keyboard input.

    Import the library BEFORE snapshotting the threads: `parse_hotkey` imports
    it lazily, so a snapshot taken first would blame the import's own threads on
    the parse, and this test would only pass when some earlier test in the file
    had already imported keyboard.
    """
    import threading

    keyboard = pytest.importorskip("keyboard")
    before = {t.name for t in threading.enumerate()}
    parse_hotkey("ctrl+win")
    assert keyboard._listener.listening is False
    assert {t.name for t in threading.enumerate()} == before
