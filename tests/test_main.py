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
from pathlib import Path

import pytest

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


def test_run_app_sets_dpi_awareness_before_it_builds_the_first_window(
    tmp_path, monkeypatch, capsys
):
    """Windows fixes a process's DPI awareness when its first window appears.

    Called after tk.Tk(), set_dpi_awareness() is a silent no-op returning
    E_ACCESSDENIED and the pill renders blurry on a scaled display. indicator_win
    deliberately never calls it, so run_app is the single production call site
    and the ordering is only checkable here.
    """
    import tkinter

    from vocal_advantage import indicator_win

    order: list[str] = []
    monkeypatch.setattr(
        indicator_win, "set_dpi_awareness", lambda: order.append("set_dpi_awareness")
    )

    class StopBeforeTheAppStarts(Exception):
        pass

    def fake_tk(*args, **kwargs):
        order.append("tk.Tk")
        raise StopBeforeTheAppStarts

    monkeypatch.setattr(tkinter, "Tk", fake_tk)

    with pytest.raises(StopBeforeTheAppStarts):
        va_main.run_app(tmp_path / "config.json")

    assert order == ["set_dpi_awareness", "tk.Tk"]
