"""Look at the Flow Bar without running the app. macOS and Windows.

    python tools/flowbar_preview.py                     # cycle every state
    python tools/flowbar_preview.py --state recording   # hold one state
    python tools/flowbar_preview.py --position bottom-right

No model, no hotkey, no tray -- it starts in about a second, so the look and
the motion can be iterated on without a 30-second restart each time. The
microphone is real: in the recording state the bars are responding to your
actual voice, through the same `Recorder` the app uses.

**On Windows this is the first thing to run**, before the app itself. The
layered-window renderer was written on a Mac and never executed, so seeing the
pill on its own -- with no model load and no hotkey hook in the way -- is the
fastest way to find out whether it draws at all.

Not a pytest test. The maths behind the motion is tested in
tests/test_waveform.py and tests/test_flowbar.py; this is the part that has to
be looked at, which is exactly what the "Spec + Test Driven" note means by a
fixture you eyeball.

While it is running, check the things that cannot be asserted:

* Click straight through the pill into whatever is underneath. The click must
  land there, and the pill must never take focus or show a highlight.
* It sits above other windows, and stays put when you switch Space.
* The corners are round and the background outside them is the desktop, not a
  grey box.
* Nothing appears in the Dock or in Cmd-Tab.
* The bars glide. If anything lurches between heights, it is wrong.
"""
from __future__ import annotations

import argparse
import sys
import time

from vocal_advantage import flowbar
from vocal_advantage.flowbar import Indicator
from vocal_advantage.recorder import Recorder, RecorderError

if sys.platform == "darwin":
    from vocal_advantage.flowbar_mac import POSITIONS, FlowBar, ensure_app
else:
    from vocal_advantage.flowbar_win import POSITIONS, FlowBar, set_dpi_awareness

# (label, seconds). Recording gets the longest slot because it is the only one
# you can steer, by talking.
SCRIPT = [
    ("idle", 4.0),
    ("recording", 12.0),
    ("transcribing", 4.0),
]
# "message" is deliberately NOT in the cycle. It is a real state the app uses
# for "nothing heard" and "could not paste", but seeing a paste error scroll
# past every 20 seconds while you are judging the motion is just noise.
# Look at it on purpose with --state message.
MESSAGE = "could not paste - press Ctrl+V"


class Preview:
    """Walks the indicator through the states and owns the microphone."""

    def __init__(self, indicator: Indicator, recorder: Recorder) -> None:
        self._indicator = indicator
        self._recorder = recorder
        self._step = -1

    def enter(self, state: str) -> None:
        # The mic is open only while recording, exactly as the app does it, so
        # the macOS "microphone in use" dot behaves the same way here.
        if state == "recording":
            try:
                self._recorder.start()
            except RecorderError as error:
                print(f"No microphone, so the bars will stay flat: {error}")
        elif self._recorder.is_recording:
            self._recorder.stop()

        if state == "recording":
            self._indicator.show_recording()
        elif state == "transcribing":
            self._indicator.show_processing()
        elif state == "message":
            self._indicator.flash(MESSAGE)
        else:
            self._indicator.hide()
        print(f"  {state}")

    def advance(self) -> float:
        """Enter the next state and return how long to stay in it."""
        self._step = (self._step + 1) % len(SCRIPT)
        state, seconds = SCRIPT[self._step]
        self.enter(state)
        return seconds

    def stop(self) -> None:
        if self._recorder.is_recording:
            self._recorder.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--position", choices=POSITIONS, default="bottom-centre")
    parser.add_argument(
        "--state",
        choices=[name for name, _ in SCRIPT] + ["message"],
        help="hold one state instead of cycling through all of them",
    )
    parser.add_argument(
        "--fps", type=int, default=60, help="render rate, for comparing motion"
    )
    args = parser.parse_args(argv)

    if sys.platform == "darwin":
        ensure_app()
    else:
        # Before any window exists, or Windows stretches the pill and the
        # "bottom centre of the screen" arithmetic is wrong above 100% scaling.
        set_dpi_awareness()

    recorder = Recorder()
    indicator = Indicator(level_source=lambda: recorder.level)
    bar = FlowBar(indicator, position=args.position, fps=args.fps)
    bar.open()

    preview = Preview(indicator, recorder)
    message_period = flowbar.MESSAGE_FRAMES / args.fps

    print("Ctrl+C to quit.")
    try:
        if sys.platform == "darwin":
            _run_mac(preview, indicator, args, message_period)
        else:
            _run_windows(preview, indicator, args, message_period)
    except KeyboardInterrupt:
        print("")
    finally:
        preview.stop()
        bar.close()
    return 0


def _run_mac(preview, indicator, args, message_period) -> None:
    """macOS: AppKit owns the main thread, so the script runs on its run loop."""
    from PyObjCTools import AppHelper

    if args.state:
        preview.enter(args.state)
        if args.state == "message":
            # The message times itself out after 1.5s, so re-arm it or there
            # would be nothing to look at.
            def renew() -> None:
                indicator.flash(MESSAGE)
                AppHelper.callLater(message_period, renew)

            AppHelper.callLater(message_period, renew)
    else:
        def step() -> None:
            AppHelper.callLater(preview.advance(), step)

        step()

    AppHelper.runEventLoop()


def _run_windows(preview, indicator, args, message_period) -> None:
    """Windows: the bar owns its own thread, so this one only has to wait."""
    if args.state:
        preview.enter(args.state)
        while True:
            time.sleep(message_period if args.state == "message" else 0.5)
            if args.state == "message":
                indicator.flash(MESSAGE)
    else:
        while True:
            time.sleep(preview.advance())


if __name__ == "__main__":
    sys.exit(main())
