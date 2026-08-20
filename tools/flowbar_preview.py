"""Look at the Flow Bar without running the app. macOS.

    python tools/flowbar_preview.py                     # cycle every state
    python tools/flowbar_preview.py --state recording   # hold one state
    python tools/flowbar_preview.py --position bottom-right

No model, no hotkey, no tray -- it starts in about a second, so the look and
the motion can be iterated on without a 30-second restart each time. The
microphone is real: in the recording state the bars are responding to your
actual voice, through the same `Recorder` the app uses.

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

from PyObjCTools import AppHelper

from vocal_advantage import flowbar
from vocal_advantage.flowbar import Indicator
from vocal_advantage.flowbar_mac import POSITIONS, FlowBar, ensure_app
from vocal_advantage.recorder import Recorder, RecorderError

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

    if sys.platform != "darwin":
        print("This preview is macOS only.", file=sys.stderr)
        return 1

    ensure_app()

    recorder = Recorder()
    indicator = Indicator(level_source=lambda: recorder.level)
    bar = FlowBar(indicator, position=args.position, fps=args.fps)
    bar.open()

    preview = Preview(indicator, recorder)

    if args.state:
        preview.enter(args.state)
        if args.state == "message":
            # The message times itself out after 1.5s, so re-arm it or there
            # would be nothing to look at.
            def renew() -> None:
                indicator.flash(MESSAGE)
                AppHelper.callLater(flowbar.MESSAGE_FRAMES / args.fps, renew)

            AppHelper.callLater(flowbar.MESSAGE_FRAMES / args.fps, renew)
    else:
        def step() -> None:
            AppHelper.callLater(preview.advance(), step)

        step()

    print("Ctrl+C to quit.")
    try:
        AppHelper.runEventLoop()
    except KeyboardInterrupt:
        print("")
    finally:
        preview.stop()
        bar.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
