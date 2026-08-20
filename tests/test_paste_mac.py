"""Tests for vocal_advantage.paste_mac.

The sequence itself lives in paste_core and is covered by test_paste_win.py;
what is tested here is the macOS platform layer and the one thing macOS does
that Windows could not:

**Stamping our own synthetic events.** On Windows there was no way to tell our
injected Ctrl+V from the user's keypresses, so the hook was gated for the whole
paste and then resynced from GetAsyncKeyState. macOS lets us write a magic value
into each event we post, so the key hook can recognise and skip exactly our own.
If that stamp is ever dropped, the hook sees our paste as user input and the
symptom is a dictation that fires a second recording the moment it pastes.

Quartz and NSPasteboard are bound once at import to module-level names, and the
tests replace those names -- the same seam recorder.py uses for sounddevice.
"""

from __future__ import annotations

import threading

import pytest

from vocal_advantage import paste_core, paste_mac


@pytest.fixture(autouse=True)
def _clean_injection_flag():
    paste_mac.injection_active.clear()
    yield
    paste_mac.injection_active.clear()


def test_it_shares_the_one_injection_flag_with_the_key_hook():
    assert paste_mac.injection_active is paste_core.injection_active
    assert isinstance(paste_mac.injection_active, threading.Event)


def test_the_chord_is_command_v_pressed_and_released_in_order():
    assert paste_mac.CMD_V_SEQUENCE == (
        (paste_mac.KEYCODE_COMMAND, True),
        (paste_mac.KEYCODE_V, True),
        (paste_mac.KEYCODE_V, False),
        (paste_mac.KEYCODE_COMMAND, False),
    )


def test_the_keycodes_are_the_ones_macos_actually_uses():
    # Verified against a live event tap on this machine, not from docs.
    assert paste_mac.KEYCODE_COMMAND == 55
    assert paste_mac.KEYCODE_V == 9


# ---------------------------------------------------------------------------
# The sequence, driven with a fake backend and a virtual clock.
# ---------------------------------------------------------------------------


class FakeBackend:
    def __init__(self, *, modifier_polls_held=0, clipboard_failures=0, send_result=1):
        self.now = 0.0
        self.log = []
        self.flag_seen = []
        self.clipboard_text = None
        self.send_result = send_result
        self._modifier_polls_held = modifier_polls_held
        self._clipboard_failures = clipboard_failures

    def _record(self, name, detail):
        self.log.append((name, detail, round(self.now, 3)))
        self.flag_seen.append(paste_mac.injection_active.is_set())

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self._record("sleep", round(seconds, 3))
        self.now += seconds

    def modifiers_down(self):
        held = self._modifier_polls_held > 0
        self._modifier_polls_held -= 1
        self._record("modifiers_down", held)
        return held

    def set_clipboard(self, text):
        if self._clipboard_failures > 0:
            self._clipboard_failures -= 1
            self._record("clipboard_failed", text)
            raise OSError("pasteboard busy")
        self.clipboard_text = text
        self._record("clipboard_set", text)

    def send_key(self, vk, down):
        self._record("key_down" if down else "key_up", vk)
        return self.send_result


def test_the_clipboard_route_still_matches_windows_step_for_step():
    """Kept as the fallback for long dictations, where one atomic paste beats
    typing hundreds of characters. Same delays as Windows: paste_core owns
    them for both."""
    fake = FakeBackend()

    assert paste_mac.paste_via_clipboard("hello world", backend=fake) is True

    assert fake.log == [
        ("modifiers_down", False, 0.0),
        ("clipboard_set", "hello world", 0.0),
        ("sleep", 0.1, 0.0),
        ("key_down", paste_mac.KEYCODE_COMMAND, 0.1),
        ("sleep", 0.02, 0.1),
        ("key_down", paste_mac.KEYCODE_V, 0.12),
        ("sleep", 0.02, 0.12),
        ("key_up", paste_mac.KEYCODE_V, 0.14),
        ("sleep", 0.02, 0.14),
        ("key_up", paste_mac.KEYCODE_COMMAND, 0.16),
        ("sleep", 0.06, 0.16),
    ]


def test_it_waits_for_a_held_modifier_before_pasting():
    # The hotkey IS a modifier (Right Option by default). Letting go a fraction
    # late would otherwise turn Cmd+V into Option+Cmd+V.
    fake = FakeBackend(modifier_polls_held=2)

    assert paste_mac.paste_via_clipboard("hello", backend=fake) is True

    assert [e[0] for e in fake.log[:5]] == [
        "modifiers_down", "sleep", "modifiers_down", "sleep", "modifiers_down",
    ]


def test_the_gate_is_up_throughout_the_clipboard_route_too():
    fake = FakeBackend()
    paste_mac.paste_via_clipboard("hello", backend=fake)
    assert fake.flag_seen and all(fake.flag_seen)
    assert not paste_mac.injection_active.is_set()


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_text_is_never_pasted(text):
    fake = FakeBackend()
    assert paste_mac.paste_text(text, backend=fake) is False
    assert fake.log == []


def test_the_gate_is_cleared_even_if_the_backend_explodes():
    """A stuck gate would deafen the key hook permanently -- the hotkey would
    stop working until restart. Both routes must survive it."""
    class ExplodingTyper(TypingFakeBackend):
        def send_text(self, text):
            raise RuntimeError("boom")

    class ExplodingPaster(FakeBackend):
        def send_key(self, vk, down):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        paste_mac.paste_text("hello", backend=ExplodingTyper())
    assert not paste_mac.injection_active.is_set()

    with pytest.raises(RuntimeError):
        paste_mac.paste_via_clipboard("hello", backend=ExplodingPaster())
    assert not paste_mac.injection_active.is_set()


# ---------------------------------------------------------------------------
# The macOS layer: fake Quartz / NSPasteboard
# ---------------------------------------------------------------------------


class FakeEvent:
    def __init__(self, keycode, down):
        self.keycode = keycode
        self.down = down
        self.flags = 0
        self.fields = {}
        self.unicode = None


class FakeQuartz:
    kCGEventFlagMaskCommand = 1 << 20
    kCGEventFlagMaskShift = 1 << 17
    kCGEventFlagMaskControl = 1 << 18
    kCGEventFlagMaskAlternate = 1 << 19
    kCGEventSourceUserData = 42
    kCGHIDEventTap = 0
    kCGEventSourceStateHIDSystemState = 1

    def __init__(self, flags_state=0):
        self.posted = []
        self.flags_state = flags_state

    def CGEventCreateKeyboardEvent(self, source, keycode, down):
        return FakeEvent(keycode, down)

    def CGEventSetFlags(self, event, flags):
        event.flags = flags

    def CGEventSetIntegerValueField(self, event, field, value):
        event.fields[field] = value

    def CGEventGetIntegerValueField(self, event, field):
        return event.fields.get(field, 0)

    def CGEventKeyboardSetUnicodeString(self, event, length, string):
        event.unicode = string

    def CGEventPost(self, tap, event):
        self.posted.append(event)

    def CGEventSourceFlagsState(self, state):
        return self.flags_state


class FakePasteboard:
    def __init__(self):
        self.cleared = 0
        self.contents = {}

    def clearContents(self):
        self.cleared += 1

    def setString_forType_(self, value, type_):
        self.contents[type_] = value
        return True


@pytest.fixture
def fake_quartz(monkeypatch):
    fake = FakeQuartz()
    monkeypatch.setattr(paste_mac, "Quartz", fake)
    return fake


def test_every_injected_event_is_stamped_so_the_hook_can_skip_it(fake_quartz):
    backend = paste_mac.MacBackend()
    backend.send_key(paste_mac.KEYCODE_V, True)

    event = fake_quartz.posted[0]
    assert event.fields[FakeQuartz.kCGEventSourceUserData] == paste_mac.INJECTED_MARKER


def test_the_v_keystroke_carries_the_command_flag(fake_quartz):
    """Posting Cmd-down does not implicitly flag later events; it must be set."""
    backend = paste_mac.MacBackend()
    for keycode, down in paste_mac.CMD_V_SEQUENCE:
        backend.send_key(keycode, down)

    by_code = [(e.keycode, e.down, bool(e.flags & FakeQuartz.kCGEventFlagMaskCommand))
               for e in fake_quartz.posted]
    assert by_code == [
        (paste_mac.KEYCODE_COMMAND, True, True),
        (paste_mac.KEYCODE_V, True, True),    # <- the one that matters
        (paste_mac.KEYCODE_V, False, True),
        (paste_mac.KEYCODE_COMMAND, False, False),
    ]


def test_is_injected_recognises_our_events_and_nothing_else(fake_quartz):
    ours = FakeEvent(paste_mac.KEYCODE_V, True)
    ours.fields[FakeQuartz.kCGEventSourceUserData] = paste_mac.INJECTED_MARKER
    theirs = FakeEvent(paste_mac.KEYCODE_V, True)

    assert paste_mac.is_injected(ours) is True
    assert paste_mac.is_injected(theirs) is False


@pytest.mark.parametrize(
    "flags, expected",
    [
        (0, False),
        (FakeQuartz.kCGEventFlagMaskCommand, True),
        (FakeQuartz.kCGEventFlagMaskShift, True),
        (FakeQuartz.kCGEventFlagMaskControl, True),
        (FakeQuartz.kCGEventFlagMaskAlternate, True),  # Option: the default hotkey
    ],
)
def test_modifiers_down_reads_the_live_flag_state(monkeypatch, flags, expected):
    fake = FakeQuartz(flags_state=flags)
    monkeypatch.setattr(paste_mac, "Quartz", fake)
    assert paste_mac.MacBackend().modifiers_down() is expected


def test_the_clipboard_gets_the_text_and_the_concealed_marker(monkeypatch):
    """org.nspasteboard.ConcealedType is the convention clipboard managers
    honour: it is the macOS analogue of the three Windows privacy formats, and
    privacy is the product."""
    board = FakePasteboard()
    monkeypatch.setattr(paste_mac, "_general_pasteboard", lambda: board)

    paste_mac._set_clipboard("secret words")

    assert board.cleared == 1
    assert board.contents[paste_mac.PASTEBOARD_TYPE_STRING] == "secret words"
    assert paste_mac.PASTEBOARD_TYPE_CONCEALED in board.contents


def test_a_refused_clipboard_write_raises_oserror_so_it_gets_retried(monkeypatch):
    class Refusing(FakePasteboard):
        def setString_forType_(self, value, type_):
            return False

    monkeypatch.setattr(paste_mac, "_general_pasteboard", lambda: Refusing())
    with pytest.raises(OSError):
        paste_mac._set_clipboard("hi")


# ---------------------------------------------------------------------------
# Typing the text directly, which never touches the clipboard at all
# ---------------------------------------------------------------------------


class TypingFakeBackend(FakeBackend):
    """Adds the one method the direct-typing route needs."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.typed = []

    def send_text(self, text):
        self._record("typed", text)
        self.typed.append(text)
        return True


def test_typing_never_touches_the_clipboard():
    """The whole point. Your clipboard is yours; a dictation should not
    silently replace whatever you had copied."""
    fake = TypingFakeBackend()

    assert paste_mac.paste_text("hello world", backend=fake) is True

    assert fake.clipboard_text is None
    assert [e[0] for e in fake.log] == ["modifiers_down", "typed", "sleep"]
    assert fake.typed == ["hello world"]


def test_typing_still_waits_for_a_held_modifier():
    """Critical here, not merely tidy: the hotkey IS Command. Typing while it
    is still physically down turns every character into a keyboard shortcut."""
    fake = TypingFakeBackend(modifier_polls_held=2)

    assert paste_mac.paste_text("hello", backend=fake) is True

    assert [e[0] for e in fake.log[:5]] == [
        "modifiers_down", "sleep", "modifiers_down", "sleep", "modifiers_down",
    ]


def test_the_gate_is_up_while_typing_and_cleared_after():
    fake = TypingFakeBackend()
    paste_mac.paste_text("hello", backend=fake)
    assert fake.flag_seen and all(fake.flag_seen)
    assert not paste_mac.injection_active.is_set()


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_text_is_never_typed(text):
    fake = TypingFakeBackend()
    assert paste_mac.paste_text(text, backend=fake) is False
    assert fake.log == []


def test_short_text_goes_in_a_single_event(fake_quartz):
    paste_mac.MacBackend().send_text("hello")
    assert len(fake_quartz.posted) == 2  # one key-down, one key-up
    assert fake_quartz.posted[0].unicode == "hello"


def test_long_text_is_split_into_chunks(fake_quartz):
    text = "x" * (paste_mac.TYPE_CHUNK_CHARS * 3 + 5)
    paste_mac.MacBackend().send_text(text)

    chunks = [e.unicode for e in fake_quartz.posted if e.down]
    assert "".join(chunks) == text, "every character must arrive, in order"
    assert all(len(c) <= paste_mac.TYPE_CHUNK_CHARS for c in chunks)


def test_typed_events_are_stamped_so_our_own_hook_skips_them(fake_quartz):
    paste_mac.MacBackend().send_text("hi")
    for event in fake_quartz.posted:
        assert event.fields[FakeQuartz.kCGEventSourceUserData] == paste_mac.INJECTED_MARKER


def test_typed_events_carry_no_modifier_flags(fake_quartz):
    """A stale Command flag would turn the text into a run of shortcuts."""
    paste_mac.MacBackend().send_text("hi")
    assert all(event.flags == 0 for event in fake_quartz.posted)


def test_unicode_survives_being_typed(fake_quartz):
    paste_mac.MacBackend().send_text("naive cafe -- ni hao")
    typed = "".join(e.unicode for e in fake_quartz.posted if e.down)
    assert typed == "naive cafe -- ni hao"


def test_typing_a_partial_never_raises_the_injection_gate():
    """The live-dictation case, and the gate would be actively harmful here.

    During a partial the user is still HOLDING the hotkey. The gate makes the
    key hook ignore every event while it is up -- so if it were raised when they
    released the key, the release would be swallowed and the recording would
    never stop. Stamping each event is what keeps our own keystrokes out of the
    hook instead, and unlike the gate it is exact.
    """
    fake = TypingFakeBackend()

    assert paste_mac.type_partial("hello", backend=fake) is True

    assert not any(fake.flag_seen), "the gate must stay down for a partial"
    assert not paste_mac.injection_active.is_set()
    assert fake.typed == ["hello"]


def test_a_partial_still_waits_for_no_modifier_it_can_control():
    """It cannot wait for the hotkey to be released -- that is the whole point,
    the user is still holding it. So a partial types straight away."""
    fake = TypingFakeBackend(modifier_polls_held=10**6)

    assert paste_mac.type_partial("hello", backend=fake) is True

    assert [e[0] for e in fake.log] == ["typed"]


def test_the_final_paste_still_raises_the_gate():
    fake = TypingFakeBackend()
    paste_mac.paste_text("hello", backend=fake)
    assert all(fake.flag_seen)
