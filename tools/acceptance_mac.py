"""The acceptance checks that need no human, run in one go.

    uv run python tools/acceptance_mac.py

SPEC.md's acceptance list was written for Windows and has drifted: it still
expects the dictated text to be left on the clipboard, which was deliberately
removed. This covers the machine-checkable half for macOS. The half that needs
eyes and a voice is in docs/ACCEPTANCE-MAC.md.

Nothing here touches the real config.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if detail and not ok:
        print(f"        {detail}")


def main() -> int:
    print("\nAutomated acceptance checks\n" + "=" * 60)

    # -- config -------------------------------------------------------------
    from vocal_advantage.config import DEFAULTS, load_config

    tmp = Path(tempfile.mkdtemp()) / "config.json"
    tmp.write_text(json.dumps({"hotkey": "nonsense"}))
    cfg = load_config(tmp)
    check("a nonsense hotkey falls back instead of crashing",
          cfg["hotkey"] == DEFAULTS["hotkey"], f"got {cfg['hotkey']!r}")

    tmp2 = Path(tempfile.mkdtemp()) / "config.json"
    fresh = load_config(tmp2)
    check("a missing config is written with defaults",
          tmp2.exists() and fresh["model"] == DEFAULTS["model"])

    # -- hotkey rules -------------------------------------------------------
    from vocal_advantage.hotkey_spec import HotkeyError, parse_hotkey

    try:
        parse_hotkey("caps lock")
        check("CapsLock is refused as a hotkey", False, "it was accepted")
    except HotkeyError as exc:
        check("CapsLock is refused as a hotkey", True, str(exc))
    try:
        parse_hotkey("left cmd")
        check("Left Cmd parses as a hotkey", True)
    except HotkeyError as exc:
        check("Left Cmd parses as a hotkey", False, str(exc))

    # -- the guards that stop stray output ----------------------------------
    from vocal_advantage.config import load_config as lc
    from vocal_advantage.transcriber import SAMPLE_RATE, Transcriber

    live = lc()
    t = Transcriber(live["model"], live["device"], live["language"],
                    float(live["min_duration_s"]))
    t.warm_up()

    tap = np.zeros(int(0.2 * SAMPLE_RATE), dtype=np.float32)
    check("a quick tap produces nothing", t.transcribe(tap) == "")

    rng = np.random.default_rng(0)
    room = (rng.standard_normal(5 * SAMPLE_RATE) * 0.002).astype(np.float32)
    heard = t.transcribe(room)
    check("five seconds of a quiet room produces nothing",
          heard == "", f"it produced {heard!r}")

    # -- cleanup ------------------------------------------------------------
    from vocal_advantage.cleanup import clean_speech
    from vocal_advantage.main import _cleaner

    cleaner = _cleaner(live)
    got = cleaner("Um, so I I think this works")
    check("fillers and stutters are removed before typing",
          "Um" not in got and got.count("I") == 1, f"got {got!r}")
    check("clean text is returned untouched",
          clean_speech("This is fine.") == "This is fine.")

    # -- single instance ----------------------------------------------------
    from vocal_advantage.main import (acquire_single_instance_lock,
                                      release_single_instance_lock)

    name = "VocalAdvantageAcceptance"
    first = acquire_single_instance_lock(name)
    second = acquire_single_instance_lock(name)
    check("a second copy cannot take the lock",
          first is not None and second is None)
    release_single_instance_lock(first)

    # -- startup ------------------------------------------------------------
    log = Path(tempfile.mkdtemp()) / "boot.log"
    with log.open("w") as handle:
        proc = subprocess.Popen([sys.executable, "-u", "-m", "vocal_advantage"],
                                cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    ready = False
    for _ in range(90):
        if log.exists() and "Ready." in log.read_text():
            ready = True
            break
        time.sleep(1)
    text = log.read_text()
    check("the app reaches 'Ready.'", ready, text[-300:])
    check("startup prints no WARNING", "WARNING" not in text,
          [l for l in text.splitlines() if "WARNING" in l][:2])

    # a second launch while it runs must refuse
    other = subprocess.run([sys.executable, "-m", "vocal_advantage"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
    check("a second launch refuses with a clear message",
          "already running" in (other.stdout + other.stderr).lower(),
          (other.stdout + other.stderr)[:200])

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    check("it shuts down when told to", proc.poll() is not None)

    # -- report -------------------------------------------------------------
    failed = [name for ok, name, _ in results if not ok]
    print("=" * 60)
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("\nFailed:")
        for name in failed:
            print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
