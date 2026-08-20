"""Entry point for Vocal Advantage: argument parsing, wiring, shutdown.

This file is glue and nothing else. All behaviour lives in the modules it bolts
together, which is why there is no state machine here — that is controller.py.

Import rule, and it is load-bearing: nothing in this module may import
`vocal_advantage.transcriber` (or faster_whisper, or ctranslate2) at module
level. `cuda_dlls.prepare()` has to register the NVIDIA DLL directories with
os.add_dll_directory() before ctranslate2 loads, and Python 3.8+ ignores PATH
for DLL resolution, so a top-level import here would produce an unfixable
"cublas64_12.dll not found" at startup. See import_transcriber_class().
"""

from __future__ import annotations

import argparse
import ctypes
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from vocal_advantage import cuda_dlls
from vocal_advantage.config import CONFIG_PATH, load_config, save_config
from vocal_advantage.controller import DictationController
from vocal_advantage.hotkey_spec import HotkeyError, HotkeySpec, parse_hotkey
from vocal_advantage.streaming import StreamingTranscript

VERSION = "0.1.0"

# Two instances would both hook the keyboard and both paste — every dictation
# would land twice. "Local\" scopes the name to this login session, which is
# what we want for a per-user desktop app.
MUTEX_NAME = r"Local\VocalAdvantageSingleInstance"
ERROR_ALREADY_EXISTS = 183

TICK_INTERVAL_S = 1.0  # how often the controller's 300s watchdog gets a chance to fire
# Live dictation transcribes the audio so far on every tick, so the tick has to
# be quick enough to feel live and slow enough not to spend the whole CPU
# re-transcribing. At 0.5s a pass on `tiny` costs ~0.2s -- comfortable.
LIVE_TICK_S = 0.5
UI_PUMP_MS = 50        # how often tkinter drains the indicator's queue
HEARTBEAT_MS = 200     # keeps Ctrl+C responsive and notices a dead controller thread

# Kept for the life of the process: Windows frees a named mutex when the last
# handle to it closes, so dropping this would let a second instance start.
_INSTANCE_LOCK: int | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m vocal_advantage",
        description=(
            "Hold a key, speak, let go - your words are pasted into whatever app "
            "has focus. Everything runs on this PC."
        ),
    )
    parser.add_argument(
        "--set-hotkey",
        action="store_true",
        help="Press the key or combo you want to use, and save it to config.json.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="Vocal Advantage %s" % VERSION,
    )
    return parser


def platform_modules():
    """The (hotkey, paste) pair for this machine.

    The only place in the project that chooses a platform. Everything else --
    the recorder, the transcriber, the controller, the config -- is shared, and
    both pairs satisfy the same two contracts: HotkeyListener + capture_hotkey,
    and paste_text.
    """
    if sys.platform == "darwin":
        from vocal_advantage import hotkey_mac, paste_mac

        return hotkey_mac, paste_mac
    from vocal_advantage import hotkey_win, paste_win

    return hotkey_win, paste_win


class NarratingTranscriber:
    """Wraps a transcriber to report what it heard and how long it took.

    A reporter, not a participant: the text passes through byte for byte and
    exceptions propagate unchanged, because the controller already knows how to
    handle a transcriber that fails.

    Worth having permanently. "It felt slow" and "it got the words wrong" are
    the two things users report, and neither is diagnosable without seeing the
    transcript and the timing side by side.
    """

    SAMPLE_RATE = 16000

    def __init__(self, inner) -> None:
        self._inner = inner

    def warm_up(self) -> None:
        warm = getattr(self._inner, "warm_up", None)
        if warm is not None:
            warm()

    def transcribe(self, audio):
        started = time.monotonic()
        text = self._inner.transcribe(audio)
        took = time.monotonic() - started
        seconds = len(audio) / self.SAMPLE_RATE
        print(f"  [heard {seconds:.1f}s of audio in {took:.2f}s] {text!r}", flush=True)
        return text


class LiveDictation:
    """Types words as they settle, while the user is still speaking.

    Sits in two places at once, which is the whole trick:

    * ``on_partial`` runs on each controller tick while RECORDING. It
      transcribes the audio so far and types whichever words two consecutive
      passes agree on.
    * ``paste_text`` satisfies the paster protocol, so when the key is released
      and the controller delivers the final transcript, this types only the part
      the partial passes have not already typed.

    Without the second half the whole dictation would be typed twice.
    """

    SAMPLE_RATE = 16000
    #: Below this there is not enough audio for a pass to tell you anything,
    #: and running the model on it is pure waste.
    MIN_AUDIO_S = 0.8

    def __init__(self, *, recorder, transcriber, type_partial, type_final) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._type_partial = type_partial
        self._type_final = type_final
        self._session = StreamingTranscript()
        self._last_samples = 0

    def on_partial(self) -> None:
        audio = self._recorder.snapshot()
        # The buffer shrinking means the recorder started a fresh recording --
        # a previous one was cancelled without ever reaching paste_text.
        if audio.size < self._last_samples:
            self._session.reset()
        self._last_samples = audio.size

        if audio.size < int(self.MIN_AUDIO_S * self.SAMPLE_RATE):
            return
        fresh = self._session.commit(self._transcriber.transcribe(audio))
        if fresh:
            self._type_partial(fresh)

    def paste_text(self, text: str) -> bool:
        """The controller's final delivery: type whatever is still owed."""
        remaining = self._session.finish(text)
        self._session.reset()
        self._last_samples = 0
        if not remaining:
            # Everything was already typed live. That is a success; reporting
            # failure would flash "could not paste" at a dictation that worked.
            return True
        return bool(self._type_final(remaining))


class ConsoleIndicator:
    """Stands in for the pill on platforms that have no overlay yet.

    Answers to exactly the four methods the controller calls, so controller.py
    needs no knowledge that the pill is missing. It prints instead of drawing,
    which is not as good as an overlay but is considerably better than no
    feedback at all while you are learning what the app is doing.

    Nothing here may raise: an exception from an indicator would surface as a
    failed dictation.
    """

    def _say(self, message: str) -> None:
        try:
            print(message, flush=True)
        except Exception:  # noqa: BLE001
            pass

    def show_recording(self) -> None:
        self._say("  [listening]")

    def show_processing(self) -> None:
        self._say("  [transcribing]")

    def hide(self) -> None:
        pass

    def flash(self, message: str) -> None:
        self._say(f"  [{message}]")


def release_single_instance_lock(handle) -> None:
    """Give the lock back. Safe to call with whatever acquire returned."""
    if handle is None:
        return
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)
        return
    import fcntl

    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def acquire_single_instance_lock(name: str = MUTEX_NAME):
    """Take the app-wide lock, or return None if another copy already holds it.

    Two instances would both hook the keyboard and both paste, so every
    dictation would land twice.

    Windows uses a named mutex; POSIX uses an exclusive flock on a file in the
    temp directory, which the OS drops automatically if the process dies -- so a
    crash never leaves the app unable to start. The return value is opaque and
    differs by platform; hand it to release_single_instance_lock.
    """
    if sys.platform != "win32":
        import fcntl
        import tempfile

        safe = "".join(c for c in name if c.isalnum() or c in "-_.") or "lock"
        handle = open(Path(tempfile.gettempdir()) / f"{safe}.lock", "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.CreateMutexW(None, 0, name)
    last_error = ctypes.get_last_error()

    if handle and last_error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)  # our duplicate handle; the owner keeps theirs
        return None
    if not handle:
        raise ctypes.WinError(last_error)
    return handle


def import_transcriber_class(prepare: Callable[[], None] = cuda_dlls.prepare):
    """Wire up the NVIDIA DLL directories, then - and only then - import the model.

    `prepare` is a parameter purely so the ordering can be tested; production
    always uses cuda_dlls.prepare. The import below is deliberately inside this
    function: at module level it would run before prepare() ever could.
    """
    prepare()
    from vocal_advantage.transcriber import Transcriber

    return Transcriber


def _safe_call(what: str, function: Callable[..., object], *args: object) -> None:
    """Run a callback; log and continue if it raises.

    One bad utterance (a mic unplugged mid-sentence, a paste target that went
    away) must never take the hotkey down with it.
    """
    try:
        function(*args)
    except Exception:  # noqa: BLE001 - deliberately broad; this is the crash barrier
        print("Error in %s:" % what, file=sys.stderr)
        traceback.print_exc()


def controller_loop(
    controller,
    events: "queue.Queue",
    stop_event: threading.Event,
    clock: Callable[[], float] = time.monotonic,
    tick_interval_s: float = TICK_INTERVAL_S,
) -> None:
    """Drain the hook's event queue on ONE thread, ticking the watchdog ~1/s.

    The keyboard hook fires on its own thread and only ever enqueues. Everything
    that reads or writes controller state - including transcription, which takes
    1-2s - happens here, so the key-up-beats-key-down race cannot happen.

    Ends on either `stop_event` or a None sentinel in the queue; the sentinel is
    what unblocks a loop that is sitting in get().
    """
    next_tick = clock() + tick_interval_s
    while not stop_event.is_set():
        timeout = max(0.01, next_tick - clock())
        try:
            item = events.get(timeout=timeout)
        except queue.Empty:
            _safe_call("watchdog tick", controller.tick)
            next_tick = clock() + tick_interval_s
            continue

        if item is None:
            break

        key_name, is_down = item
        _safe_call("key event", controller.on_key_event, key_name, is_down)

        # A burst of typing must not turn into a burst of watchdog checks.
        if clock() >= next_tick:
            _safe_call("watchdog tick", controller.tick)
            next_tick = clock() + tick_interval_s


def run_set_hotkey(
    capture: Callable[[], HotkeySpec],
    config_path: Path = CONFIG_PATH,
    attempts: int = 3,
) -> int:
    """Capture a hotkey, validate it, and save it. Returns a process exit code.

    `capture` is injected so this is testable without a keyboard; main() passes
    hotkey_win.capture_hotkey. config.json is only touched on success - a refused
    key leaves the file exactly as it was.
    """
    for attempt in range(1, attempts + 1):
        print("Hold the key or combo you want, then release it.")
        try:
            spec = capture()
        except HotkeyError as error:
            print("That will not work as a hotkey: %s" % error, file=sys.stderr)
            if attempt < attempts:
                print("Try a different key.\n", file=sys.stderr)
            continue
        except TimeoutError as error:
            # capture_hotkey's other documented failure (Task 6's contract).
            # Not retried: a timeout means nobody is at the keyboard, so asking
            # twice more just makes them wait another 30 seconds for the same
            # answer. Uncaught it would end --set-hotkey in a stack trace.
            print("%s" % error, file=sys.stderr)
            break

        cfg = load_config(config_path)
        # Store the raw key names, not str(spec)'s display form: sorted key names
        # are deterministic and are exactly what parse_hotkey expects to read back.
        cfg["hotkey"] = "+".join(sorted(spec.keys))
        save_config(cfg, config_path)

        print("Hotkey set to %s - saved to %s" % (spec, config_path))
        print("Restart Vocal Advantage for the new hotkey to take effect.")
        return 0

    print("Hotkey unchanged.", file=sys.stderr)
    return 1


def _run_app_windows(config_path: Path = CONFIG_PATH) -> int:
    """Build the object graph, start the threads, and hand the main thread to tk."""
    # These imports are function-local on purpose. tkinter, sounddevice and the
    # keyboard hook are all real hardware/OS bindings; keeping them out of module
    # scope means `import vocal_advantage.main` stays cheap and testable, and it
    # makes it structurally impossible to accidentally import the model stack
    # before import_transcriber_class() has run prepare().
    import tkinter as tk

    from vocal_advantage import paste_win
    from vocal_advantage.hotkey_win import HotkeyListener
    from vocal_advantage.indicator_win import Indicator, set_dpi_awareness
    from vocal_advantage.recorder import Recorder

    cfg = load_config(config_path)
    # load_config already warned and substituted the default if this was garbage,
    # so this parse cannot raise.
    spec = parse_hotkey(cfg["hotkey"])
    print("Hotkey: %s" % spec)

    # MUST be before tk.Tk(). Windows fixes a process's DPI awareness when that
    # process creates its first window; called afterwards this is a silent no-op
    # answering E_ACCESSDENIED, the pill renders blurry on a scaled display, and
    # the "bottom centre of the screen" pixel maths is wrong above 100% scaling.
    # indicator_win deliberately never calls it for us, so this is the single
    # production call site (Task 8's contract) - keep it directly above tk.Tk().
    set_dpi_awareness()

    # tkinter owns the main thread. The root itself is never shown - the only
    # visible window is the Toplevel the Indicator makes for itself.
    root = tk.Tk()
    root.withdraw()
    indicator = Indicator(root)

    print(
        "Loading the %s model on device=%s ..." % (cfg["model"], cfg["device"])
    )
    print("(First run only: this downloads about 1.6 GB to your user cache.)")
    transcriber_cls = import_transcriber_class()
    transcriber = transcriber_cls(
        model_name=cfg["model"],
        device=cfg["device"],
        language=cfg["language"],
        min_duration_s=float(cfg["min_duration_s"]),
    )
    # The first transcribe() pays 1-3s of CUDA init. Paying it now, at startup,
    # is what makes the first real dictation as fast as the tenth.
    transcriber.warm_up()
    print("Model ready.")

    recorder = Recorder()
    controller = DictationController(
        hotkey=spec,
        recorder=recorder,
        transcriber=transcriber,
        # The paste_win module itself satisfies the paster protocol: it exposes a
        # module-level paste_text(str) -> bool, which is the whole interface.
        paster=paste_win,
        indicator=indicator,
        min_duration_s=float(cfg["min_duration_s"]),
        max_duration_s=float(cfg["max_duration_s"]),
    )

    events: "queue.Queue" = queue.Queue()
    stop_event = threading.Event()
    worker = threading.Thread(
        target=controller_loop,
        args=(controller, events, stop_event),
        name="vocal-advantage-controller",
        daemon=True,
    )
    worker.start()

    def on_key(key_name: str, is_down: bool) -> None:
        # Runs on the keyboard hook's thread. It must do nothing but enqueue -
        # any real work here would block Windows' low-level keyboard hook.
        events.put((key_name, is_down))

    listener = HotkeyListener(spec, on_key)
    listener.start()

    shutting_down = threading.Event()

    def shutdown() -> None:
        if shutting_down.is_set():
            return
        shutting_down.set()
        print("Shutting down ...")

        _safe_call("hotkey listener stop", listener.stop)
        try:
            import keyboard

            keyboard.unhook_all()  # belt and braces: nothing may still see keys
        except Exception:  # noqa: BLE001
            pass

        stop_event.set()
        events.put(None)  # unblock the loop if it is sitting in get()
        worker.join(timeout=5.0)

        try:
            if recorder.is_recording:
                recorder.stop()  # closes the stream so the Windows mic light goes out
        except Exception:  # noqa: BLE001
            traceback.print_exc()

        _safe_call("indicator hide", indicator.hide)
        root.quit()

    root.protocol("WM_DELETE_WINDOW", shutdown)

    def heartbeat() -> None:
        # A repeating tk callback is what lets Ctrl+C in the console interrupt
        # mainloop at all: Python only runs signal handlers between bytecodes,
        # and mainloop otherwise blocks inside Tcl.
        if not worker.is_alive() and not shutting_down.is_set():
            print("Controller thread stopped unexpectedly.", file=sys.stderr)
            shutdown()
            return
        root.after(HEARTBEAT_MS, heartbeat)

    # Indicator.pump re-arms itself with root.after; this just starts it.
    root.after(UI_PUMP_MS, indicator.pump)
    root.after(HEARTBEAT_MS, heartbeat)

    print("Ready. Hold %s and speak. Close this console window to quit." % spec)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("")
    finally:
        shutdown()
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    global _INSTANCE_LOCK

    # --version and --help exit inside parse_args, before the mutex, so they
    # still work while the app is running.
    args = build_parser().parse_args(argv)

    lock = acquire_single_instance_lock()
    if lock is None:
        print(
            "Vocal Advantage is already running. Close the other window first.",
            file=sys.stderr,
        )
        return 1
    _INSTANCE_LOCK = lock

    if args.set_hotkey:
        # --set-hotkey installs its own keyboard hook, so it must not run
        # alongside the app; holding the same mutex is what prevents that.
        hotkey_module, _ = platform_modules()

        return run_set_hotkey(hotkey_module.capture_hotkey, CONFIG_PATH)

    return run_app(CONFIG_PATH)


def _run_app_mac(config_path: Path = CONFIG_PATH) -> int:
    """The macOS launcher. No tkinter, no pill, no CUDA.

    Deliberately does not build a Tk root: with no overlay there is nothing to
    draw, and creating one would put a Python rocket in the Dock and steal focus
    at launch -- the exact thing the Windows pill goes to such lengths to avoid.

    The event tap needs a CFRunLoop, but HotkeyListener owns one on its own
    thread, so the main thread here just waits.
    """
    from vocal_advantage import paste_mac
    from vocal_advantage.hotkey_mac import HotkeyListener, HotkeyPermissionError
    from vocal_advantage.recorder import Recorder

    cfg = load_config(config_path)
    spec = parse_hotkey(cfg["hotkey"])
    print("Hotkey: %s" % spec)

    indicator = ConsoleIndicator()

    print("Loading the %s model on device=%s ..." % (cfg["model"], cfg["device"]))
    print("(First run only: this downloads about 1.6 GB to your user cache.)")
    transcriber_cls = import_transcriber_class()
    transcriber = transcriber_cls(
        model_name=cfg["model"],
        device=cfg["device"],
        language=cfg["language"],
        min_duration_s=float(cfg["min_duration_s"]),
    )
    transcriber.warm_up()
    print("Model ready.")

    recorder = Recorder()
    # The raw transcriber for partial passes: narrating every one of them would
    # bury the console. The final transcript still gets reported.
    live = LiveDictation(
        recorder=recorder,
        transcriber=transcriber,
        type_partial=paste_mac.type_partial,
        type_final=paste_mac.paste_text,
    )
    controller = DictationController(
        hotkey=spec,
        recorder=recorder,
        transcriber=NarratingTranscriber(transcriber),
        paster=live,
        indicator=indicator,
        min_duration_s=float(cfg["min_duration_s"]),
        max_duration_s=float(cfg["max_duration_s"]),
        on_partial=live.on_partial,
    )

    events: "queue.Queue" = queue.Queue()
    stop_event = threading.Event()
    worker = threading.Thread(
        target=controller_loop,
        args=(controller, events, stop_event),
        kwargs={"tick_interval_s": LIVE_TICK_S},
        name="vocal-advantage-controller",
        daemon=True,
    )
    worker.start()

    def on_key(key_name: str, is_down: bool) -> None:
        # Runs on the tap's thread. Enqueue and nothing else: real work here
        # would stall the event tap and macOS would eventually disable it.
        events.put((key_name, is_down))

    listener = HotkeyListener(spec, on_key)
    try:
        listener.start()
    except HotkeyPermissionError as error:
        print("", file=sys.stderr)
        print(error, file=sys.stderr)
        stop_event.set()
        events.put(None)
        worker.join(timeout=2.0)
        return 1

    print("Ready. Hold %s and speak. Press Ctrl+C to quit." % spec)
    try:
        while worker.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("")
    finally:
        print("Shutting down ...")
        _safe_call("hotkey listener stop", listener.stop)
        stop_event.set()
        events.put(None)
        worker.join(timeout=5.0)
        try:
            if recorder.is_recording:
                recorder.stop()  # close the stream so the mic indicator goes out
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    return 0


def run_app(config_path: Path = CONFIG_PATH) -> int:
    """Start the app, on whichever platform this is."""
    if sys.platform == "darwin":
        return _run_app_mac(config_path)
    return _run_app_windows(config_path)
