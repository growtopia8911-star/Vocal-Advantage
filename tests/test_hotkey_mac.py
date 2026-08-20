"""Tests for the macOS key hook.

Quartz is bound once at import to the module-level name ``Quartz``; these tests
replace it, then call the tap callback directly with fake events. No event tap
is created, no Accessibility permission is needed, and no keys are touched.

The behaviour that matters here is the decoding, because macOS reports
modifiers completely differently from Windows:

* modifiers arrive as ``flagsChanged``, never as keyDown/keyUp -- anything
  waiting for a keyDown on Right Option waits forever, silently;
* left and right ARE distinguishable (61 vs 58), unlike Windows;
* whether a modifier went down or up is not in the event type, and cannot
  always be read from the flag mask either -- hold both Options, release one,
  and the option flag is still set. The physical key state is the only
  unambiguous answer.
"""

from __future__ import annotations

import threading

import pytest

from vocal_advantage import hotkey_mac
from vocal_advantage.hotkey_spec import HotkeySpec

RIGHT_OPTION = HotkeySpec(frozenset({"right alt"}))
F8 = HotkeySpec(frozenset({"f8"}))


class FakeEvent:
    def __init__(self, keycode, fields=None):
        self.keycode = keycode
        self.fields = dict(fields or {})


class FakeQuartz:
    kCGEventKeyDown = 10
    kCGEventKeyUp = 11
    kCGEventFlagsChanged = 12
    kCGKeyboardEventKeycode = 9
    kCGEventSourceUserData = 42
    kCGEventSourceStateHIDSystemState = 1
    # the constants CGEventTapCreate is called with
    kCGSessionEventTap = 1
    kCGHeadInsertEventTap = 0
    kCGEventTapOptionListenOnly = 1

    def __init__(self, down_keys=()):
        self.down_keys = set(down_keys)

    def CGEventGetIntegerValueField(self, event, field):
        if field == self.kCGKeyboardEventKeycode:
            return event.keycode
        return event.fields.get(field, 0)

    def CGEventSourceKeyState(self, state, keycode):
        return keycode in self.down_keys


@pytest.fixture
def quartz(monkeypatch):
    fake = FakeQuartz()
    monkeypatch.setattr(hotkey_mac, "Quartz", fake)
    return fake


# ---------------------------------------------------------------------------
# The keycode table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keycode, expected",
    [
        (61, "right alt"),      # the default hotkey
        (58, "left alt"),
        (55, "left windows"),   # Command; macOS Cmd shares the Windows-key slot
        (54, "right windows"),
        (59, "left ctrl"),
        (62, "right ctrl"),
        (56, "left shift"),
        (57, "caps lock"),
        (100, "f8"),
        (8, "c"),
        (49, "space"),
    ],
)
def test_keycodes_map_onto_the_shared_vocabulary(keycode, expected):
    assert hotkey_mac.KEYCODE_TO_NAME[keycode] == expected


def test_right_option_is_the_documented_default():
    # "right ctrl" does not exist on a MacBook keyboard at all.
    assert hotkey_mac.DEFAULT_HOTKEY == "right alt"
    assert hotkey_mac.KEYCODE_TO_NAME[hotkey_mac.KEYCODE_RIGHT_OPTION] == "right alt"


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def test_a_modifier_press_is_decoded_from_the_physical_key_state(quartz):
    """flagsChanged carries no direction. The key state is the truth."""
    quartz.down_keys = {61}
    assert hotkey_mac.decode_event(
        quartz.kCGEventFlagsChanged, FakeEvent(61)
    ) == ("right alt", True)


def test_a_modifier_release_is_decoded_the_same_way(quartz):
    quartz.down_keys = set()
    assert hotkey_mac.decode_event(
        quartz.kCGEventFlagsChanged, FakeEvent(61)
    ) == ("right alt", False)


def test_releasing_one_of_two_held_options_is_still_unambiguous(quartz):
    """The flag mask would still say 'option'; the key state does not lie."""
    quartz.down_keys = {58}  # left Option still held, right one just released
    assert hotkey_mac.decode_event(
        quartz.kCGEventFlagsChanged, FakeEvent(61)
    ) == ("right alt", False)
    assert hotkey_mac.decode_event(
        quartz.kCGEventFlagsChanged, FakeEvent(58)
    ) == ("left alt", True)


@pytest.mark.parametrize("etype_name, is_down", [("kCGEventKeyDown", True), ("kCGEventKeyUp", False)])
def test_ordinary_keys_use_the_event_type(quartz, etype_name, is_down):
    etype = getattr(quartz, etype_name)
    assert hotkey_mac.decode_event(etype, FakeEvent(8)) == ("c", is_down)


def test_an_unmapped_keycode_still_counts_as_some_other_key(quartz):
    """It only has to be a name that is not the hotkey, so cancelling works."""
    name, is_down = hotkey_mac.decode_event(quartz.kCGEventKeyDown, FakeEvent(999))
    assert is_down is True
    assert name and name not in RIGHT_OPTION.keys


# ---------------------------------------------------------------------------
# Our own injected events
# ---------------------------------------------------------------------------


def test_our_own_paste_keystrokes_are_skipped(quartz):
    """Without this the hook sees our Cmd+V as the user typing, and a dictation
    starts a second recording the instant it pastes."""
    from vocal_advantage import paste_mac

    seen = []
    listener = hotkey_mac.HotkeyListener(
        RIGHT_OPTION, lambda n, d: seen.append((n, d)), gate=threading.Event()
    )
    ours = FakeEvent(9, {FakeQuartz.kCGEventSourceUserData: paste_mac.INJECTED_MARKER})

    listener.handle_event(None, quartz.kCGEventKeyDown, ours, None)

    assert seen == []


# ---------------------------------------------------------------------------
# The listener
# ---------------------------------------------------------------------------


def test_holding_and_releasing_the_hotkey_forwards_one_pair(quartz):
    seen = []
    listener = hotkey_mac.HotkeyListener(
        RIGHT_OPTION, lambda n, d: seen.append((n, d)), gate=threading.Event()
    )

    quartz.down_keys = {61}
    listener.handle_event(None, quartz.kCGEventFlagsChanged, FakeEvent(61), None)
    quartz.down_keys = set()
    listener.handle_event(None, quartz.kCGEventFlagsChanged, FakeEvent(61), None)

    assert seen == [("right alt", True), ("right alt", False)]


def test_another_key_is_forwarded_so_the_controller_can_cancel(quartz):
    seen = []
    listener = hotkey_mac.HotkeyListener(
        RIGHT_OPTION, lambda n, d: seen.append((n, d)), gate=threading.Event()
    )

    quartz.down_keys = {61}
    listener.handle_event(None, quartz.kCGEventFlagsChanged, FakeEvent(61), None)
    listener.handle_event(None, quartz.kCGEventKeyDown, FakeEvent(8), None)

    assert seen == [("right alt", True), ("c", True)]


def test_the_callback_always_returns_the_event_so_nothing_is_swallowed(quartz):
    """Listen-only, never suppressing - the same rule as Windows."""
    listener = hotkey_mac.HotkeyListener(
        RIGHT_OPTION, lambda n, d: None, gate=threading.Event()
    )
    event = FakeEvent(8)
    assert listener.handle_event(None, quartz.kCGEventKeyDown, event, None) is event


def test_a_missing_accessibility_permission_says_exactly_that(monkeypatch):
    """CGEventTapCreate returns None and explains nothing. A dead hotkey with no
    message is the worst possible failure here."""
    class NoPermission(FakeQuartz):
        def CGEventTapCreate(self, *args):
            return None

    monkeypatch.setattr(hotkey_mac, "Quartz", NoPermission())
    listener = hotkey_mac.HotkeyListener(
        RIGHT_OPTION, lambda n, d: None, gate=threading.Event()
    )

    with pytest.raises(hotkey_mac.HotkeyPermissionError) as caught:
        listener.start()

    message = str(caught.value).lower()
    assert "accessibility" in message
    assert "privacy" in message


# ---------------------------------------------------------------------------
# read_pressed_keys - the twin of GetAsyncKeyState
# ---------------------------------------------------------------------------


def test_read_pressed_keys_reports_only_what_is_physically_down(quartz):
    quartz.down_keys = {61}
    assert hotkey_mac.read_pressed_keys(["right alt", "left alt", "f8"]) == frozenset(
        {"right alt"}
    )


def test_read_pressed_keys_ignores_names_it_has_no_keycode_for(quartz):
    assert hotkey_mac.read_pressed_keys(["not a real key"]) == frozenset()


# ---------------------------------------------------------------------------
# capture_hotkey
# ---------------------------------------------------------------------------


def test_capture_session_records_the_largest_chord():
    session = hotkey_mac._CaptureSession()
    for name, is_down in [("left windows", True), ("right alt", True),
                          ("right alt", False), ("left windows", False)]:
        session.feed(name, is_down)
    assert session.largest == frozenset({"left windows", "right alt"})
    assert session.done
