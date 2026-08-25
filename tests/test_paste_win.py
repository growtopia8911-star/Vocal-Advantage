"""Tests for vocal_advantage.paste_win.

The module imports cleanly on any OS (the Windows DLLs are loaded lazily,
inside Win32Backend), so everything here runs anywhere except the two tests
explicitly marked Windows-only.
"""

import ctypes
import sys
import threading

import pytest

from vocal_advantage import paste_win


IS_64BIT = ctypes.sizeof(ctypes.c_size_t) == 8


@pytest.fixture(autouse=True)
def _clean_injection_flag():
    """injection_active is module-global state; never leak it between tests."""
    paste_win.injection_active.clear()
    yield
    paste_win.injection_active.clear()


def test_injection_active_is_a_threading_event_and_starts_clear():
    assert isinstance(paste_win.injection_active, threading.Event)
    assert not paste_win.injection_active.is_set()


@pytest.mark.parametrize(
    "name, expected",
    [
        ("CF_UNICODETEXT", 13),
        ("GMEM_MOVEABLE", 0x0002),
        ("INPUT_KEYBOARD", 1),
        ("KEYEVENTF_KEYUP", 0x0002),
        ("MAPVK_VK_TO_VSC", 0),
        ("VK_SHIFT", 0x10),
        ("VK_CONTROL", 0x11),
        ("VK_MENU", 0x12),
        ("VK_LWIN", 0x5B),
        ("VK_RWIN", 0x5C),
        ("VK_LCONTROL", 0xA2),
        ("VK_V", 0x56),
    ],
)
def test_win32_constants_have_their_documented_values(name, expected):
    assert getattr(paste_win, name) == expected


def test_modifier_vks_cover_ctrl_shift_alt_and_both_win_keys():
    # SPEC.md step 2: wait until Ctrl/Shift/Alt/Win are all released.
    assert set(paste_win.MODIFIER_VKS) == {0x11, 0x10, 0x12, 0x5B, 0x5C}


def test_privacy_formats_are_the_three_documented_names():
    assert paste_win.PRIVACY_FORMATS == (
        "ExcludeClipboardContentFromMonitorProcessing",
        "CanIncludeInClipboardHistory",
        "CanUploadToCloudClipboard",
    )


def test_keybdinput_field_layout():
    kb = paste_win.KEYBDINPUT
    assert [f[0] for f in kb._fields_] == [
        "wVk",
        "wScan",
        "dwFlags",
        "time",
        "dwExtraInfo",
    ]
    assert (kb.wVk.offset, kb.wVk.size) == (0, 2)
    assert (kb.wScan.offset, kb.wScan.size) == (2, 2)
    assert (kb.dwFlags.offset, kb.dwFlags.size) == (4, 4)
    assert (kb.time.offset, kb.time.size) == (8, 4)
    assert kb.dwExtraInfo.offset == (16 if IS_64BIT else 12)
    assert kb.dwExtraInfo.size == ctypes.sizeof(ctypes.c_size_t)


def test_struct_sizes_match_the_win32_abi():
    # SendInput compares its cbSize argument against its own sizeof(INPUT).
    # A mismatch makes it return 0 and insert nothing, with no exception.
    assert ctypes.sizeof(paste_win.MOUSEINPUT) == (32 if IS_64BIT else 24)
    assert ctypes.sizeof(paste_win.KEYBDINPUT) == (24 if IS_64BIT else 16)
    assert ctypes.sizeof(paste_win.INPUT) == (40 if IS_64BIT else 28)
    assert paste_win.INPUT.type.offset == 0


def test_input_union_is_anonymous_so_ki_is_reachable_directly():
    event = paste_win.INPUT(type=paste_win.INPUT_KEYBOARD)
    event.ki.wVk = paste_win.VK_V
    assert event.type == paste_win.INPUT_KEYBOARD
    assert event.ki.wVk == paste_win.VK_V
    assert event.ki.dwFlags == 0


def test_timings_match_the_spec():
    assert paste_win.MODIFIER_WAIT_S == 2.0
    assert paste_win.MODIFIER_POLL_S == 0.01
    assert paste_win.CLIPBOARD_ATTEMPTS == 5
    assert paste_win.CLIPBOARD_RETRY_S == 0.05
    assert paste_win.CLIPBOARD_SETTLE_S == 0.1
    assert paste_win.KEY_INTERVAL_S == 0.02
    assert paste_win.POST_PASTE_S == 0.06


# ---------------------------------------------------------------------------
# The Windows layer, driven with fake user32 / kernel32 objects.
# ---------------------------------------------------------------------------


class FakeKernel32:
    """GlobalAlloc/Lock/Unlock/Free backed by real ctypes buffers.

    The handle we return IS the address of a real block of memory, so the
    production code's memmove is a genuine write and payload() can read the
    exact bytes Windows would have received.
    """

    def __init__(self):
        self.blocks = {}
        self.freed = []

    def GlobalAlloc(self, flags, size):
        assert flags == paste_win.GMEM_MOVEABLE
        buf = ctypes.create_string_buffer(size)
        handle = ctypes.addressof(buf)
        self.blocks[handle] = buf  # keeps the buffer alive
        return handle

    def GlobalLock(self, handle):
        return handle

    def GlobalUnlock(self, handle):
        return 0

    def GlobalFree(self, handle):
        self.freed.append(handle)
        return None

    def payload(self, handle):
        return self.blocks[handle].raw


class FakeUser32:
    def __init__(
        self,
        kernel32,
        *,
        open_result=1,
        empty_result=1,
        failing_formats=(),
        unregisterable=(),
    ):
        self.kernel32 = kernel32
        self.calls = []
        self.data = {}
        self.registered = {}
        self.key_state = {}
        self.sent = []
        self.send_result = 1
        self.open_result = open_result
        self.empty_result = empty_result
        self.failing_formats = set(failing_formats)
        self.unregisterable = set(unregisterable)

    # -- clipboard
    def OpenClipboard(self, hwnd):
        self.calls.append(("OpenClipboard", hwnd))
        return self.open_result

    def EmptyClipboard(self):
        self.calls.append(("EmptyClipboard",))
        self.data.clear()
        return self.empty_result

    def RegisterClipboardFormatW(self, name):
        self.calls.append(("RegisterClipboardFormatW", name))
        if name in self.unregisterable:
            return 0
        return self.registered.setdefault(name, 0xC001 + len(self.registered))

    def SetClipboardData(self, fmt, handle):
        self.calls.append(("SetClipboardData", fmt))
        if fmt in self.failing_formats:
            return None
        self.data[fmt] = self.kernel32.payload(handle)
        return handle

    def CloseClipboard(self):
        self.calls.append(("CloseClipboard",))
        return 1

    # -- input
    def GetAsyncKeyState(self, vk):
        return self.key_state.get(vk, 0)

    def MapVirtualKeyW(self, vk, map_type):
        assert map_type == paste_win.MAPVK_VK_TO_VSC
        return {paste_win.VK_LCONTROL: 0x1D, paste_win.VK_V: 0x2F}[vk]

    def SendInput(self, count, pointer, size):
        # Copy out of the pointer immediately: the caller's INPUT is a local
        # that may be collected as soon as SendInput returns.
        copy = paste_win.INPUT()
        ctypes.memmove(ctypes.byref(copy), pointer, ctypes.sizeof(paste_win.INPUT))
        self.sent.append((count, copy, size))
        return self.send_result


@pytest.mark.parametrize(
    "key_state, expected",
    [
        ({}, False),
        ({paste_win.VK_CONTROL: 0x8000}, True),
        # GetAsyncKeyState returns a SHORT, so "down" arrives as a negative
        # number. This is exactly why restype must be c_short and the code
        # must mask with 0x8000 rather than test for truthiness.
        ({paste_win.VK_SHIFT: -32768}, True),
        ({paste_win.VK_MENU: 0x8000}, True),
        ({paste_win.VK_LWIN: 0x8000}, True),
        ({paste_win.VK_RWIN: 0x8000}, True),
        # Bit 0 means "was pressed since the last call", not "is down now".
        ({paste_win.VK_CONTROL: 0x0001}, False),
        ({paste_win.VK_CONTROL: 0x0001, paste_win.VK_MENU: 0x8001}, True),
    ],
)
def test_modifiers_down_reads_only_the_high_bit(key_state, expected):
    user32 = FakeUser32(FakeKernel32())
    user32.key_state = key_state
    assert paste_win._modifiers_down(user32) is expected


def test_set_clipboard_writes_unicode_text_and_three_privacy_formats():
    kernel32 = FakeKernel32()
    user32 = FakeUser32(kernel32)

    paste_win._set_clipboard(user32, kernel32, "héllo wörld")

    expected_text = "héllo wörld".encode("utf-16-le") + b"\x00\x00"
    assert user32.data[paste_win.CF_UNICODETEXT] == expected_text
    for name in paste_win.PRIVACY_FORMATS:
        fmt = user32.registered[name]
        assert user32.data[fmt] == b"\x00\x00\x00\x00"
    # Once SetClipboardData succeeds the system owns the memory; freeing it
    # here would corrupt the clipboard.
    assert kernel32.freed == []


def test_set_clipboard_call_order():
    kernel32 = FakeKernel32()
    user32 = FakeUser32(kernel32)

    paste_win._set_clipboard(user32, kernel32, "hi")

    assert user32.calls[0] == ("OpenClipboard", None)
    assert user32.calls[1] == ("EmptyClipboard",)
    assert user32.calls[2] == ("SetClipboardData", paste_win.CF_UNICODETEXT)
    assert user32.calls[-1] == ("CloseClipboard",)
    assert user32.calls.count(("EmptyClipboard",)) == 1
    assert [c[1] for c in user32.calls if c[0] == "RegisterClipboardFormatW"] == list(
        paste_win.PRIVACY_FORMATS
    )


def test_set_clipboard_raises_oserror_when_open_fails_and_does_not_close():
    kernel32 = FakeKernel32()
    user32 = FakeUser32(kernel32, open_result=0)

    with pytest.raises(OSError):
        paste_win._set_clipboard(user32, kernel32, "hi")

    # Closing a clipboard you never opened would close somebody else's.
    assert ("CloseClipboard",) not in user32.calls


def test_set_clipboard_wipes_and_closes_when_the_text_cannot_be_set():
    kernel32 = FakeKernel32()
    user32 = FakeUser32(kernel32, failing_formats={paste_win.CF_UNICODETEXT})

    with pytest.raises(OSError):
        paste_win._set_clipboard(user32, kernel32, "hi")

    assert len(kernel32.freed) == 1  # the block the clipboard refused
    assert user32.calls[-1] == ("CloseClipboard",)
    assert user32.data == {}


def test_set_clipboard_wipes_the_text_when_a_privacy_format_fails():
    # Clipboard history snapshots the session at CloseClipboard. If we cannot
    # mark the entry private we must not leave the dictation behind.
    kernel32 = FakeKernel32()
    user32 = FakeUser32(kernel32, unregisterable={"CanUploadToCloudClipboard"})

    with pytest.raises(OSError):
        paste_win._set_clipboard(user32, kernel32, "secret")

    assert user32.calls.count(("EmptyClipboard",)) == 2
    assert user32.data == {}
    assert user32.calls[-1] == ("CloseClipboard",)


@pytest.mark.parametrize(
    "vk, down, expected_scan, expected_flags",
    [
        (paste_win.VK_LCONTROL, True, 0x1D, 0),
        (paste_win.VK_V, True, 0x2F, 0),
        (paste_win.VK_V, False, 0x2F, paste_win.KEYEVENTF_KEYUP),
        (paste_win.VK_LCONTROL, False, 0x1D, paste_win.KEYEVENTF_KEYUP),
    ],
)
def test_send_key_builds_one_keyboard_input_event(vk, down, expected_scan, expected_flags):
    user32 = FakeUser32(FakeKernel32())

    inserted = paste_win._send_key(user32, vk, down)

    assert inserted == 1
    count, event, size = user32.sent[0]
    assert count == 1
    assert size == ctypes.sizeof(paste_win.INPUT)
    assert event.type == paste_win.INPUT_KEYBOARD
    assert event.ki.wVk == vk
    assert event.ki.wScan == expected_scan
    assert event.ki.dwFlags == expected_flags
    assert event.ki.time == 0
    assert event.ki.dwExtraInfo == 0


def test_send_key_returns_the_count_sendinput_reported():
    # 0 means UIPI blocked us. paste_text turns that into a False return.
    user32 = FakeUser32(FakeKernel32())
    user32.send_result = 0
    assert paste_win._send_key(user32, paste_win.VK_V, True) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="loads user32.dll")
def test_win32_backend_talks_to_the_real_dlls():
    backend = paste_win.Win32Backend()
    assert isinstance(backend.modifiers_down(), bool)
    assert isinstance(backend.monotonic(), float)


def test_win_error_wraps_its_message_without_the_windows_last_error():
    """``ctypes.get_last_error`` exists only on Windows.

    The clipboard retry logic is built to catch OSError, so on any platform
    without that function this must still return an OSError rather than raise
    AttributeError -- otherwise the three failure paths above are untestable
    off Windows, and the module docstring's "imports and runs on any OS" claim
    is false for exactly the paths that matter.
    """
    error = paste_win._win_error("OpenClipboard failed")
    assert isinstance(error, OSError)
    assert "OpenClipboard failed" in str(error)


# ---------------------------------------------------------------------------
# The paste sequence itself, driven with a fake backend and a virtual clock.
# ---------------------------------------------------------------------------


class FakeBackend:
    """Records every operation with the virtual time at which it happened.

    Log entries are (name, detail, time). `flag_seen` records whether
    injection_active was set at each of those moments.
    """

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
        self.flag_seen.append(paste_win.injection_active.is_set())

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
            raise OSError(0, "OpenClipboard failed (WinError 5)", None, 5)
        self.clipboard_text = text
        self._record("clipboard_set", text)

    def get_clipboard(self):
        """What the user had copied before the dictation (spec 10c)."""
        self._record("clipboard_get", self.clipboard_text)
        return self.clipboard_text

    def send_key(self, vk, down):
        self._record("key_down" if down else "key_up", vk)
        return self.send_result


def entries(fake, name):
    return [e for e in fake.log if e[0] == name]


def test_paste_sequence_order_and_timing():
    fake = FakeBackend()
    fake.clipboard_text = "prior contents"   # something to save and put back

    assert paste_win.paste_text("hello world", backend=fake) is True

    assert fake.log == [
        ("modifiers_down", False, 0.0),
        ("clipboard_get", "prior contents", 0.0),  # 10c: read before overwriting
        ("clipboard_set", "hello world", 0.0),
        ("sleep", 0.1, 0.0),  # apps fetch the clipboard lazily
        ("key_down", paste_win.VK_LCONTROL, 0.1),
        ("sleep", 0.02, 0.1),
        ("key_down", paste_win.VK_V, 0.12),
        ("sleep", 0.02, 0.12),
        ("key_up", paste_win.VK_V, 0.14),
        ("sleep", 0.02, 0.14),
        ("key_up", paste_win.VK_LCONTROL, 0.16),
        ("sleep", 0.06, 0.16),  # let the hook swallow our own key events
        ("sleep", 0.25, 0.22),  # let the app fetch it before taking it back
        ("clipboard_set", "prior contents", 0.47),  # 10c: and put it back
    ]
    # The transcript is gone again; the user's clipboard survived the dictation.
    assert fake.clipboard_text == "prior contents"


def test_injection_flag_is_set_throughout_and_cleared_at_the_end():
    fake = FakeBackend()

    paste_win.paste_text("hello", backend=fake)

    assert fake.flag_seen  # sanity: the fake did record something
    assert all(fake.flag_seen), "the hook must be gated for the whole sequence"
    assert not paste_win.injection_active.is_set()


def test_it_waits_for_physically_held_modifiers_before_pasting():
    # Injected keystrokes combine with keys the user is still holding, so a
    # held Shift would turn Ctrl+V into Ctrl+Shift+V.
    fake = FakeBackend(modifier_polls_held=3)

    assert paste_win.paste_text("hello", backend=fake) is True

    assert fake.log[:8] == [
        ("modifiers_down", True, 0.0),
        ("sleep", 0.01, 0.0),
        ("modifiers_down", True, 0.01),
        ("sleep", 0.01, 0.01),
        ("modifiers_down", True, 0.02),
        ("sleep", 0.01, 0.02),
        ("modifiers_down", False, 0.03),
        # The clipboard is read only once the modifiers are clear -- the wait
        # comes first, which is what this test is pinning.
        ("clipboard_get", None, 0.03),
    ]


def test_it_gives_up_waiting_after_two_seconds_and_pastes_anyway():
    fake = FakeBackend(modifier_polls_held=10**6)

    assert paste_win.paste_text("hello", backend=fake) is True

    clipboard_at = entries(fake, "clipboard_set")[0][2]
    assert clipboard_at == pytest.approx(2.0, abs=0.05)
    assert len(entries(fake, "key_down")) == 2


def test_clipboard_is_retried_five_times_fifty_milliseconds_apart():
    # OpenClipboard loses a race with the Win+V clipboard-history process and
    # returns WinError 5; retrying is the documented fix.
    fake = FakeBackend(clipboard_failures=4)

    assert paste_win.paste_text("hello", backend=fake) is True

    assert len(entries(fake, "clipboard_failed")) == 4
    assert len(entries(fake, "clipboard_set")) == 1
    assert [e[2] for e in entries(fake, "sleep") if e[1] == 0.05] == [0.0, 0.05, 0.1, 0.15]
    assert entries(fake, "clipboard_set")[0][2] == 0.2


def test_it_gives_up_after_five_clipboard_attempts():
    fake = FakeBackend(clipboard_failures=5)

    assert paste_win.paste_text("hello", backend=fake) is False

    assert len(entries(fake, "clipboard_failed")) == 5
    assert len([e for e in entries(fake, "sleep") if e[1] == 0.05]) == 4
    assert entries(fake, "key_down") == []  # never press Ctrl+V with stale data
    assert not paste_win.injection_active.is_set()


def test_blocked_sendinput_returns_false_but_still_releases_ctrl():
    # UIPI: nothing was inserted, so report failure rather than raising - the
    # text is on the clipboard and the user can press Ctrl+V. The sequence
    # still runs to the end so a partially accepted Ctrl is never left down.
    #
    # The plan's draft asserted `A or B` where A compared fake.log[-5:] against
    # a list ending in key_up LCONTROL. A can never hold: the log always ends
    # with the POST_PASTE_S sleep, so the last five entries are shifted by one
    # and A is False for both a working and a broken implementation. Only the
    # key-sequence assertion is kept, which is the one with teeth.
    fake = FakeBackend(send_result=0)

    assert paste_win.paste_text("hello", backend=fake) is False

    assert [e[:2] for e in fake.log if e[0].startswith("key")] == [
        ("key_down", paste_win.VK_LCONTROL),
        ("key_down", paste_win.VK_V),
        ("key_up", paste_win.VK_V),
        ("key_up", paste_win.VK_LCONTROL),
    ]
    assert not paste_win.injection_active.is_set()


@pytest.mark.parametrize("text", ["", "   ", "\n", "\t \n"])
def test_blank_text_is_never_pasted(text):
    fake = FakeBackend()

    assert paste_win.paste_text(text, backend=fake) is False

    assert fake.log == []
    assert not paste_win.injection_active.is_set()


def test_unicode_survives_to_the_clipboard():
    fake = FakeBackend()

    paste_win.paste_text("naïve café — 你好", backend=fake)

    assert fake.clipboard_text == "naïve café — 你好"


def test_the_flag_is_cleared_even_if_the_backend_explodes():
    # A stuck injection_active would deafen the hotkey hook permanently.
    class Exploding(FakeBackend):
        def send_key(self, vk, down):
            raise RuntimeError("boom")

    fake = Exploding()

    with pytest.raises(RuntimeError):
        paste_win.paste_text("hello", backend=fake)

    assert not paste_win.injection_active.is_set()
