"""Tests for the global key hook.

The edge logic is driven with synthetic events - no real hook is installed, so
these run in a normal pytest session without touching the keyboard.
"""

from __future__ import annotations

import sys
import threading

import pytest

from vocal_advantage import _key_names
from vocal_advantage.hotkey_spec import HotkeySpec, parse_hotkey
from vocal_advantage.hotkey_win import VK_CODES, normalise_key_name, spec_key_for

RIGHT_CTRL = HotkeySpec(frozenset({"right ctrl"}))
LEFT_CTRL = HotkeySpec(frozenset({"left ctrl"}))
CTRL_WIN = HotkeySpec(frozenset({"ctrl", "windows"}))
F8 = HotkeySpec(frozenset({"f8"}))


# --------------------------------------------------------------------------
# Name translation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Right Ctrl", "right ctrl"),
        ("RIGHT CTRL", "right ctrl"),
        ("  right   ctrl ", "right ctrl"),
        ("left control", "left ctrl"),
        ("left menu", "left alt"),
        ("win", "windows"),
        ("left win", "left windows"),
        ("F8", "f8"),
        ("A", "a"),
        ("", None),
        (None, None),
    ],
)
def test_normalise_key_name(raw, expected):
    assert normalise_key_name(raw) == expected


@pytest.mark.parametrize(
    "event_name, spec_keys, expected",
    [
        # Windows names the LEFT modifiers without a side, so a config of
        # "left ctrl" has to be satisfied by an event named plain "ctrl".
        ("ctrl", {"left ctrl"}, "left ctrl"),
        ("ctrl", {"ctrl"}, "ctrl"),
        ("right ctrl", {"ctrl"}, "ctrl"),
        ("right ctrl", {"right ctrl"}, "right ctrl"),
        # ...but the two Ctrls must stay distinguishable when the config picks a side.
        ("right ctrl", {"left ctrl"}, "right ctrl"),
        ("ctrl", {"right ctrl"}, "ctrl"),
        # "windows" is the canonical name (Task 2 maps win/cmd onto it); the
        # "win" row is defence for a hand-written spec that slipped through.
        ("left windows", {"win"}, "win"),
        ("left windows", {"windows"}, "windows"),
        ("c", {"right ctrl"}, "c"),
    ],
)
def test_spec_key_for(event_name, spec_keys, expected):
    assert spec_key_for(event_name, spec_keys) == expected


@pytest.mark.parametrize("text", ["right ctrl", "left ctrl", "f8", "ctrl+win", "ctrl+alt+space"])
def test_every_parsed_key_has_a_virtual_key_code(text):
    """Resync after a paste reads GetAsyncKeyState by name, so any key the
    config accepts must be one we can look up."""
    for key in parse_hotkey(text).keys:
        assert key in VK_CODES, f"{key!r} has no virtual-key code in hotkey_win.VK_CODES"


@pytest.mark.parametrize(
    "configured, event_name",
    [
        ("right ctrl", "right ctrl"),
        ("left ctrl", "ctrl"),
        ("ctrl", "ctrl"),
        ("f8", "f8"),
    ],
)
def test_hook_event_names_resolve_onto_the_configured_keys(configured, event_name):
    spec = parse_hotkey(configured)
    resolved = spec_key_for(normalise_key_name(event_name), spec.keys)
    assert resolved in spec.keys


def test_the_module_binds_no_user32_off_windows():
    """The pure layers above have to stay testable on a Mac, so the
    ``ctypes.WinDLL`` binding is guarded by ``sys.platform`` -- ``WinDLL`` does
    not exist at all off Windows, and an unguarded call at module scope would
    make this whole file uncollectable. With no user32 to ask, nothing is held.
    """
    import vocal_advantage.hotkey_win as hw

    if sys.platform == "win32":
        pytest.skip("user32 is real here; this pins the non-Windows path")
    assert hw._user32 is None
    assert hw.read_pressed_keys(["ctrl", "f8", "not a key at all"]) == frozenset()


from vocal_advantage.hotkey_win import Edge, EdgeDetector


# --------------------------------------------------------------------------
# EdgeDetector - table driven
# --------------------------------------------------------------------------

EDGE_CASES = [
    (
        "single key: down then up",
        RIGHT_CTRL,
        [("right ctrl", True), ("right ctrl", False)],
        [Edge.HOTKEY_DOWN, Edge.HOTKEY_UP],
    ),
    (
        "OS autorepeat: extra downs while held are ignored",
        RIGHT_CTRL,
        [("right ctrl", True), ("right ctrl", True), ("right ctrl", True), ("right ctrl", False)],
        [Edge.HOTKEY_DOWN, Edge.IGNORED, Edge.IGNORED, Edge.HOTKEY_UP],
    ),
    (
        "combo: partial first, fires only when complete",
        CTRL_WIN,
        [("ctrl", True), ("left windows", True), ("left windows", False)],
        [Edge.COMBO_DOWN, Edge.HOTKEY_DOWN, Edge.HOTKEY_UP],
    ),
    (
        "combo: order does not matter",
        CTRL_WIN,
        [("left windows", True), ("ctrl", True), ("ctrl", False)],
        [Edge.COMBO_DOWN, Edge.HOTKEY_DOWN, Edge.HOTKEY_UP],
    ),
    (
        "combo: releasing either member ends it, the other stays held",
        CTRL_WIN,
        [("ctrl", True), ("left windows", True), ("ctrl", False), ("left windows", False)],
        [Edge.COMBO_DOWN, Edge.HOTKEY_DOWN, Edge.HOTKEY_UP, Edge.COMBO_UP],
    ),
    (
        "combo: re-pressing the released member fires again",
        CTRL_WIN,
        [("ctrl", True), ("left windows", True), ("ctrl", False), ("ctrl", True)],
        [Edge.COMBO_DOWN, Edge.HOTKEY_DOWN, Edge.HOTKEY_UP, Edge.HOTKEY_DOWN],
    ),
    (
        "a key outside the combo is reported, not swallowed",
        RIGHT_CTRL,
        [("right ctrl", True), ("c", True), ("c", False), ("right ctrl", False)],
        [Edge.HOTKEY_DOWN, Edge.OTHER_DOWN, Edge.OTHER_UP, Edge.HOTKEY_UP],
    ),
    (
        "an up with no matching down is ignored",
        RIGHT_CTRL,
        [("right ctrl", False)],
        [Edge.IGNORED],
    ),
    (
        "left ctrl arrives named plain 'ctrl' and still fires a left-ctrl hotkey",
        LEFT_CTRL,
        [("ctrl", True), ("ctrl", False)],
        [Edge.HOTKEY_DOWN, Edge.HOTKEY_UP],
    ),
    (
        "right ctrl must not fire a left-ctrl hotkey",
        LEFT_CTRL,
        [("right ctrl", True), ("right ctrl", False)],
        [Edge.OTHER_DOWN, Edge.OTHER_UP],
    ),
    (
        "f8: typing while held does not touch the hotkey state",
        F8,
        [("f8", True), ("a", True), ("a", False), ("f8", False)],
        [Edge.HOTKEY_DOWN, Edge.OTHER_DOWN, Edge.OTHER_UP, Edge.HOTKEY_UP],
    ),
]


@pytest.mark.parametrize(
    "spec, events, expected",
    [pytest.param(s, e, x, id=name) for name, s, e, x in EDGE_CASES],
)
def test_edge_detector(spec, events, expected):
    detector = EdgeDetector(spec)
    assert [detector.feed(name, is_down) for name, is_down in events] == expected


def test_events_are_ignored_while_the_injection_gate_is_set():
    gate = threading.Event()
    gate.set()
    detector = EdgeDetector(RIGHT_CTRL, gate=gate, read_pressed=frozenset)
    edges = [detector.feed("right ctrl", True), detector.feed("right ctrl", False)]
    assert edges == [Edge.IGNORED, Edge.IGNORED]
    assert not detector.armed


def test_held_set_is_resynced_after_the_gate_clears():
    """Our own injected Ctrl+V must not be left in the held set, and keys the
    user really is holding must be back in it."""
    gate = threading.Event()
    physical = {"right ctrl"}
    detector = EdgeDetector(
        RIGHT_CTRL, gate=gate, read_pressed=lambda: frozenset(physical)
    )

    assert detector.feed("right ctrl", True) is Edge.HOTKEY_DOWN
    assert detector.feed("right ctrl", False) is Edge.HOTKEY_UP

    gate.set()  # paste_win takes over
    assert detector.feed("ctrl", True) is Edge.IGNORED  # our injected Ctrl
    assert detector.feed("v", True) is Edge.IGNORED
    assert detector.feed("v", False) is Edge.IGNORED
    assert detector.feed("ctrl", False) is Edge.IGNORED
    gate.clear()

    # The user pressed the hotkey again during the paste, so we never saw the
    # down - but the up must still stop the (new) recording.
    assert detector.feed("right ctrl", False) is Edge.HOTKEY_UP
    assert detector.held == frozenset()


def test_a_fresh_press_right_after_the_gate_clears_still_fires():
    """The resync reads the key as already down (the user is pressing it right
    now); that must not be mistaken for autorepeat."""
    gate = threading.Event()
    detector = EdgeDetector(
        RIGHT_CTRL, gate=gate, read_pressed=lambda: frozenset({"right ctrl"})
    )
    gate.set()
    detector.feed("ctrl", True)
    gate.clear()
    assert detector.feed("right ctrl", True) is Edge.HOTKEY_DOWN


def test_resync_forgets_keys_released_during_the_gate():
    gate = threading.Event()
    detector = EdgeDetector(CTRL_WIN, gate=gate, read_pressed=lambda: frozenset({"ctrl"}))
    detector.feed("ctrl", True)
    detector.feed("left windows", True)
    assert detector.armed
    gate.set()
    detector.feed("a", True)
    gate.clear()
    # Windows says only Ctrl is down now, so the combo is no longer armed.
    detector.feed("a", False)
    assert detector.held == frozenset({"ctrl"})
    assert not detector.armed


import vocal_advantage.hotkey_win as hotkey_win
from vocal_advantage.hotkey_win import HotkeyListener


# --------------------------------------------------------------------------
# HotkeyListener
# --------------------------------------------------------------------------


class FakeEvent:
    def __init__(self, name, event_type):
        self.name = name
        self.event_type = event_type
        self.scan_code = 0


class FakeKeyboard:
    """Stands in for the keyboard package: records hooks, replays events."""

    def __init__(self):
        self.hooks = []
        self.suppress_flags = []

    def hook(self, callback, suppress=False):
        self.hooks.append(callback)
        self.suppress_flags.append(suppress)
        return callback

    def unhook(self, handle):
        self.hooks.remove(handle)

    def send(self, name, is_down):
        event = FakeEvent(name, "down" if is_down else "up")
        for callback in list(self.hooks):
            callback(event)


@pytest.fixture
def fake_keyboard(monkeypatch):
    fake = FakeKeyboard()
    monkeypatch.setitem(sys.modules, "keyboard", fake)
    monkeypatch.setattr(hotkey_win, "read_pressed_keys", lambda names: frozenset())
    return fake


def test_listener_forwards_edges_and_drops_autorepeat(fake_keyboard):
    seen = []
    listener = HotkeyListener(RIGHT_CTRL, lambda name, down: seen.append((name, down)), gate=threading.Event())
    listener.start()
    for is_down in (True, True, True, False):
        fake_keyboard.send("right ctrl", is_down)
    listener.stop()
    assert seen == [("right ctrl", True), ("right ctrl", False)]


def test_listener_forwards_other_keys_so_the_controller_can_cancel(fake_keyboard):
    seen = []
    listener = HotkeyListener(RIGHT_CTRL, lambda name, down: seen.append((name, down)), gate=threading.Event())
    listener.start()
    fake_keyboard.send("right ctrl", True)
    fake_keyboard.send("c", True)
    fake_keyboard.send("c", False)
    fake_keyboard.send("right ctrl", False)
    listener.stop()
    assert seen == [
        ("right ctrl", True),
        ("c", True),
        ("c", False),
        ("right ctrl", False),
    ]


def test_listener_never_suppresses(fake_keyboard):
    listener = HotkeyListener(RIGHT_CTRL, lambda name, down: None, gate=threading.Event())
    listener.start()
    listener.stop()
    assert fake_keyboard.suppress_flags == [False]


def test_listener_is_silent_while_pasting_then_resyncs(fake_keyboard, monkeypatch):
    seen = []
    gate = threading.Event()
    listener = HotkeyListener(RIGHT_CTRL, lambda name, down: seen.append((name, down)), gate=gate)
    listener.start()

    gate.set()
    fake_keyboard.send("ctrl", True)
    fake_keyboard.send("v", True)
    fake_keyboard.send("v", False)
    fake_keyboard.send("ctrl", False)
    assert seen == []

    # The user is holding the hotkey again by the time the paste finishes.
    monkeypatch.setattr(hotkey_win, "read_pressed_keys", lambda names: frozenset({"right ctrl"}))
    gate.clear()
    fake_keyboard.send("right ctrl", False)
    listener.stop()
    assert seen == [("right ctrl", False)]


def test_listener_ignores_nameless_events(fake_keyboard):
    seen = []
    listener = HotkeyListener(RIGHT_CTRL, lambda name, down: seen.append((name, down)), gate=threading.Event())
    listener.start()
    fake_keyboard.send(None, True)
    listener.stop()
    assert seen == []


def test_stop_removes_the_hook(fake_keyboard):
    seen = []
    listener = HotkeyListener(RIGHT_CTRL, lambda name, down: seen.append((name, down)), gate=threading.Event())
    listener.start()
    listener.stop()
    assert fake_keyboard.hooks == []
    fake_keyboard.send("right ctrl", True)
    assert seen == []


from vocal_advantage.hotkey_spec import HotkeyError
from vocal_advantage.hotkey_win import _CaptureSession, capture_hotkey


# --------------------------------------------------------------------------
# capture_hotkey - ONE shot. main.py owns the prompt and the retry loop.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "events, expected",
    [
        ([("f8", True), ("f8", False)], frozenset({"f8"})),
        (
            [("ctrl", True), ("left windows", True), ("left windows", False), ("ctrl", False)],
            frozenset({"ctrl", "left windows"}),
        ),
        # A single tap before the real chord must not win.
        (
            [
                ("a", True), ("a", False),
                ("ctrl", True), ("left windows", True),
                ("ctrl", False), ("left windows", False),
            ],
            frozenset({"ctrl", "left windows"}),
        ),
    ],
)
def test_capture_session_records_the_largest_chord(events, expected):
    session = _CaptureSession()
    for name, is_down in events:
        session.feed(name, is_down)
    assert session.largest == expected
    assert session.done


def test_capture_session_is_not_done_while_a_key_is_still_held():
    session = _CaptureSession()
    session.feed("f8", True)
    assert not session.done


class ScriptedKeyboard:
    """Replays one canned chord per hook() call, synchronously.

    It stands in for the whole ``keyboard`` module, so it has to answer
    everything the code under test asks of it -- not just the hook pair.
    ``HotkeySpec._canonical`` also calls ``normalize_name`` and
    ``key_to_scan_codes``, but *only on Windows*: off Windows it returns early
    against the vendored table and never touches the library at all. A double
    missing those two therefore passes everywhere except the one platform the
    app ships on, which is exactly what happened -- macOS was green while
    Windows raised ``AttributeError``.

    Both are backed by ``_key_names``, the table captured from the real library,
    so this double agrees with it for as long as
    ``test_vendored_key_table_matches_the_library`` says the table is current.
    """

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0

    def hook(self, callback, suppress=False):
        script = self.scripts[self.calls] if self.calls < len(self.scripts) else []
        self.calls += 1
        for name, is_down in script:
            callback(FakeEvent(name, "down" if is_down else "up"))
        return callback

    def unhook(self, handle):
        pass

    def normalize_name(self, name):
        """Mirrors ``keyboard.normalize_name``: lower-case, underscores to
        spaces, whitespace collapsed, then the alias map."""
        name = " ".join(name.lower().replace("_", " ").split())
        return _key_names.ALIASES.get(name, name)

    def key_to_scan_codes(self, name):
        """The real one raises ``ValueError`` for a name the layout has no key
        for; the scan codes themselves are never inspected."""
        if name not in _key_names.VALID_NAMES:
            raise ValueError(f"Key name {name!r} is not mapped to any known key.")
        return (0,)


class FakeClock:
    """A monotonic clock we control: the first reading sets the deadline, every
    later reading is far past it, so the wait loop gives up immediately."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return 0.0 if self.calls == 1 else 1_000_000.0


def test_capture_hotkey_returns_the_chord(monkeypatch):
    scripted = ScriptedKeyboard([[("f8", True), ("f8", False)]])
    monkeypatch.setitem(sys.modules, "keyboard", scripted)
    assert capture_hotkey(timeout_s=1.0).keys == frozenset({"f8"})


def test_capture_hotkey_raises_on_a_banned_key(monkeypatch, capsys):
    """One prompt, one raise. main.run_set_hotkey catches this and re-prompts;
    capture_hotkey must not loop, or the CapsLock case never terminates."""
    scripted = ScriptedKeyboard([[("caps lock", True), ("caps lock", False)]])
    monkeypatch.setitem(sys.modules, "keyboard", scripted)

    with pytest.raises(HotkeyError) as caught:
        capture_hotkey(timeout_s=1.0)

    assert "caps lock" in str(caught.value).lower()
    assert scripted.calls == 1  # captured once, did NOT ask again
    # It still echoes what it heard, in HotkeySpec's display spelling.
    assert "You held: Caps Lock" in capsys.readouterr().out


def test_capture_hotkey_times_out(monkeypatch):
    """The wait is driven through the injected clock/sleep, so this is instant
    and deterministic even at the real 15s default."""
    scripted = ScriptedKeyboard([[]])  # hook installed, no key ever arrives
    monkeypatch.setitem(sys.modules, "keyboard", scripted)
    clock = FakeClock()
    slept: list[float] = []

    with pytest.raises(TimeoutError) as caught:
        capture_hotkey(timeout_s=15.0, clock=clock, sleep=slept.append)

    assert "15s" in str(caught.value)
    assert clock.calls == 2  # one to set the deadline, one to find it passed
    assert slept == []       # and it never actually waits
