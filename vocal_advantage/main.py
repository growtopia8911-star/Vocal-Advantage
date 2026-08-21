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
# Re-exported so `main.say(...)` reads as intended; the implementation lives in
# console.py so config.py can use it without an import cycle.
from vocal_advantage.console import say, warn
from vocal_advantage.controller import DictationController
from vocal_advantage.dictionary import DICTIONARY_PATH, load_dictionary
from vocal_advantage.frontmost import frontmost_app, matches
from vocal_advantage.history import HISTORY_PATH, History
from vocal_advantage.sounds import Player
from vocal_advantage.hotkey_spec import HotkeyError, HotkeySpec, parse_hotkey
from vocal_advantage.cleanup import (
    ai_clean,
    clean_speech,
    strip_fillers,
    warm_up_model,
)
from vocal_advantage.streaming import StreamingTranscript

VERSION = "0.1.0"

# Two instances would both hook the keyboard and both paste — every dictation
# would land twice. "Local\" scopes the name to this login session, which is
# what we want for a per-user desktop app.
MUTEX_NAME = r"Local\VocalAdvantageSingleInstance"
ERROR_ALREADY_EXISTS = 183

TICK_INTERVAL_S = 1.0  # how often the controller's 300s watchdog gets a chance to fire
# The live loop's wake-up interval, NOT the transcription cadence -- that is
# self-paced inside LiveDictation from the measured cost of each pass, so fast
# hardware transcribes as often as it can afford and slow hardware backs off.
# This only bounds how promptly a newly-earned pass starts, and a wake-up on an
# un-earned tick costs one clock comparison.
LIVE_TICK_S = 0.1
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
        say(f"  [heard {seconds:.1f}s of audio in {took:.2f}s] {text!r}")
        return text


def _cleaner(cfg: dict, dictionary=None):
    """The text-to-text callable both platforms take, chosen by config.

    clean_speech=false wins over ai_cleanup=true: asking for the raw
    transcript and then running it through a model would be a contradiction.

    The personal dictionary's fixes run **last**, after whichever cleanup was
    chosen and even when cleaning is off entirely. Two reasons, and both matter:

    * A correction is not cleanup. Asking for the raw transcript is a request
      about fillers and stutters, not permission to keep spelling your name
      wrong.
    * The AI pass rewrites whole sentences. Fixing first would let it put
      "Kelvin" back, and the correction would look like it had never happened.
    """
    cleaning_on = cfg.get("clean_speech", True)

    if not cleaning_on:
        base = lambda text: text  # noqa: E731 - the identity, deliberately
    elif cfg.get("ai_cleanup", False):
        base = ai_clean
    else:
        base = clean_speech

    # What a skip-listed app gets. Not the identity: fillers are the one part
    # of the pass that is unwanted everywhere -- "um" is not a valid token in
    # any shell either -- so what is skipped is the *rest* of it, the
    # recapitalising and correction-collapsing that break a command.
    #
    # Raw mode still wins, or `clean_speech: false` would quietly come to mean
    # "raw except fillers".
    skipped = strip_fillers if cleaning_on else base

    # Absent (a bare dict in a test) means "no list", not "the defaults" --
    # which is what keeps `_cleaner({}) is clean_speech` true.
    skip = tuple(cfg.get("skip_cleanup_in") or ())

    if not dictionary and not skip:
        return base

    def clean(text: str) -> str:
        # Checked per dictation, not once at startup: the whole point is which
        # window is about to receive this paste, and that changes constantly.
        if skip and matches(frontmost_app(), skip):
            cleaned = skipped(text)
        else:
            cleaned = base(text)
        return dictionary.apply(cleaned) if dictionary else cleaned

    return clean


def _warm_up_ai_cleanup(cfg: dict) -> None:
    """Load the Ollama model now, so the first dictation is not the slow one.

    Says so on the console either way. Ollama being absent is a supported
    state, not an error -- the rules-only cleanup does not need it.
    """
    if not cfg.get("ai_cleanup", False):
        return
    say("Warming up the AI cleanup model ...")
    if warm_up_model():
        say("AI cleanup ready.")
    else:
        warn(
            "WARNING: AI cleanup is on but Ollama did not answer. Filler and "
            "stutter removal still works; the extra pass will be skipped."
        )


def live_typing_enabled(cfg: dict) -> bool:
    """Whether words are typed as they are spoken.

    The AI pass forces this off and always will: it can only clean a finished
    sentence, so words already typed would have to be backspaced over
    afterwards, in a document we do not own.

    But that constraint runs one way only, and the two were welded together in
    both directions by mistake. Turning the AI pass off does not mean live
    typing is *wanted* -- and on macOS it costs real money, because every pass
    re-transcribes the sentence from the start. At `small`'s RTF of 0.37 on
    that CPU a ten-second sentence is ~3.7s per pass, so live typing is what
    made `small` unaffordable there and `base` the only option.

    Separating them is what lets one machine run `small` with no model and no
    live preview, which is the combination that was previously unreachable.
    """
    if cfg.get("ai_cleanup", False):
        return False
    return bool(cfg.get("live_typing", True))


class CleaningPaster:
    """Wraps a paster so filler words never reach the document.

    The Windows path has no live preview -- the whole transcript arrives at
    once on key release -- so there is no LiveDictation to clean inside and the
    cleaning belongs here instead. Same rule either way: clean before the text
    is typed, never after.
    """

    def __init__(self, paster, clean=clean_speech) -> None:
        self._paster = paster
        self._clean = clean

    def paste_text(self, text: str) -> bool:
        cleaned = self._clean(text)
        if not cleaned:
            # The user said nothing but "um". Reporting failure would flash an
            # error at a dictation that worked exactly as asked.
            return True
        return bool(self._paster.paste_text(cleaned))


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
    #: The floor between passes. Not a pace-setter -- just enough to stop a
    #: very fast machine from spinning on identical audio.
    MIN_GAP_S = 0.05
    #: Past this much audio the live preview stops. Every pass re-transcribes
    #: from the start, so cost grows with length -- and a pass in flight delays
    #: the key-release event queued behind it. The final transcript on release
    #: still delivers everything; only the preview goes quiet.
    MAX_PARTIAL_S = 25.0

    def __init__(
        self, *, recorder, transcriber, type_partial, type_final,
        clock=time.monotonic, clean=clean_speech,
    ) -> None:
        self._recorder = recorder
        # Applied before the agreement logic, never after: a filler that
        # reached the document could only be removed by backspacing over words
        # the user is watching. Identity when config says clean_speech=false.
        self._clean = clean
        self._transcriber = transcriber
        self._type_partial = type_partial
        self._type_final = type_final
        self._clock = clock
        self._session = StreamingTranscript()
        self._last_samples = 0
        # Self-pacing. There is no good constant here: a pass costs ~0.05s on a
        # GPU and seconds on a weak CPU, so the cadence is derived from the
        # measured cost of the previous pass -- next pass earned one
        # pass-duration after the last one finished (at most half the machine's
        # time spent transcribing). Fast hardware ticks fast, slow hardware
        # backs off, and no tuning survives being wrong on somebody's machine.
        self._next_pass_at = 0.0

    def on_partial(self) -> None:
        if self._clock() < self._next_pass_at:
            return
        audio = self._recorder.snapshot()
        # The buffer shrinking means the recorder started a fresh recording --
        # a previous one was cancelled without ever reaching paste_text.
        if audio.size < self._last_samples:
            self._session.reset()
        self._last_samples = audio.size

        if audio.size < int(self.MIN_AUDIO_S * self.SAMPLE_RATE):
            return
        if audio.size > int(self.MAX_PARTIAL_S * self.SAMPLE_RATE):
            return
        started = self._clock()
        text = self._clean(self._transcriber.transcribe(audio))
        took = self._clock() - started
        self._next_pass_at = self._clock() + max(took, self.MIN_GAP_S)
        fresh = self._session.commit(text)
        if fresh:
            say(
                f"  [live +{audio.size / self.SAMPLE_RATE:.1f}s in {took:.2f}s]"
                f" {fresh.strip()!r}"
            )
            self._type_partial(fresh)

    def paste_text(self, text: str) -> bool:
        """The controller's final delivery: type whatever is still owed."""
        remaining = self._session.finish(self._clean(text))
        self._session.reset()
        self._last_samples = 0
        self._next_pass_at = 0.0
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
        # console.say already swallows a missing or broken stream, which is the
        # whole reason it exists; nothing extra is needed here.
        say(message)

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
        # format_exc + warn, never traceback.print_exc(): that writes straight
        # to sys.stderr, which is None under pythonw and in a .app bundle, so
        # the crash barrier would itself crash.
        warn("Error in %s:" % what)
        warn(traceback.format_exc())


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
        say("Hold the key or combo you want, then release it.")
        try:
            spec = capture()
        except HotkeyError as error:
            warn("That will not work as a hotkey: %s" % error)
            if attempt < attempts:
                warn("Try a different key.\n")
            continue
        except TimeoutError as error:
            # capture_hotkey's other documented failure (Task 6's contract).
            # Not retried: a timeout means nobody is at the keyboard, so asking
            # twice more just makes them wait another 30 seconds for the same
            # answer. Uncaught it would end --set-hotkey in a stack trace.
            warn("%s" % error)
            break

        cfg = load_config(config_path)
        # Store the raw key names, not str(spec)'s display form: sorted key names
        # are deterministic and are exactly what parse_hotkey expects to read back.
        cfg["hotkey"] = "+".join(sorted(spec.keys))
        save_config(cfg, config_path)

        say("Hotkey set to %s - saved to %s" % (spec, config_path))
        say("Restart Vocal Advantage for the new hotkey to take effect.")
        return 0

    warn("Hotkey unchanged.")
    return 1


class SoundingIndicator:
    """Wraps the indicator so the tones follow the state machine.

    Here rather than inside `controller.py` because the controller has no
    business knowing about audio, and the four methods it already calls happen
    to be exactly the events worth hearing.

    "Done" is deliberately not simply `hide()`. hide() also fires when a
    recording is cancelled and when a tap was too short to transcribe, and a
    success tone for those would be a lie. Tracking whether transcription
    actually started is what tells the three apart.
    """

    def __init__(self, inner, player) -> None:
        self._inner = inner
        self._player = player
        self._processed = False

    def show_recording(self) -> None:
        self._processed = False
        self._player.play("start")
        self._inner.show_recording()

    def show_processing(self) -> None:
        self._processed = True
        self._inner.show_processing()

    def hide(self) -> None:
        if self._processed:
            self._player.play("done")
        self._processed = False
        self._inner.hide()

    def flash(self, message: str) -> None:
        self._processed = False
        self._player.play("error")
        self._inner.flash(message)

    # The tray reads this straight through; the wrapper is invisible to it.
    def status_text(self) -> str:
        return self._inner.status_text()


class RecordingPaster:
    """Wraps a paster so every dictation is written to the history first.

    First, not after: the point of the history is the dictation that did NOT
    arrive where it was meant to. Recording only on success would lose exactly
    the case it exists for.

    What is stored is the transcript as the controller delivered it, before the
    cleanup pass -- which is what you actually said, and the more useful thing
    to find when you are looking for words that went missing.
    """

    def __init__(self, inner, history) -> None:
        self._inner = inner
        self._history = history

    def paste_text(self, text: str) -> bool:
        self._history.record(text, app=frontmost_app())
        return bool(self._inner.paste_text(text))


class HotkeyChanger:
    """The tray's "Change hotkey" item, without restarting the app.

    `--set-hotkey` cannot be used while the app is running: it installs its own
    keyboard hook, and the single-instance lock exists precisely to stop two
    hooks fighting over the keyboard. So the change happens in place -- stop
    our hook, capture, swap, start a new hook.

    **The property that matters is that the hotkey always comes back.** Every
    failure path -- a refused key, a timeout, nobody at the keyboard, an
    exception from the capture -- restarts the listener on the hotkey that was
    already working. Losing the hotkey means losing the app, with no way to get
    it back except quitting from the tray.

    Runs on its own thread: `capture_hotkey` blocks for up to thirty seconds,
    and on macOS that thread must not be the one running the UI.
    """

    #: Re-shown at this interval while waiting. `flash` times itself out after
    #: 1.5s, and a prompt that vanished while you were deciding which key to
    #: press would be worse than no prompt.
    PROMPT_EVERY_S = 1.2

    def __init__(self, *, listener, spec, on_key, controller, indicator,
                 config_path: Path) -> None:
        self._listener = listener
        self._spec = spec
        self._on_key = on_key
        self._controller = controller
        # The RAW indicator, deliberately, not the one wrapped for sounds:
        # these are prompts, and the sound wrapper maps every flash to the
        # error tone. Repeating that once a second while you decide which key
        # to press would be unbearable.
        self._indicator = indicator
        self._config_path = config_path
        self._busy = threading.Event()

    @property
    def listener(self):
        """Whichever listener is live now. Shutdown stops this one."""
        return self._listener

    def request(self) -> None:
        """Menu handler. Returns at once; the work is on a worker thread."""
        if self._busy.is_set():
            say("Already waiting for a new hotkey.")
            return
        self._busy.set()
        threading.Thread(
            target=self._run, name="vocal-advantage-set-hotkey", daemon=True
        ).start()

    def _run(self) -> None:
        try:
            self._change()
        finally:
            # Whatever happened, the menu item works again.
            self._busy.clear()

    def _change(self) -> None:
        hotkey_module, _ = platform_modules()
        say("Hold the key or combo you want, then release it.")

        stop_prompt = threading.Event()
        threading.Thread(
            target=self._keep_prompting, args=(stop_prompt,),
            name="vocal-advantage-hotkey-prompt", daemon=True,
        ).start()

        # Our hook has to be down before capture installs its own, or both see
        # the keypress and the app starts recording while you are choosing.
        _safe_call("hotkey listener stop", self._listener.stop)
        try:
            spec = hotkey_module.capture_hotkey()
        except Exception as error:  # noqa: BLE001 - HotkeyError, TimeoutError, anything
            stop_prompt.set()
            warn("Hotkey unchanged: %s" % error)
            self._indicator.flash("hotkey unchanged")
            self._restart(self._spec)
            return
        stop_prompt.set()

        if not self._save(spec):
            self._restart(self._spec)
            return

        self._spec = spec
        # While no hook is installed, so no key event can be in flight.
        self._controller.set_hotkey(spec)
        self._restart(spec)
        say("Hotkey is now %s." % spec)
        self._indicator.flash("hotkey: %s" % spec)

    def _keep_prompting(self, stop: threading.Event) -> None:
        while not stop.wait(self.PROMPT_EVERY_S):
            self._indicator.flash("press your new hotkey")

    def _save(self, spec) -> bool:
        try:
            cfg = load_config(self._config_path)
            # The raw key names, not str(spec)'s display form: sorted key names
            # are deterministic and are exactly what parse_hotkey reads back.
            cfg["hotkey"] = "+".join(sorted(spec.keys))
            save_config(cfg, self._config_path)
            return True
        except Exception:  # noqa: BLE001
            warn("Could not save the new hotkey; keeping the old one.")
            warn(traceback.format_exc())
            return False

    def _restart(self, spec) -> None:
        """Get a working hook back. The one thing that must not fail quietly."""
        hotkey_module, _ = platform_modules()
        try:
            self._listener = hotkey_module.HotkeyListener(spec, self._on_key)
            self._listener.start()
        except Exception:  # noqa: BLE001
            warn("The hotkey could not be restarted. Quit from the tray and "
                 "start the app again.")
            warn(traceback.format_exc())
            self._indicator.flash("hotkey lost - restart")


def _make_player(cfg: dict) -> Player:
    return Player(
        enabled=bool(cfg.get("sounds", True)),
        on_start=bool(cfg.get("sound_on_start", False)),
    )


def _make_history(cfg: dict, path: Path = HISTORY_PATH) -> History:
    history = History(path, enabled=bool(cfg.get("history", True)))
    if history.enabled:
        say("History: every dictation is appended to %s" % path)
    return history


def _load_dictionary(path: Path = DICTIONARY_PATH):
    """The personal dictionary, or an empty one. Never raises, and says so.

    An accuracy aid: losing it costs accuracy, never the product.
    """
    try:
        dictionary = load_dictionary(path)
    except Exception:  # noqa: BLE001 - load_dictionary already guards, belt too
        warn("The personal dictionary could not be loaded; carrying on without it.")
        warn(traceback.format_exc())
        return _EmptyDictionary()

    if dictionary:
        say(
            "Personal dictionary: %d word(s) to listen for, %d fix(es)."
            % (len(dictionary.words), len(dictionary.fixes))
        )
    return dictionary


class _EmptyDictionary:
    """Stands in when even the guarded loader failed. Does nothing, safely."""

    words: list = []
    fixes: dict = {}
    hotwords = ""

    def __bool__(self) -> bool:
        return False

    def apply(self, text: str) -> str:
        return text


def _make_flow_bar(cfg: dict, indicator):
    """The overlay, or None if it is switched off or refuses to start.

    Never raises. Dictation is the product and the bar is decoration: losing it
    costs a warning on the console and nothing else.
    """
    if not cfg.get("flow_bar", True):
        say("Flow bar off in config.json - tray icon only.")
        return None
    try:
        if sys.platform == "darwin":
            from vocal_advantage.flowbar_mac import FlowBar
        else:
            from vocal_advantage.flowbar_win import FlowBar

        bar = FlowBar(
            indicator,
            position=cfg["flow_bar_position"],
            point=cfg["flow_bar_point"],
        )
        bar.open()
        return bar
    except Exception:  # noqa: BLE001 - decoration must never stop dictation
        warn("The floating bar could not be created, so there is no overlay.")
        warn("Dictation and the hotkey are unaffected.")
        warn(traceback.format_exc())
        return None


def _menu_items(bar, changer, config_path: Path):
    """The (title, action) pairs between the status line and Quit.

    A title may be a callable, re-read whenever the menu opens, so an item can
    say what clicking it will DO rather than what the state currently is. An
    action of None drops the item entirely -- which is how "Move bar"
    disappears when there is no bar to move.
    """
    toggle_move, is_movable = _move_mode(bar, config_path)
    return [
        (
            (lambda: "Lock bar in place" if is_movable() else "Move bar")
            if is_movable
            else "Move bar",
            toggle_move,
        ),
        ("Change hotkey...", changer.request),
    ]


def _make_tray(indicator, on_quit: Callable[[], None], items=()):
    """The tray / menu-bar icon, or None if it refuses to start. Never raises."""
    try:
        if sys.platform == "darwin":
            from vocal_advantage.tray_mac import TrayIcon
        else:
            from vocal_advantage.tray_win import TrayIcon

        return TrayIcon(indicator, on_quit, items)
    except Exception:  # noqa: BLE001 - as above
        warn("The tray icon could not be created.")
        warn("Dictation and the hotkey are unaffected; press Ctrl+C to quit.")
        warn(traceback.format_exc())
        return None


def _save_flow_bar_point(bar, config_path: Path) -> None:
    """Remember where the bar was dragged to, so it is there next launch.

    Re-reads config.json rather than writing back the dict the app started
    with: the file is documented as hand-editable, and a session that has been
    running for hours would otherwise silently revert every edit made to it in
    the meantime.
    """
    if bar is None:
        return
    try:
        point = bar.current_point()
        if point is None:
            return
        cfg = load_config(config_path)
        cfg["flow_bar_point"] = point
        save_config(cfg, config_path)
        say("Bar position saved (%.0f, %.0f)." % (point[0], point[1]))
    except Exception:  # noqa: BLE001 - never worth losing the session over
        warn("Could not save the bar position.")
        warn(traceback.format_exc())


def _move_mode(bar, config_path: Path):
    """The "Move bar" menu item: returns (toggle, is_movable).

    Move mode and click-through are one setting, not two -- a window that
    ignores mouse events never receives the mouse-down that starts a drag. So
    turning this on genuinely does make the pill intercept clicks, which is why
    it is an explicit menu item with a visible outline rather than something
    always available.

    Returns (None, None) when there is no bar, so the menu simply leaves the
    item out instead of offering something that cannot work.
    """
    if bar is None:
        return None, None

    def toggle() -> None:
        movable = not bar.movable
        bar.set_movable(movable)
        if movable:
            say("Move bar: drag it, then choose Lock bar in place.")
        else:
            _save_flow_bar_point(bar, config_path)

    return toggle, (lambda: bar.movable)


def _stop_dictation(listener, recorder, worker, events, stop_event) -> None:
    """Put the microphone and the keyboard down. Order matters; see comments."""
    # Hook first: no new key events may arrive while the rest is torn down.
    _safe_call("hotkey listener stop", listener.stop)
    if sys.platform == "win32":
        try:
            import keyboard

            keyboard.unhook_all()  # belt and braces: nothing may still see keys
        except Exception:  # noqa: BLE001
            pass

    stop_event.set()
    events.put(None)  # unblocks the loop if it is sitting in get()
    worker.join(timeout=5.0)

    try:
        if recorder.is_recording:
            # Closes the stream, so the "microphone in use" light goes out.
            recorder.stop()
    except Exception:  # noqa: BLE001
        warn(traceback.format_exc())


def _run_app_windows(config_path: Path = CONFIG_PATH) -> int:
    """Build the object graph, start the threads, hand the main thread to the tray.

    The tray owns the main thread here, which it could not while the pill was a
    tkinter window. `flowbar_win` renders with UpdateLayeredWindow on its own
    thread instead, so nothing else competes for it.
    """
    # Function-local on purpose: sounddevice and the keyboard hook are real
    # hardware bindings, so `import vocal_advantage.main` stays cheap and
    # testable, and it is structurally impossible to import the model stack
    # before import_transcriber_class() has run prepare().
    from vocal_advantage import paste_win
    from vocal_advantage.flowbar import Indicator
    from vocal_advantage.flowbar_win import set_dpi_awareness
    from vocal_advantage.hotkey_win import HotkeyListener
    from vocal_advantage.recorder import Recorder

    cfg = load_config(config_path)
    # load_config already warned and substituted the default if this was
    # garbage, so this parse cannot raise.
    spec = parse_hotkey(cfg["hotkey"])
    say("Hotkey: %s" % spec)

    # MUST be before any window exists. Windows fixes a process's DPI awareness
    # when that process creates its first window; called afterwards it is a
    # silent no-op answering E_ACCESSDENIED, the pill renders blurry on a
    # scaled display, and the "bottom centre of the screen" arithmetic is wrong
    # above 100% scaling. Neither flowbar_win nor pystray calls it for us.
    set_dpi_awareness()

    recorder = Recorder()
    # The level tap the waveform reads. A plain float on the recorder, written
    # by PortAudio's thread and read here lock-free -- there is no second
    # microphone stream anywhere in this project.
    indicator = Indicator(level_source=lambda: recorder.level)

    dictionary = _load_dictionary()
    player = _make_player(cfg)
    history = _make_history(cfg)

    say("Loading the %s model on device=%s ..." % (cfg["model"], cfg["device"]))
    transcriber_cls = import_transcriber_class()
    transcriber = transcriber_cls(
        model_name=cfg["model"],
        device=cfg["device"],
        language=cfg["language"],
        min_duration_s=float(cfg["min_duration_s"]),
        hotwords=dictionary.hotwords,
    )
    # The first transcribe() pays 1-3s of CUDA init. Paying it now is what
    # makes the first real dictation as fast as the tenth.
    transcriber.warm_up()
    say("Model ready.")
    _warm_up_ai_cleanup(cfg)

    controller = DictationController(
        hotkey=spec,
        recorder=recorder,
        transcriber=transcriber,
        # paste_win itself satisfies the paster protocol: a module-level
        # paste_text(str) -> bool is the whole interface.
        paster=RecordingPaster(
            CleaningPaster(paste_win, clean=_cleaner(cfg, dictionary)),
            history,
        ),
        indicator=SoundingIndicator(indicator, player),
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
        # Runs on the keyboard hook's thread. It must do nothing but enqueue --
        # any real work here would block Windows' low-level keyboard hook.
        events.put((key_name, is_down))

    listener = HotkeyListener(spec, on_key)
    listener.start()

    # UI last: by here the hotkey already works, so anything below failing
    # costs decoration and never dictation.
    bar = _make_flow_bar(cfg, indicator)
    changer = HotkeyChanger(
        listener=listener, spec=spec, on_key=on_key, controller=controller,
        indicator=indicator, config_path=config_path,
    )
    tray = _make_tray(indicator, lambda: None, _menu_items(bar, changer, config_path))

    shutting_down = threading.Event()

    def shutdown() -> None:
        if shutting_down.is_set():
            return
        shutting_down.set()
        say("Shutting down ...")
        # Quitting mid-drag should not throw the new position away.
        if bar is not None and bar.movable:
            _save_flow_bar_point(bar, config_path)
        # changer.listener, not `listener`: "Change hotkey" replaces the
        # listener object, and stopping the original would leave the live hook
        # installed after the app exits.
        _stop_dictation(
            changer.listener, recorder, worker, events, stop_event
        )
        if bar is not None:
            _safe_call("flow bar close", bar.close)
        if tray is not None:
            _safe_call("tray stop", tray.stop)

    if tray is not None:
        tray._on_quit = shutdown  # the menu's Quit item
        say("Ready. Hold %s and speak. Quit from the tray icon." % spec)
        try:
            tray.run()          # blocks on the main thread until Quit
        except KeyboardInterrupt:
            say("")
        finally:
            shutdown()
        return 0

    # No tray: fall back to waiting, so dictation still works with no UI at all.
    say("Ready. Hold %s and speak. Press Ctrl+C to quit." % spec)
    try:
        while worker.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        say("")
    finally:
        shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    global _INSTANCE_LOCK

    # --version and --help exit inside parse_args, before the mutex, so they
    # still work while the app is running.
    args = build_parser().parse_args(argv)

    lock = acquire_single_instance_lock()
    if lock is None:
        warn("Vocal Advantage is already running. Quit the other copy first.")
        return 1
    _INSTANCE_LOCK = lock

    if args.set_hotkey:
        # --set-hotkey installs its own keyboard hook, so it must not run
        # alongside the app; holding the same mutex is what prevents that.
        hotkey_module, _ = platform_modules()

        return run_set_hotkey(hotkey_module.capture_hotkey, CONFIG_PATH)

    return run_app(CONFIG_PATH)


def _run_app_mac(config_path: Path = CONFIG_PATH) -> int:
    """The macOS launcher. One NSApplication on the main thread; no tkinter.

    The menu-bar icon and the Flow Bar are both AppKit, so they share a single
    run loop and there is no contention for the main thread. The event tap needs
    a CFRunLoop too, but HotkeyListener owns one on its own thread.

    Accessory activation policy (see flowbar_mac.ensure_app) is what keeps this
    process out of the Dock and out of Cmd-Tab -- the thing the earlier macOS
    port avoided a Tk root in order to dodge.
    """
    from vocal_advantage import paste_mac
    from vocal_advantage.flowbar import Indicator
    from vocal_advantage.hotkey_mac import HotkeyListener, HotkeyPermissionError
    from vocal_advantage.recorder import Recorder

    cfg = load_config(config_path)
    spec = parse_hotkey(cfg["hotkey"])
    say("Hotkey: %s" % spec)

    recorder = Recorder()
    # The level tap the waveform reads: a plain float on the recorder, written
    # by PortAudio's thread and read lock-free by the renderer. No second
    # microphone stream.
    indicator = Indicator(level_source=lambda: recorder.level)

    dictionary = _load_dictionary()
    player = _make_player(cfg)
    history = _make_history(cfg)

    say("Loading the %s model on device=%s ..." % (cfg["model"], cfg["device"]))
    transcriber_cls = import_transcriber_class()
    transcriber = transcriber_cls(
        model_name=cfg["model"],
        device=cfg["device"],
        language=cfg["language"],
        min_duration_s=float(cfg["min_duration_s"]),
        hotwords=dictionary.hotwords,
    )
    transcriber.warm_up()
    say("Model ready.")
    _warm_up_ai_cleanup(cfg)

    # The raw transcriber for partial passes: narrating every one of them would
    # bury the console. The final transcript still gets reported.
    live = LiveDictation(
        recorder=recorder,
        transcriber=transcriber,
        type_partial=paste_mac.type_partial,
        type_final=paste_mac.paste_text,
        clean=_cleaner(cfg, dictionary),
    )
    controller = DictationController(
        hotkey=spec,
        recorder=recorder,
        transcriber=NarratingTranscriber(transcriber),
        paster=RecordingPaster(live, history),
        indicator=SoundingIndicator(indicator, player),
        min_duration_s=float(cfg["min_duration_s"]),
        max_duration_s=float(cfg["max_duration_s"]),
        on_partial=live.on_partial if live_typing_enabled(cfg) else None,
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
        warn("")
        warn(str(error))
        stop_event.set()
        events.put(None)
        worker.join(timeout=2.0)
        return 1

    # UI last: the hotkey already works by here, so anything below failing costs
    # decoration and never dictation.
    from vocal_advantage.flowbar_mac import ensure_app

    app = None
    try:
        app = ensure_app()
    except Exception:  # noqa: BLE001
        warn("No NSApplication, so there is no menu bar icon and no overlay.")
        warn(traceback.format_exc())

    bar = _make_flow_bar(cfg, indicator) if app is not None else None
    changer = HotkeyChanger(
        listener=listener, spec=spec, on_key=on_key, controller=controller,
        indicator=indicator, config_path=config_path,
    )
    tray = (
        _make_tray(
            indicator, lambda: None, _menu_items(bar, changer, config_path)
        )
        if app is not None
        else None
    )

    shutting_down = threading.Event()

    def shutdown() -> None:
        if shutting_down.is_set():
            return
        shutting_down.set()
        say("Shutting down ...")
        # Quitting mid-drag should not throw the new position away.
        if bar is not None and bar.movable:
            _save_flow_bar_point(bar, config_path)
        # changer.listener, not `listener`: "Change hotkey" replaces the
        # listener object, and stopping the original would leave the live hook
        # installed after the app exits.
        _stop_dictation(
            changer.listener, recorder, worker, events, stop_event
        )
        if bar is not None:
            _safe_call("flow bar close", bar.close)
        if tray is not None:
            _safe_call("tray stop", tray.stop)

    if tray is not None:
        tray._on_quit = _quit_mac(shutdown)
        _safe_call("tray start", tray.start)

    if app is not None and (tray is not None or bar is not None):
        from PyObjCTools import AppHelper

        say("Ready. Hold %s and speak. Quit from the menu bar icon." % spec)
        try:
            AppHelper.runEventLoop()
        except KeyboardInterrupt:
            say("")
        finally:
            shutdown()
        return 0

    # No UI at all: wait, exactly as this launcher did before it had any.
    say("Ready. Hold %s and speak. Press Ctrl+C to quit." % spec)
    try:
        while worker.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        say("")
    finally:
        shutdown()
    return 0


def _quit_mac(shutdown: Callable[[], None]) -> Callable[[], None]:
    """Shut down, then stop the Cocoa run loop so the process actually exits.

    Without the second half the menu's Quit tears everything down and then sits
    in runEventLoop forever with no icon and no window -- an invisible orphan,
    which is precisely what "no orphan processes" rules out.
    """

    def quit_now() -> None:
        shutdown()
        try:
            from PyObjCTools import AppHelper

            AppHelper.stopEventLoop()
        except Exception:  # noqa: BLE001 - exiting regardless
            pass

    return quit_now


def run_app(config_path: Path = CONFIG_PATH) -> int:
    """Start the app, on whichever platform this is."""
    if sys.platform == "darwin":
        return _run_app_mac(config_path)
    return _run_app_windows(config_path)
