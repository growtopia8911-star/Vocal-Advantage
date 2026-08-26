"""Tests for the wiring in vocal_advantage/main.py.

Three things here are worth more than the rest:

1. `main.py` must not drag faster-whisper in at import time. The CUDA DLL
   directories have to be registered by `cuda_dlls.prepare()` *before*
   ctranslate2 loads, and Python 3.8+ ignores PATH for DLL resolution, so an
   import at the top of main.py would be unfixable at runtime.
2. The controller loop must serialise hook events and still tick the watchdog
   while idle.
3. `--set-hotkey` must never damage config.json when the key is refused.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from unittest import mock
from pathlib import Path

import numpy as np
import pytest

from vocal_advantage import cleanup as va_cleanup
from vocal_advantage import history as va_history
from vocal_advantage import main as va_main
from vocal_advantage.hotkey_spec import HotkeyError, parse_hotkey


@pytest.fixture(autouse=True)
def never_open_a_real_microphone(monkeypatch):
    """Stop any test that drives a launcher from opening the real device.

    The microphone is opened at startup now, not on the first keypress, so
    ``_run_app_windows`` / ``_run_app_mac`` reach a real PortAudio stream long
    before the point most of these tests stop them. That stream then outlives
    the test -- ``stop()`` deliberately no longer closes it -- and PortAudio's
    callback thread eventually segfaults the whole session while garbage
    collecting, several tests later and nowhere near the cause.

    Autouse rather than per-test: the failure lands so far from its origin that
    finding it a second time would cost what it cost the first time.
    """
    monkeypatch.setattr(
        va_main, "_open_microphone", lambda recorder: None, raising=False
    )

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_child(script: str) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter rooted at the repo.

    A subprocess is the only honest way to assert "module X was not imported":
    inside the pytest process another test may already have imported it.
    """
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------
# Import ordering — the load-bearing rule of this file
# --------------------------------------------------------------------------

IMPORT_PURITY_SCRIPT = """
import sys
import vocal_advantage.main  # noqa: F401

too_early = [
    name
    for name in ("vocal_advantage.transcriber", "faster_whisper", "ctranslate2")
    if name in sys.modules
]
assert not too_early, "imported before cuda_dlls.prepare() could run: %r" % (too_early,)
print("OK")
"""


def test_importing_main_does_not_pull_in_the_whisper_stack():
    result = _run_child(IMPORT_PURITY_SCRIPT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


ORDERING_SCRIPT = """
import sys
import types

import vocal_advantage.main as m

# Stand-in for faster_whisper so this test does not need the real 200MB wheel
# or a GPU; it is installed *after* the purity check above has already run.
stub = types.ModuleType("faster_whisper")


class WhisperModel:
    def __init__(self, *args, **kwargs):
        pass


stub.WhisperModel = WhisperModel
sys.modules["faster_whisper"] = stub

seen = []


def fake_prepare():
    seen.append("vocal_advantage.transcriber" in sys.modules)


cls = m.import_transcriber_class(prepare=fake_prepare)

assert seen == [False], "prepare() did not run before the transcriber import: %r" % (seen,)
assert cls.__name__ == "Transcriber"
print("OK")
"""


def test_prepare_runs_before_the_transcriber_module_is_imported():
    result = _run_child(ORDERING_SCRIPT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# --------------------------------------------------------------------------
# The -m entry point
# --------------------------------------------------------------------------


def test_python_dash_m_entry_point_reports_the_version():
    result = subprocess.run(
        [sys.executable, "-m", "vocal_advantage", "--version"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert va_main.VERSION in result.stdout


def test_version_flag_prints_the_version_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        va_main.build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert va_main.VERSION in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv, expected",
    [
        ([], False),
        (["--set-hotkey"], True),
    ],
)
def test_set_hotkey_flag_parsing(argv, expected):
    assert va_main.build_parser().parse_args(argv).set_hotkey is expected


# --------------------------------------------------------------------------
# Single-instance mutex
# --------------------------------------------------------------------------


def _close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="the lock is a Win32 named mutex")
def test_second_lock_on_the_same_name_is_refused_and_released_on_close():
    name = r"Local\VocalAdvantageTest_%d" % os.getpid()

    first = va_main.acquire_single_instance_lock(name)
    assert first is not None, "first acquire should succeed"
    try:
        assert va_main.acquire_single_instance_lock(name) is None, (
            "a second acquire must report the name is already taken"
        )
    finally:
        _close_handle(first)

    # Once the only handle is closed the name is free again, which is what makes
    # relaunching after a crash work.
    again = va_main.acquire_single_instance_lock(name)
    assert again is not None
    _close_handle(again)


# --------------------------------------------------------------------------
# Controller loop: hook thread -> queue -> controller thread
# --------------------------------------------------------------------------


class FakeController:
    """Records what the loop hands it. No state machine — that is Task 9's test."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, bool]] = []
        self.ticks = 0

    def on_key_event(self, key_name: str, is_down: bool) -> None:
        with self._lock:
            self.events.append((key_name, is_down))

    def tick(self) -> None:
        with self._lock:
            self.ticks += 1

    def snapshot(self) -> tuple[list[tuple[str, bool]], int]:
        with self._lock:
            return list(self.events), self.ticks


def _start_loop(controller, events, stop, **kwargs) -> threading.Thread:
    thread = threading.Thread(
        target=va_main.controller_loop,
        args=(controller, events, stop),
        kwargs=kwargs,
        daemon=True,
    )
    thread.start()
    return thread


def test_controller_loop_delivers_events_in_the_order_they_were_queued():
    controller = FakeController()
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    thread = _start_loop(controller, events, stop)

    sent = [("right ctrl", True), ("c", True), ("c", False), ("right ctrl", False)]
    for item in sent:
        events.put(item)
    events.put(None)  # sentinel: stop

    thread.join(timeout=5.0)
    assert not thread.is_alive(), "the sentinel must end the loop"
    seen, _ = controller.snapshot()
    assert seen == sent


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class IdleQueue:
    """A queue that is always empty and burns its timeout on the fake clock.

    This is what makes the idle-tick test deterministic. The plan's draft slept
    a real 0.4s and asserted a wall-clock tick count between 3 and 40, which is
    both slow and flaky on a loaded machine. Advancing the injected clock by
    exactly the timeout the loop asked for models the same thing precisely: the
    loop *did* block for that long, and we know exactly how long it asked for.
    """

    def __init__(self, clock: FakeClock, stop: threading.Event, expiries: int) -> None:
        self.clock = clock
        self.stop = stop
        self.remaining = expiries
        self.timeouts: list[float] = []

    def get(self, timeout=None):
        self.timeouts.append(timeout)
        self.clock.advance(timeout)  # the loop really did wait this long
        self.remaining -= 1
        if self.remaining <= 0:
            self.stop.set()
        raise queue.Empty


def test_controller_loop_ticks_once_per_interval_while_idle_and_blocks_between_ticks():
    controller = FakeController()
    clock = FakeClock()
    stop = threading.Event()
    events = IdleQueue(clock, stop, expiries=8)

    # Runs on this thread: no sleeping, no joining, nothing to race.
    va_main.controller_loop(
        controller, events, stop, clock=clock, tick_interval_s=0.05
    )

    seen, ticks = controller.snapshot()
    assert seen == [], "an idle loop must not invent key events"
    assert ticks == 8, "one watchdog tick per interval, not one per spin"
    # Every get() asked to block for the whole remaining interval. A busy-spin
    # would show a stream of near-zero timeouts here instead.
    assert events.timeouts == [pytest.approx(0.05)] * 8


def test_controller_loop_does_not_tick_more_than_once_per_interval_under_load():
    controller = FakeController()
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    thread = _start_loop(controller, events, stop, tick_interval_s=10.0)

    for index in range(200):
        events.put(("f8", index % 2 == 0))
    events.put(None)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    seen, ticks = controller.snapshot()
    assert len(seen) == 200
    assert ticks <= 1, "200 key events must not trigger 200 watchdog ticks (%d)" % ticks


def test_controller_loop_survives_an_exception_from_the_controller():
    class Exploding(FakeController):
        def on_key_event(self, key_name, is_down):
            super().on_key_event(key_name, is_down)
            if key_name == "boom":
                raise RuntimeError("transcription blew up")

    controller = Exploding()
    events: queue.Queue = queue.Queue()
    stop = threading.Event()
    thread = _start_loop(controller, events, stop)

    events.put(("boom", True))
    events.put(("f8", True))
    events.put(None)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    seen, _ = controller.snapshot()
    assert seen == [("boom", True), ("f8", True)], (
        "one failed dictation must not take the hotkey down with it"
    )


# --------------------------------------------------------------------------
# --set-hotkey
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["f8", "right ctrl", "ctrl+win"])
def test_run_set_hotkey_saves_a_re_parseable_hotkey_and_echoes_the_friendly_name(
    tmp_path, capsys, text
):
    config_path = tmp_path / "config.json"
    spec = parse_hotkey(text)

    exit_code = va_main.run_set_hotkey(lambda: spec, config_path)

    assert exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert parse_hotkey(saved["hotkey"]).keys == spec.keys, (
        "what we wrote to config.json must parse back to the same key set"
    )
    assert str(spec) in capsys.readouterr().out


def test_run_set_hotkey_reprompts_after_a_refused_key_and_then_saves():
    attempts_left = [
        HotkeyError("the Windows key on its own opens the Start menu when you let go"),
        parse_hotkey("ctrl+win"),
    ]

    def capture():
        item = attempts_left.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        config_path = Path(folder) / "config.json"
        exit_code = va_main.run_set_hotkey(capture, config_path)

    assert exit_code == 0
    assert attempts_left == [], "it should have used both captures"


def test_run_set_hotkey_leaves_the_config_untouched_when_every_key_is_refused(
    tmp_path, capsys
):
    config_path = tmp_path / "config.json"
    original = json.dumps({"hotkey": "right ctrl", "language": "en"}, indent=2)
    config_path.write_text(original, encoding="utf-8")

    calls = []

    def capture():
        calls.append(1)
        raise HotkeyError("caps lock only works if we block the key, and we never do")

    exit_code = va_main.run_set_hotkey(capture, config_path, attempts=3)

    assert exit_code == 1
    assert len(calls) == 3
    assert config_path.read_text(encoding="utf-8") == original, (
        "a refused key must not rewrite config.json"
    )
    assert "caps lock" in capsys.readouterr().err


def test_run_set_hotkey_reports_a_timeout_instead_of_ending_in_a_stack_trace(
    tmp_path, capsys
):
    """capture_hotkey raises TimeoutError when nothing is held in time.

    Task 6 states this as part of its contract and leaves the handling here.
    Uncaught, --set-hotkey ends in a traceback; and there is no point asking
    again, because a timeout means nobody is at the keyboard.
    """
    config_path = tmp_path / "config.json"
    original = json.dumps({"hotkey": "right ctrl", "language": "en"}, indent=2)
    config_path.write_text(original, encoding="utf-8")

    calls = []

    def capture():
        calls.append(1)
        raise TimeoutError("No key held within 15s - hotkey left unchanged.")

    exit_code = va_main.run_set_hotkey(capture, config_path, attempts=3)

    assert exit_code == 1
    assert len(calls) == 1, "a timeout must not be retried three times"
    assert config_path.read_text(encoding="utf-8") == original
    err = capsys.readouterr().err
    assert "15s" in err
    assert "Hotkey unchanged." in err


# --------------------------------------------------------------------------
# run_app: DPI awareness has to be set before the first window exists
# --------------------------------------------------------------------------


def test_run_app_sets_dpi_awareness_before_anything_could_make_a_window(
    tmp_path, monkeypatch
):
    """Windows fixes a process's DPI awareness when its first window appears.

    Called after the fact it is a silent no-op returning E_ACCESSDENIED: the
    pill renders blurry on a scaled display and the "bottom centre of the
    screen" arithmetic is wrong above 100% scaling. Neither flowbar_win nor
    pystray calls it for us, so _run_app_windows is the single production call
    site and the ordering is only checkable here.

    The run is stopped at the model load, which sits between the DPI call and
    every window this app can create -- the tray icon and the Flow Bar are both
    built well after it. So reaching this point with the call already made is
    what proves the ordering.
    """
    from vocal_advantage import flowbar_win

    order: list[str] = []
    monkeypatch.setattr(
        flowbar_win, "set_dpi_awareness", lambda: order.append("set_dpi_awareness")
    )

    class StopBeforeAnyWindowExists(Exception):
        pass

    def stop():
        order.append("model load - no window yet")
        raise StopBeforeAnyWindowExists

    monkeypatch.setattr(va_main, "import_transcriber_class", stop)

    with pytest.raises(StopBeforeAnyWindowExists):
        # The Windows launcher specifically: run_app dispatches by platform,
        # and this is a Windows requirement. Nothing real is touched, so it
        # still runs anywhere.
        va_main._run_app_windows(tmp_path / "config.json")

    assert order == ["set_dpi_awareness", "model load - no window yet"]


def test_run_app_on_windows_does_not_import_tkinter(tmp_path, monkeypatch):
    """Tk is gone from this project.

    It is what used to own the main thread on Windows, which is why the tray
    icon could not. Rendering the pill with UpdateLayeredWindow removed it, and
    an accidental re-import would quietly take the main thread back.
    """
    import tkinter

    touched = []
    monkeypatch.setattr(tkinter, "Tk", lambda *a, **k: touched.append("Tk"))

    class StopBeforeTheModelLoads(Exception):
        pass

    def stop():
        raise StopBeforeTheModelLoads

    monkeypatch.setattr(va_main, "import_transcriber_class", stop)

    with pytest.raises(StopBeforeTheModelLoads):
        va_main._run_app_windows(tmp_path / "config.json")

    assert touched == []


# --------------------------------------------------------------------------
# Platform selection
# --------------------------------------------------------------------------


def test_the_platform_modules_match_the_host():
    """One codebase, two machines. main is the only place that chooses."""
    hotkey, paste = va_main.platform_modules()
    if sys.platform == "darwin":
        assert hotkey.__name__.endswith("hotkey_mac")
        assert paste.__name__.endswith("paste_mac")
    else:
        assert hotkey.__name__.endswith("hotkey_win")
        assert paste.__name__.endswith("paste_win")


def test_both_platform_hotkey_modules_offer_the_same_two_entry_points():
    """--set-hotkey must work on both, or choosing your own key is
    Windows-only. This is the contract main.py depends on."""
    from vocal_advantage import hotkey_mac, hotkey_win

    for module in (hotkey_win, hotkey_mac):
        assert hasattr(module, "HotkeyListener"), module.__name__
        assert hasattr(module, "capture_hotkey"), module.__name__


def test_both_platform_paste_modules_expose_paste_text():
    from vocal_advantage import paste_mac, paste_win

    for module in (paste_win, paste_mac):
        assert callable(module.paste_text), module.__name__


# --------------------------------------------------------------------------
# The console indicator that stands in for the pill on macOS
# --------------------------------------------------------------------------


def test_the_console_indicator_satisfies_everything_the_controller_calls():
    """controller.py must not change for the port, so this has to answer to the
    same four methods the Windows pill does."""
    indicator = va_main.ConsoleIndicator()
    for method in ("show_recording", "show_processing", "hide", "flash"):
        assert callable(getattr(indicator, method)), method
    # And none of them may raise: the controller calls them inside its own
    # try/finally, but a throwing indicator would still break a dictation.
    indicator.show_recording()
    indicator.show_processing()
    indicator.flash("nothing heard")
    indicator.hide()


def test_the_console_indicator_says_something_useful_while_recording(capsys):
    indicator = va_main.ConsoleIndicator()
    indicator.show_recording()
    assert capsys.readouterr().out.strip() != ""


# --------------------------------------------------------------------------
# Single-instance lock, on whichever platform we are
# --------------------------------------------------------------------------


def test_a_second_instance_is_refused_on_this_platform():
    """Two instances would both hook the keyboard and both paste -- every
    dictation would land twice."""
    name = "VocalAdvantageTest_%d" % os.getpid()

    first = va_main.acquire_single_instance_lock(name)
    assert first is not None, "the first acquire should succeed"
    try:
        assert va_main.acquire_single_instance_lock(name) is None, (
            "a second acquire must report the name is already taken"
        )
    finally:
        va_main.release_single_instance_lock(first)

    # Freeing it makes relaunching after a crash work.
    again = va_main.acquire_single_instance_lock(name)
    assert again is not None
    va_main.release_single_instance_lock(again)


def test_run_app_on_mac_never_touches_tkinter(tmp_path, monkeypatch):
    """With no pill there is no reason to build a Tk root, and doing so would
    put a Python rocket in the Dock and steal focus on launch."""
    import tkinter

    touched = []
    monkeypatch.setattr(tkinter, "Tk", lambda *a, **k: touched.append("Tk"))

    class StopBeforeTheModelLoads(Exception):
        pass

    def stop():
        raise StopBeforeTheModelLoads

    monkeypatch.setattr(va_main, "import_transcriber_class", stop)

    with pytest.raises(StopBeforeTheModelLoads):
        va_main._run_app_mac(tmp_path / "config.json")

    assert touched == []


def test_run_app_dispatches_to_the_right_platform(monkeypatch):
    calls = []
    monkeypatch.setattr(va_main, "_run_app_mac", lambda p: calls.append("mac") or 0)
    monkeypatch.setattr(va_main, "_run_app_windows", lambda p: calls.append("win") or 0)

    va_main.run_app(Path("ignored.json"))

    assert calls == ["mac" if sys.platform == "darwin" else "win"]


# --------------------------------------------------------------------------
# Reporting what was heard
# --------------------------------------------------------------------------


class _EchoTranscriber:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


def test_the_narrating_transcriber_returns_the_text_untouched():
    """It is a reporter, not a participant: whatever the real transcriber says
    must reach the controller byte for byte."""
    import numpy as np

    inner = _EchoTranscriber("hello there")
    wrapped = va_main.NarratingTranscriber(inner)

    assert wrapped.transcribe(np.zeros(16000, dtype=np.float32)) == "hello there"
    assert inner.calls == 1


def test_it_reports_the_transcript_and_how_long_it_took(capsys):
    import numpy as np

    wrapped = va_main.NarratingTranscriber(_EchoTranscriber("hello there"))
    wrapped.transcribe(np.zeros(32000, dtype=np.float32))  # 2.0s at 16kHz

    out = capsys.readouterr().out
    assert "hello there" in out, "we need to see what it heard"
    assert "2.0s" in out, "and how much audio that was"


def test_it_survives_a_transcriber_that_raises(capsys):
    """The controller already handles a failing transcriber; the reporter must
    not turn that into a different, unhandled failure."""
    import numpy as np

    class Exploding:
        def transcribe(self, audio):
            raise RuntimeError("boom")

    wrapped = va_main.NarratingTranscriber(Exploding())
    with pytest.raises(RuntimeError):
        wrapped.transcribe(np.zeros(16000, dtype=np.float32))


def test_it_passes_warm_up_through():
    class Warms:
        def __init__(self):
            self.warmed = False

        def warm_up(self):
            self.warmed = True

        def transcribe(self, audio):
            return ""

    inner = Warms()
    va_main.NarratingTranscriber(inner).warm_up()
    assert inner.warmed is True


# --------------------------------------------------------------------------
# Cleaning: which text-to-text pass config selects
# --------------------------------------------------------------------------
#
# The live-dictation section that used to sit here went with the feature. The
# cleanup pass it wrapped now runs inside the controller instead, once, on the
# stitched transcript -- see tests/test_controller_modes.py.




class _RecordingPaster:
    def __init__(self, ok=True):
        self.pasted = []
        self.ok = ok

    def paste_text(self, text):
        self.pasted.append(text)
        return self.ok


def test_the_rules_cleaner_is_the_default():
    assert va_main._cleaner({}) is va_cleanup.clean_speech


def test_switching_cleaning_off_gives_the_raw_transcript():
    cleaner = va_main._cleaner({"clean_speech": False})
    assert cleaner("Um, hello") == "Um, hello"


def test_enabling_ai_cleanup_selects_the_model_pass():
    assert va_main._cleaner({"ai_cleanup": True}) is va_cleanup.ai_clean


def test_ai_cleanup_off_beats_ai_cleanup_on_when_cleaning_is_off_entirely():
    """clean_speech=false means raw. The AI pass would clean it anyway."""
    cleaner = va_main._cleaner({"clean_speech": False, "ai_cleanup": True})
    assert cleaner("Um, hello") == "Um, hello"


# --------------------------------------------------------------------------
# The UI is decoration: losing it must never cost dictation
# --------------------------------------------------------------------------


class _Recorder:
    def __init__(self, recording=True, explode=False):
        self.is_recording = recording
        self.stopped = False
        self.closed = False
        self._explode = explode

    def stop(self):
        if self._explode:
            raise RuntimeError("the mic was unplugged")
        self.stopped = True

    def close(self):
        """The stream now outlives a dictation, so shutdown closes it."""
        if self._explode:
            raise RuntimeError("the mic was unplugged")
        self.closed = True


class _Listener:
    def __init__(self, explode=False):
        self.stopped = False
        self._explode = explode

    def stop(self):
        if self._explode:
            raise RuntimeError("the hook was already gone")
        self.stopped = True


class _Worker:
    def __init__(self):
        self.joined = False

    def join(self, timeout=None):
        self.joined = True

    def is_alive(self):
        return False


def test_the_flow_bar_is_skipped_when_config_switches_it_off(capsys):
    cfg = {"flow_bar": False, "flow_bar_position": "bottom-centre"}
    assert va_main._make_flow_bar(cfg, object()) is None
    assert "tray icon only" in capsys.readouterr().out


def test_a_flow_bar_that_will_not_start_is_survivable(monkeypatch, capsys):
    """Dictation is the product; the overlay is decoration."""
    import vocal_advantage.flowbar_mac as flowbar_mac
    import vocal_advantage.flowbar_win as flowbar_win

    def explode(*args, **kwargs):
        raise RuntimeError("no window server")

    monkeypatch.setattr(flowbar_mac, "FlowBar", explode)
    monkeypatch.setattr(flowbar_win, "FlowBar", explode)

    cfg = {"flow_bar": True, "flow_bar_position": "bottom-centre"}
    assert va_main._make_flow_bar(cfg, object()) is None
    assert "unaffected" in capsys.readouterr().err


def test_a_tray_icon_that_will_not_start_is_survivable(monkeypatch, capsys):
    import vocal_advantage.tray_mac as tray_mac
    import vocal_advantage.tray_win as tray_win

    def explode(*args, **kwargs):
        raise RuntimeError("no status bar")

    monkeypatch.setattr(tray_mac, "TrayIcon", explode)
    monkeypatch.setattr(tray_win, "TrayIcon", explode)

    assert va_main._make_tray(object(), lambda: None) is None
    assert "unaffected" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Quit: "shuts down cleanly ... no orphan processes"
# --------------------------------------------------------------------------


def test_quit_releases_the_keyboard_the_mic_and_the_thread():
    events: "queue.Queue" = queue.Queue()
    stop_event = threading.Event()
    listener, recorder, worker = _Listener(), _Recorder(), _Worker()

    va_main._stop_dictation(listener, recorder, worker, events, stop_event)

    assert listener.stopped, "the hotkey hook is still installed"
    assert stop_event.is_set(), "the controller thread was never told to stop"
    assert worker.joined, "the controller thread was never waited for"
    assert recorder.stopped, "the microphone stream is still open"


def test_quit_queues_the_sentinel_that_unblocks_a_waiting_loop():
    # Without it the controller thread sits in queue.get() forever and the
    # join() above times out -- the exact shape of an orphan.
    events: "queue.Queue" = queue.Queue()
    va_main._stop_dictation(
        _Listener(), _Recorder(), _Worker(), events, threading.Event()
    )
    assert events.get_nowait() is None


def test_quit_does_not_stop_a_recorder_that_was_not_recording():
    recorder = _Recorder(recording=False)
    va_main._stop_dictation(
        _Listener(), recorder, _Worker(), queue.Queue(), threading.Event()
    )
    assert not recorder.stopped


def test_quit_still_releases_the_mic_when_the_hotkey_hook_fails(capsys):
    # Order matters: the listener is stopped first, and if that raising skipped
    # the rest, the microphone light would stay on after quitting.
    recorder = _Recorder()
    va_main._stop_dictation(
        _Listener(explode=True), recorder, _Worker(), queue.Queue(),
        threading.Event(),
    )
    assert recorder.stopped


def test_quit_survives_a_microphone_that_fails_to_close(capsys):
    va_main._stop_dictation(
        _Listener(), _Recorder(explode=True), _Worker(), queue.Queue(),
        threading.Event(),
    )
    assert "the mic was unplugged" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "darwin", reason="the Cocoa run loop")
def test_quit_on_mac_stops_the_run_loop_so_the_process_can_exit(monkeypatch):
    """Tearing down without stopping the loop leaves an invisible orphan.

    No icon, no window, still running -- which is precisely what "no orphan
    processes" rules out, and impossible to notice by looking.
    """
    from PyObjCTools import AppHelper

    order = []
    monkeypatch.setattr(AppHelper, "stopEventLoop", lambda: order.append("stop loop"))

    va_main._quit_mac(lambda: order.append("shutdown"))()

    assert order == ["shutdown", "stop loop"]


# --------------------------------------------------------------------------
# The personal dictionary, wired into the cleaning chain
# --------------------------------------------------------------------------


class _Dict:
    """Stands in for dictionary.Dictionary."""

    def __init__(self, fixes=None, words=None):
        self.fixes = fixes or {}
        self.words = words or []
        self.hotwords = ", ".join(self.words)

    def __bool__(self):
        return bool(self.fixes or self.words)

    def apply(self, text):
        for wrong, right in self.fixes.items():
            text = text.replace(wrong, right)
        return text


def test_no_dictionary_leaves_the_cleaner_exactly_as_it_was():
    cfg = {"clean_speech": False, "ai_cleanup": False}
    assert va_main._cleaner(cfg, None)("um so kelvin") == "um so kelvin"


def test_the_dictionary_corrects_the_text():
    cfg = {"clean_speech": False, "ai_cleanup": False}
    clean = va_main._cleaner(cfg, _Dict({"kelvin": "Kevin"}))
    assert clean("send it to kelvin") == "send it to Kevin"


def test_corrections_apply_even_with_cleaning_switched_off():
    # "Give me the raw transcript" is a request about fillers and stutters,
    # not permission to keep spelling a name wrong.
    cfg = {"clean_speech": False, "ai_cleanup": False}
    clean = va_main._cleaner(cfg, _Dict({"kelvin": "Kevin"}))
    assert "Kevin" in clean("um, kelvin")


def test_corrections_run_after_the_cleanup_pass_not_before():
    """The ordering that matters, and the reason it is not the other way round.

    A cleanup pass rewrites whole sentences. Correcting first would let it put
    the mistake back, and the fix would look like it had never happened.
    """
    order = []

    def fake_clean(text):
        order.append("cleanup")
        return text

    dictionary = _Dict({"kelvin": "Kevin"})
    real_apply = dictionary.apply

    def watched_apply(text):
        order.append("dictionary")
        return real_apply(text)

    dictionary.apply = watched_apply

    cfg = {"clean_speech": True, "ai_cleanup": False}
    with mock.patch.object(va_main, "clean_speech", fake_clean):
        va_main._cleaner(cfg, dictionary)("kelvin")

    assert order == ["cleanup", "dictionary"]


def test_an_ai_pass_cannot_undo_a_correction():
    # The concrete version of the test above: a model that "helpfully" spells
    # the name the way it heard it must not get the last word.
    cfg = {"clean_speech": True, "ai_cleanup": True}
    dictionary = _Dict({"Kelvin": "Kevin"})
    with mock.patch.object(va_main, "ai_clean", lambda text: "Tell Kelvin."):
        assert va_main._cleaner(cfg, dictionary)("tell kevin") == "Tell Kevin."


def test_an_empty_dictionary_does_not_wrap_the_cleaner():
    # Falsey dictionaries are skipped entirely, so an empty dictionary.json
    # costs nothing per dictation.
    cfg = {"clean_speech": False, "ai_cleanup": False}
    assert va_main._cleaner(cfg, _Dict()) is not None
    assert va_main._cleaner(cfg, _Dict())("kelvin") == "kelvin"


def test_loading_a_dictionary_reports_what_it_found(tmp_path, capsys):
    path = tmp_path / "dictionary.json"
    path.write_text(
        '{"words": ["Obsidian"], "fixes": {"kelvin": "Kevin"}}', encoding="utf-8"
    )
    loaded = va_main._load_dictionary(path)
    assert loaded.hotwords == "Obsidian"
    assert "1 word" in capsys.readouterr().out


def test_an_empty_dictionary_says_nothing(tmp_path, capsys):
    va_main._load_dictionary(tmp_path / "dictionary.json")
    assert "dictionary" not in capsys.readouterr().out.lower()


def test_a_dictionary_that_explodes_never_stops_the_app(tmp_path, capsys):
    def explode(_path):
        raise RuntimeError("disk on fire")

    with mock.patch.object(va_main, "load_dictionary", explode):
        loaded = va_main._load_dictionary(tmp_path / "dictionary.json")

    assert not loaded
    assert loaded.apply("kelvin") == "kelvin"
    assert loaded.hotwords == ""
    assert "carrying on without it" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Skipping the cleanup pass in terminals and code editors
# --------------------------------------------------------------------------


def test_the_rewriting_half_of_cleanup_is_skipped_in_a_listed_app():
    # What must not happen in a shell: `Git status` does not run, and
    # `--exclude --exclude` is not a stutter to be tidied away. Fillers are a
    # separate question -- see test_fillers_are_removed_in_a_skipped_app.
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: "Terminal"):
        assert clean("git status") == "git status"
        assert clean("git git status") == "git git status"


def test_cleanup_still_runs_everywhere_else():
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: "Notes"):
        assert clean("Um, hello") != "Um, hello"


def test_the_dictionary_still_applies_in_a_skipped_app():
    # Skipping *cleanup* is not the same as agreeing to spell your name wrong.
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg, _Dict({"kelvin": "Kevin"}))
    with mock.patch.object(va_main, "frontmost_app", lambda: "Terminal"):
        assert clean("ssh kelvin") == "ssh Kevin"


def test_an_unknown_app_gets_the_normal_cleanup():
    # frontmost_app() returning None means "could not tell". The safe failure
    # is cleaning where you would rather it had not, never skipping everywhere.
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: None):
        assert clean("Um, hello") != "Um, hello"


def test_the_app_is_checked_per_dictation_not_once_at_startup():
    # The whole point is which window is about to receive this paste, and that
    # changes constantly. Reading it once would pin the answer to whatever had
    # focus when the app launched.
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)

    app = ["Notes"]
    with mock.patch.object(va_main, "frontmost_app", lambda: app[0]):
        # Capitalisation is the half that switches off, so it is what tells
        # the two apps apart.
        assert clean("um hello") == "Hello"
        app[0] = "Terminal"
        assert clean("um hello") == "hello"


def test_no_skip_list_leaves_the_cleaner_untouched():
    # Preserves the original contract: with nothing to wrap, _cleaner returns
    # the cleanup function itself rather than a closure around it.
    assert va_main._cleaner({"skip_cleanup_in": []}) is va_cleanup.clean_speech


# --------------------------------------------------------------------------
# v0.5: sounds follow the state machine, history keeps what was said
# --------------------------------------------------------------------------


class _Player:
    def __init__(self):
        self.played = []

    def play(self, kind):
        self.played.append(kind)


class _Indicator:
    def __init__(self):
        self.calls = []

    def show_recording(self):
        self.calls.append("recording")

    def show_processing(self):
        self.calls.append("processing")

    def hide(self):
        self.calls.append("hide")

    def flash(self, message):
        self.calls.append(("flash", message))

    def status_text(self):
        return "Idle"

    def set_keys(self, hotkey, cancel_key):
        self.calls.append(("set_keys", hotkey, cancel_key))


def _sounding():
    inner, player = _Indicator(), _Player()
    return va_main.SoundingIndicator(inner, player), inner, player


def test_a_finished_dictation_plays_the_done_tone():
    sounding, _, player = _sounding()
    sounding.show_recording()
    sounding.show_processing()
    sounding.hide()
    assert player.played[-1] == "done"


def test_a_cancelled_recording_does_not_claim_success():
    # hide() also fires on cancel and on a tap too short to transcribe. A
    # success tone for either would be a lie, and this is why "done" is not
    # simply wired to hide().
    sounding, _, player = _sounding()
    sounding.show_recording()
    sounding.hide()
    assert "done" not in player.played


def test_a_failure_plays_the_error_tone():
    sounding, _, player = _sounding()
    sounding.show_recording()
    sounding.show_processing()
    sounding.flash("nothing heard")
    assert player.played[-1] == "error"


def test_an_error_is_not_followed_by_a_success_tone():
    sounding, _, player = _sounding()
    sounding.show_recording()
    sounding.show_processing()
    sounding.flash("could not paste")
    sounding.hide()
    assert player.played.count("done") == 0


def test_the_wrapper_passes_everything_through_to_the_real_indicator():
    # The pill must behave identically whether sounds are on or off.
    sounding, inner, _ = _sounding()
    sounding.show_recording()
    sounding.show_processing()
    sounding.flash("nothing heard")
    sounding.hide()
    assert inner.calls == [
        "recording", "processing", ("flash", "nothing heard"), "hide"
    ]


def test_the_tray_can_still_read_the_status_through_the_wrapper():
    sounding, _, _ = _sounding()
    assert sounding.status_text() == "Idle"


def test_two_dictations_in_a_row_each_get_their_own_tone():
    sounding, _, player = _sounding()
    for _ in range(2):
        sounding.show_recording()
        sounding.show_processing()
        sounding.hide()
    assert player.played.count("done") == 2


# --- history ---------------------------------------------------------------


class _Paster:
    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.pasted = []

    def paste_text(self, text):
        self.pasted.append(text)
        return self.succeeds


def test_a_dictation_is_written_to_the_history(tmp_path):
    history = va_history.History(tmp_path / "h.jsonl")
    paster = va_main.RecordingPaster(_Paster(), history)
    paster.paste_text("hello there")
    assert "hello there" in (tmp_path / "h.jsonl").read_text(encoding="utf-8")


def test_a_dictation_is_recorded_even_when_the_paste_fails(tmp_path):
    # The whole point of the history is the dictation that did NOT arrive
    # where it was meant to. Recording only on success loses exactly that case.
    history = va_history.History(tmp_path / "h.jsonl")
    paster = va_main.RecordingPaster(_Paster(succeeds=False), history)
    assert paster.paste_text("words that went missing") is False
    assert "went missing" in (tmp_path / "h.jsonl").read_text(encoding="utf-8")


def test_the_paste_result_is_passed_through_unchanged(tmp_path):
    history = va_history.History(tmp_path / "h.jsonl", enabled=False)
    assert va_main.RecordingPaster(_Paster(True), history).paste_text("x") is True
    assert va_main.RecordingPaster(_Paster(False), history).paste_text("x") is False


def test_history_can_be_switched_off(tmp_path):
    path = tmp_path / "h.jsonl"
    history = va_history.History(path, enabled=False)
    va_main.RecordingPaster(_Paster(), history).paste_text("hello")
    assert not path.exists()


def test_the_player_is_built_from_config():
    assert va_main._make_player({"sounds": False}).enabled is False
    assert va_main._make_player({"sounds": True}).enabled is True
    assert va_main._make_player({"sound_on_start": True}).on_start is True


def test_the_start_tone_is_off_unless_asked_for():
    # It plays while the microphone is open; on speakers it feeds back in.
    assert va_main._make_player({}).on_start is False


# --------------------------------------------------------------------------
# "Change hotkey" from the tray menu
# --------------------------------------------------------------------------
#
# --set-hotkey cannot be used while the app is running: it installs its own
# keyboard hook, and the single-instance lock exists to stop two hooks fighting
# over the keyboard. So the change happens in place.
#
# THE PROPERTY THAT MATTERS: the hotkey always comes back. Losing it means
# losing the app, with no way to recover except quitting from the tray.


class _FakeHook:
    started = []

    def __init__(self, spec=None, on_key=None):
        self.spec = spec
        self.stopped = False
        self.start_error = None

    def start(self):
        if self.start_error:
            raise self.start_error
        type(self).started.append(self.spec)

    def stop(self):
        self.stopped = True


class _Ctl:
    def __init__(self):
        self.hotkey = None

    def set_hotkey(self, spec):
        self.hotkey = spec


class _HotkeyModule:
    """Stands in for hotkey_mac / hotkey_win."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.HotkeyListener = _FakeHook

    def capture_hotkey(self, *_a, **_k):
        if self.error:
            raise self.error
        return self.result


def _changer(tmp_path, module, spec_text="right ctrl"):
    _FakeHook.started = []
    original = parse_hotkey(spec_text)
    controller = _Ctl()
    indicator = _Indicator()
    config_path = tmp_path / "config.json"
    changer = va_main.HotkeyChanger(
        listener=_FakeHook(original),
        spec=original,
        on_key=lambda *_: None,
        controller=controller,
        indicator=indicator,
        config_path=config_path,
    )
    return changer, controller, indicator, config_path


def _run(changer, module, monkeypatch):
    monkeypatch.setattr(va_main, "platform_modules", lambda: (module, None))
    changer.PROMPT_EVERY_S = 0.01
    changer._change()


def test_a_new_hotkey_is_saved_and_swapped_in(tmp_path, monkeypatch):
    module = _HotkeyModule(result=parse_hotkey("f8"))
    changer, controller, _, config_path = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    assert controller.hotkey == parse_hotkey("f8")
    assert json.loads(config_path.read_text(encoding="utf-8"))["hotkey"] == "f8"


def test_the_saved_hotkey_can_be_read_back_by_the_parser(tmp_path, monkeypatch):
    # Stored as raw key names, not str(spec)'s display form, or the next launch
    # cannot parse its own config.
    module = _HotkeyModule(result=parse_hotkey("right ctrl+f8"))
    changer, _, _, config_path = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    stored = json.loads(config_path.read_text(encoding="utf-8"))["hotkey"]
    assert parse_hotkey(stored).keys == parse_hotkey("right ctrl+f8").keys


def test_the_old_hook_is_stopped_before_capture(tmp_path, monkeypatch):
    # Both hooks live at once would mean the app starts recording while you
    # are choosing which key to use.
    module = _HotkeyModule(result=parse_hotkey("f8"))
    changer, _, _, _ = _changer(tmp_path, module)
    original = changer.listener

    _run(changer, module, monkeypatch)

    assert original.stopped


def test_a_listener_is_running_again_afterwards(tmp_path, monkeypatch):
    module = _HotkeyModule(result=parse_hotkey("f8"))
    changer, _, _, _ = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    assert _FakeHook.started == [parse_hotkey("f8")]


def test_a_refused_key_restores_the_working_hotkey(tmp_path, monkeypatch, capsys):
    module = _HotkeyModule(error=HotkeyError("that key cannot be used"))
    changer, controller, _, _ = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    assert _FakeHook.started == [parse_hotkey("right ctrl")]
    assert controller.hotkey is None, "the controller must not have been changed"
    assert "unchanged" in capsys.readouterr().err


def test_a_timeout_restores_the_working_hotkey(tmp_path, monkeypatch):
    # Nobody at the keyboard. The app must not be left with no hotkey.
    module = _HotkeyModule(error=TimeoutError("nobody pressed anything"))
    changer, _, _, _ = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    assert _FakeHook.started == [parse_hotkey("right ctrl")]


def test_an_unexpected_crash_in_capture_still_restores_the_hotkey(
    tmp_path, monkeypatch
):
    module = _HotkeyModule(error=RuntimeError("the tap died"))
    changer, _, _, _ = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    assert _FakeHook.started == [parse_hotkey("right ctrl")]


def test_a_config_that_cannot_be_saved_keeps_the_old_hotkey(
    tmp_path, monkeypatch, capsys
):
    module = _HotkeyModule(result=parse_hotkey("f8"))
    changer, controller, _, _ = _changer(tmp_path, module)
    monkeypatch.setattr(
        va_main, "save_config",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")),
    )

    _run(changer, module, monkeypatch)

    assert controller.hotkey is None
    assert _FakeHook.started == [parse_hotkey("right ctrl")]
    assert "keeping the old one" in capsys.readouterr().err


def test_a_hook_that_will_not_restart_says_so_loudly(tmp_path, monkeypatch, capsys):
    module = _HotkeyModule(result=parse_hotkey("f8"))
    changer, _, indicator, _ = _changer(tmp_path, module)

    class Broken(_FakeHook):
        def start(self):
            raise RuntimeError("the hook is gone")

    module.HotkeyListener = Broken
    _run(changer, module, monkeypatch)

    captured = capsys.readouterr().err
    assert "could not be restarted" in captured
    assert ("flash", "hotkey lost - restart") in indicator.calls


def test_the_live_listener_is_the_one_shutdown_will_stop(tmp_path, monkeypatch):
    # "Change hotkey" replaces the listener object. Stopping the original at
    # shutdown would leave the live hook installed after the app exits.
    module = _HotkeyModule(result=parse_hotkey("f8"))
    changer, _, _, _ = _changer(tmp_path, module)
    original = changer.listener

    _run(changer, module, monkeypatch)

    assert changer.listener is not original


def test_a_second_request_while_waiting_is_ignored(tmp_path, capsys):
    module = _HotkeyModule(result=parse_hotkey("f8"))
    changer, _, _, _ = _changer(tmp_path, module)
    changer._busy.set()

    changer.request()

    assert "Already waiting" in capsys.readouterr().out


def test_a_hotkey_change_reaches_the_indicator(tmp_path, monkeypatch):
    """Gate 2e. Before this fix the Stop cap kept showing the OLD key until
    the app restarted: `Indicator` had no setter, and the tray's "Change
    hotkey" path never told it about the swap. `controller.set_hotkey`
    already had a place the new spec fanned out to; the Indicator needs to be
    another one."""
    module = _HotkeyModule(result=parse_hotkey("ctrl+alt+d"))
    changer, _, indicator, _ = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    # CANCEL_KEY itself ("esc", lowercase), not `str(parse_hotkey("esc"))`
    # ("Esc") -- the exact literal the construction sites already pass as the
    # Cancel cap's text, so the runtime path must match it, not "improve" it.
    assert (
        "set_keys", str(parse_hotkey("ctrl+alt+d")), va_main.CANCEL_KEY
    ) in indicator.calls


def test_a_hotkey_change_to_esc_drops_the_cancel_control(tmp_path, monkeypatch):
    """CRITICAL: same rule the construction sites use --
    `"" if CANCEL_KEY in spec.keys else CANCEL_KEY` -- must be applied on the
    runtime path too, not just at startup. A Cancel control that cannot fire
    (the hotkey takes precedence in `_handle_down`) would be a lie."""
    module = _HotkeyModule(result=parse_hotkey("esc"))
    changer, _, indicator, _ = _changer(tmp_path, module)

    _run(changer, module, monkeypatch)

    assert ("set_keys", str(parse_hotkey("esc")), "") in indicator.calls


def test_the_prompt_uses_the_plain_indicator_not_the_sounding_one(tmp_path):
    # The prompt repeats about once a second while you decide. The sound
    # wrapper maps every flash to the error tone, so repeating it through that
    # would beep at you until you pressed something.
    _, _, indicator, _ = _changer(tmp_path, _HotkeyModule())
    assert not isinstance(indicator, va_main.SoundingIndicator)


# --------------------------------------------------------------------------
# Fillers go even where the rest of the cleanup does not
# --------------------------------------------------------------------------


def test_fillers_are_removed_in_a_skipped_app():
    # The reason skip_cleanup_in exists is that recapitalising breaks a
    # command. "um" breaks one too -- it is not a valid token in any shell.
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: "Terminal"):
        assert clean("um git status") == "git status"


def test_a_skipped_app_is_still_not_recapitalised():
    # The half that must NOT happen: `Git status` does not run.
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: "Terminal"):
        assert clean("um cd Documents") == "cd Documents"


def test_a_skipped_app_keeps_its_self_corrections():
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: "Terminal"):
        assert clean("deploy Tuesday, no, Wednesday") == (
            "deploy Tuesday, no, Wednesday"
        )


def test_raw_mode_still_means_raw_in_a_skipped_app():
    # clean_speech=false is a request for the untouched transcript. It has to
    # win here too, or "raw" would quietly mean "raw except fillers".
    cfg = {"clean_speech": False, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: "Terminal"):
        assert clean("um git status") == "um git status"


# --------------------------------------------------------------------------
# The paster protocol, checked at wiring time
# --------------------------------------------------------------------------
#
# This exists because of a bug that reached a real dictation: _run_app_mac
# passed `paste_mac.paste_text` (the function) where the paster protocol wants
# an object with a `.paste_text` method. The controller caught the resulting
# AttributeError, flashed "could not paste", and printed a timing report that
# looked entirely healthy apart from a suspiciously fast insertion stage.
#
# The launchers are not covered end to end -- every test that drives them stops
# at the model load -- so the wiring itself has to be self-checking.


def test_recording_paster_rejects_a_bare_function():
    """The exact mistake: the function, not the module that contains it."""
    from vocal_advantage import paste_mac

    with pytest.raises(TypeError, match="paste_text"):
        va_main.RecordingPaster(paste_mac.paste_text, _Paster())


def test_recording_paster_accepts_a_module_with_a_module_level_paste_text():
    from vocal_advantage import paste_mac, paste_win

    va_main.RecordingPaster(paste_mac, _Paster())
    va_main.RecordingPaster(paste_win, _Paster())


def test_recording_paster_accepts_an_ordinary_object():
    va_main.RecordingPaster(_Paster(), _Paster())


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_each_launcher_wires_a_usable_paster(platform_name, tmp_path, monkeypatch):
    """Catch a miswired paster on either platform without a real dictation.

    The launcher is stopped at the model load, which is *after* the paster is
    built -- so reaching the stop proves RecordingPaster's check passed on
    whatever that platform hands it.
    """
    monkeypatch.setattr(va_main.sys, "platform", platform_name)

    class StopAfterWiring(Exception):
        pass

    built: list = []
    real = va_main.RecordingPaster

    def spy(inner, history):
        built.append(inner)
        return real(inner, history)

    monkeypatch.setattr(va_main, "RecordingPaster", spy)

    def stop():
        raise StopAfterWiring

    # The paster is built in the controller call, which is after this point on
    # macOS and before it on Windows -- so stop at the paste instead.
    monkeypatch.setattr(va_main, "_build_controller", lambda *a, **k: stop())

    launcher = va_main._run_app_mac if platform_name == "darwin" else (
        va_main._run_app_windows
    )
    with pytest.raises(StopAfterWiring):
        launcher(tmp_path / "config.json")

    assert built, "no paster was constructed"
    for inner in built:
        assert callable(getattr(inner, "paste_text", None)), inner


# --------------------------------------------------------------------------
# The launchers hand the bar the configured hotkey
# --------------------------------------------------------------------------
#
# Replaces test_flowbar_legend.py::test_each_launcher_hands_the_bar_a_legend,
# which spied on this same Indicator() call for the `legend=` kwarg that
# argument replaced. flowbar.Frame no longer carries a legend string, but the
# launcher still has to hand *something* correct to Indicator's `hotkey=`, and
# nothing else checks that main.py actually does -- the two tests that merely
# stop the launcher past this line (above, and
# test_run_app_on_mac_never_touches_tkinter) assert nothing about what was
# passed.
#
# Both platforms now pass the hotkey (Task 8): the Windows launcher used to
# omit it because `flowbar_win.render_frame` drew no text, and that stopped
# being true once the panel work gave it a font.


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_each_launcher_hands_the_bar_the_configured_hotkey(
    platform_name, tmp_path, monkeypatch
):
    """Neither launcher is covered end to end -- every test that drives one
    stops at the model load -- so the wiring has to be checked where it
    happens.

    Same shape as ``test_each_launcher_wires_a_usable_paster`` above: spy on
    the constructor call itself, because a hotkey that silently never reaches
    the Indicator looks exactly like a bar that has nothing to say.
    """
    # Imported before sys.platform is faked, and deliberately. `flowbar_win`
    # builds `ctypes.WinDLL("user32")` at module scope behind a platform guard,
    # so importing it *while* pretending to be Windows runs that on a Mac and
    # dies. Getting it into sys.modules first makes the guard a no-op.
    from vocal_advantage import flowbar as fb
    from vocal_advantage import flowbar_win, hotkey_win, paste_win  # noqa: F401

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"hotkey": "ctrl+alt+d"}, indent=2) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(va_main.sys, "platform", platform_name)

    class StopAfterWiring(Exception):
        pass

    built: list = []

    def spy(*args, **kwargs):
        built.append(kwargs.get("hotkey", ""))
        raise StopAfterWiring

    monkeypatch.setattr(fb, "Indicator", spy)

    launcher = (
        va_main._run_app_mac if platform_name == "darwin"
        else va_main._run_app_windows
    )
    with pytest.raises(StopAfterWiring):
        launcher(config_path)

    assert built, "no Indicator was constructed"
    # Not a hardcoded display string: whatever parse_hotkey/str() does to
    # "ctrl+alt+d" today is what the launcher must hand over too, so this
    # stays correct if the display format ever changes.
    assert built[0] == str(parse_hotkey("ctrl+alt+d")), built[0]
