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
from vocal_advantage import main as va_main
from vocal_advantage.hotkey_spec import HotkeyError, parse_hotkey

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
# Live dictation
# --------------------------------------------------------------------------


class _FakeRec:
    def __init__(self):
        self.buffer = np.zeros(0, dtype=np.float32)

    def snapshot(self):
        return self.buffer

    def say(self, seconds):
        self.buffer = np.zeros(int(16000 * seconds), dtype=np.float32)


class _LiveClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _ScriptedTranscriber:
    """Returns canned answers; `pass_time` charges the fake clock per pass,
    which is how a slow or fast device is simulated."""

    def __init__(self, answers, clock=None, pass_time=0.0):
        self.answers = list(answers)
        self.calls = 0
        self.clock = clock
        self.pass_time = pass_time

    def transcribe(self, audio):
        self.calls += 1
        if self.clock is not None:
            self.clock.advance(self.pass_time)
        return self.answers.pop(0) if self.answers else ""


def _live(answers, pass_time=0.0, **kwargs):
    rec = _FakeRec()
    clk = _LiveClock()
    tr = _ScriptedTranscriber(answers, clock=clk, pass_time=pass_time)
    typed, final = [], []
    live = va_main.LiveDictation(
        recorder=rec,
        transcriber=tr,
        type_partial=lambda t: typed.append(t) or True,
        type_final=lambda t: final.append(t) or True,
        clock=clk,
        **kwargs,
    )
    return live, rec, tr, typed, final, clk


def test_a_partial_pass_is_skipped_until_there_is_enough_audio():
    """Transcribing a fifth of a second wastes time and tells you nothing."""
    live, rec, tr, typed, _, clk = _live(["hello"])
    rec.say(0.2)
    live.on_partial()
    assert tr.calls == 0
    assert typed == []


def test_words_are_typed_once_two_passes_agree():
    live, rec, tr, typed, _, clk = _live(["hello there", "hello there"])
    rec.say(2.0)
    live.on_partial()
    assert typed == [], "one pass proves nothing"
    clk.advance(1.0)
    live.on_partial()
    assert typed == ["hello there"]


def test_the_final_transcript_types_only_what_is_still_owed():
    live, rec, tr, typed, final, clk = _live(["one two", "one two"])
    rec.say(2.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()
    assert typed == ["one two"]

    assert live.paste_text("one two three") is True
    assert final == [" three"], "the already-typed words must not repeat"


def test_a_dictation_with_no_partials_types_the_whole_thing():
    live, rec, tr, typed, final, clk = _live([])
    assert live.paste_text("hello world") is True
    assert final == ["hello world"]


def test_it_reports_success_when_the_partials_already_typed_everything():
    """Nothing left to type is a success, not a failed paste -- the controller
    would otherwise flash 'could not paste' at a dictation that worked."""
    live, rec, tr, typed, final, clk = _live(["all done", "all done"])
    rec.say(2.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()

    assert live.paste_text("all done") is True
    assert final == []


def test_each_dictation_starts_clean():
    live, rec, tr, typed, final, clk = _live(["one", "one", "two", "two"])
    rec.say(2.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()
    live.paste_text("one")

    rec.buffer = np.zeros(0, dtype=np.float32)   # the recorder starts over
    rec.say(2.0)
    clk.advance(1.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()
    assert typed == ["one", "two"], "the second dictation must not repeat the first"


def test_live_dictation_reports_each_group_of_words_it_types(capsys):
    """Without this the live half is invisible in the log, and 'it feels slow'
    cannot be told apart from 'it never fired'."""
    live, rec, tr, typed, _, clk = _live(["hello there", "hello there"])
    rec.say(2.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()

    out = capsys.readouterr().out
    assert "hello there" in out


# --------------------------------------------------------------------------
# Self-pacing: the live cadence adapts to whatever hardware this is
# --------------------------------------------------------------------------


def test_a_pass_is_not_rerun_before_the_machine_has_earned_it():
    """The loop ticks fast on purpose; LiveDictation itself decides when a new
    pass is affordable. Two ticks with no time passed = one transcription."""
    live, rec, tr, typed, _, clk = _live(["hello", "hello"], pass_time=0.2)
    rec.say(2.0)
    live.on_partial()
    assert tr.calls == 1
    live.on_partial()          # the clock has only moved by the pass itself
    assert tr.calls == 1, "a second pass this soon is pure waste"


def test_a_slow_device_backs_off_instead_of_choking():
    """The gap between passes tracks the cost of the last one, so a machine
    where a pass takes 2s spends at most half its time transcribing instead of
    queueing up work it can never finish."""
    live, rec, tr, typed, _, clk = _live(["a", "a", "a"], pass_time=2.0)
    rec.say(4.0)
    live.on_partial()
    assert tr.calls == 1
    clk.advance(1.0)           # 1s later: a 2s pass has not been earned yet
    live.on_partial()
    assert tr.calls == 1
    clk.advance(1.5)           # 2.5s after the pass ended: now it has
    live.on_partial()
    assert tr.calls == 2


def test_a_fast_device_is_only_held_to_a_tiny_floor():
    live, rec, tr, typed, _, clk = _live(["a", "a", "a"], pass_time=0.01)
    rec.say(2.0)
    live.on_partial()
    clk.advance(va_main.LiveDictation.MIN_GAP_S + 0.001)
    live.on_partial()
    assert tr.calls == 2, "fast hardware should be allowed to run fast"


def test_very_long_dictations_stop_doing_live_passes():
    """Each pass re-transcribes from the start, so cost grows with length --
    and a pass in flight delays the key-release sitting behind it in the
    queue. Past the cap the preview stops; the final transcript on release
    still delivers everything."""
    live, rec, tr, typed, final, clk = _live(["long text"])
    rec.say(va_main.LiveDictation.MAX_PARTIAL_S + 5.0)
    live.on_partial()
    assert tr.calls == 0
    assert live.paste_text("the whole long thing") is True
    assert final == ["the whole long thing"]


def test_pacing_resets_between_dictations():
    live, rec, tr, typed, final, clk = _live(["a", "a"], pass_time=5.0)
    rec.say(2.0)
    live.on_partial()          # expensive pass; next one owed far in the future
    live.paste_text("a")

    rec.buffer = np.zeros(0, dtype=np.float32)
    rec.say(2.0)
    live.on_partial()          # a fresh dictation must not inherit that debt
    assert tr.calls == 2



# -- filler cleanup ---------------------------------------------------------
#
# Cleaning must happen before StreamingTranscript sees the text, so a filler is
# never typed and then removed -- removing it would mean backspacing over words
# the user is watching. See docs/plans/2026-08-20-speech-cleanup.md.


def test_a_filler_word_never_reaches_the_document():
    live, rec, tr, typed, _, clk = _live(["Um, so I think", "Um, so I think"])
    rec.say(2.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()
    assert typed == ["So I think"], typed
    assert "Um" not in " ".join(typed)


def test_the_final_transcript_is_cleaned_too():
    live, rec, tr, typed, final, clk = _live(["Um, so I think", "Um, so I think"])
    rec.say(2.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()
    assert live.paste_text("Um, so I think we should uh ship it") is True
    assert final == [" we should ship it"], final


def test_cleaning_can_be_switched_off():
    """config.json clean_speech=false must give the raw transcript back."""
    live, rec, tr, typed, _, clk = _live(
        ["Um, so I think", "Um, so I think"], clean=lambda text: text
    )
    rec.say(2.0)
    live.on_partial()
    clk.advance(1.0)
    live.on_partial()
    assert typed == ["Um, so I think"], typed


class _RecordingPaster:
    def __init__(self, ok=True):
        self.pasted = []
        self.ok = ok

    def paste_text(self, text):
        self.pasted.append(text)
        return self.ok


def test_the_windows_path_cleans_before_pasting():
    """Windows has no live preview, so cleaning has to happen at the paster."""
    inner = _RecordingPaster()
    paster = va_main.CleaningPaster(inner)
    assert paster.paste_text("Um, so I I think we should uh ship it") is True
    assert inner.pasted == ["So I think we should ship it"]


def test_an_utterance_that_was_only_filler_pastes_nothing():
    """Nothing to type is a success, not a failure to report to the user."""
    inner = _RecordingPaster()
    paster = va_main.CleaningPaster(inner)
    assert paster.paste_text("um uh er") is True
    assert inner.pasted == []


def test_a_paste_failure_is_still_reported_through_the_wrapper():
    inner = _RecordingPaster(ok=False)
    assert va_main.CleaningPaster(inner).paste_text("hello there") is False


def test_the_wrapper_can_be_switched_off():
    inner = _RecordingPaster()
    paster = va_main.CleaningPaster(inner, clean=lambda text: text)
    paster.paste_text("Um, hello")
    assert inner.pasted == ["Um, hello"]


# -- which cleaner each config asks for -------------------------------------


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


def test_live_typing_is_paused_while_ai_cleanup_is_on():
    """The AI can only clean a finished sentence. Typing words first would mean
    backspacing over them, so nothing is typed until the key is released."""
    assert va_main.live_typing_enabled({}) is True
    assert va_main.live_typing_enabled({"ai_cleanup": False}) is True
    assert va_main.live_typing_enabled({"ai_cleanup": True}) is False


# --------------------------------------------------------------------------
# The UI is decoration: losing it must never cost dictation
# --------------------------------------------------------------------------


class _Recorder:
    def __init__(self, recording=True, explode=False):
        self.is_recording = recording
        self.stopped = False
        self._explode = explode

    def stop(self):
        if self._explode:
            raise RuntimeError("the mic was unplugged")
        self.stopped = True


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


def test_cleanup_is_skipped_in_a_listed_app():
    # Filler removal is right for prose and wrong for a shell: it will happily
    # turn a command into something that does not run.
    cfg = {"clean_speech": True, "skip_cleanup_in": ["terminal"]}
    clean = va_main._cleaner(cfg)
    with mock.patch.object(va_main, "frontmost_app", lambda: "Terminal"):
        assert clean("Um, git status") == "Um, git status"


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
        assert clean("Um, hello") != "Um, hello"
        app[0] = "Terminal"
        assert clean("Um, hello") == "Um, hello"


def test_no_skip_list_leaves_the_cleaner_untouched():
    # Preserves the original contract: with nothing to wrap, _cleaner returns
    # the cleanup function itself rather than a closure around it.
    assert va_main._cleaner({"skip_cleanup_in": []}) is va_cleanup.clean_speech
