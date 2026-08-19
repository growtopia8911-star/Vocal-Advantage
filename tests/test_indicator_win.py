"""Headless tests for the recording pill's queue and state logic.

The pill has two halves:

* portable - a thread-safe queue plus a tick-driven state machine that decides
  what the pill should look like right now (`Indicator`);
* Windows - a tkinter Toplevel carrying no-activate window styles (`_TkPill`),
  which needs a display and has no honest automated test. It is covered by the
  three manual checklists in Steps 8, 9 and 10 of this task instead.

The seam is `Indicator._new_pill()`. These tests subclass `Indicator`, swap in a
fake pill that records the frames it is asked to draw, and pass a fake root that
records `after()` calls. Nothing here touches tkinter or Win32.
"""
from __future__ import annotations

import threading

import pytest

from vocal_advantage.indicator_win import (
    FLASH_TICKS,
    PROCESSING_FRAMES,
    PROCESSING_TICKS_PER_FRAME,
    PUMP_INTERVAL_MS,
    Frame,
    Indicator,
)

HIDDEN = Frame(visible=False, dot=False, text="")
RECORDING = Frame(visible=True, dot=True, text="")


class FakeRoot:
    """Stands in for tk.Tk(). All Indicator ever asks of a root is after()."""

    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []

    def after(self, delay_ms, callback):
        self.after_calls.append((delay_ms, callback))
        return f"after#{len(self.after_calls)}"


class FakePill:
    """Records what it was asked to draw.

    Note there is deliberately no destroy() here: the pill window is created
    once at startup and never destroyed, so if Indicator ever tried to destroy
    it these tests would blow up with AttributeError.
    """

    def __init__(self) -> None:
        self.frames: list[Frame] = []

    def render(self, frame: Frame) -> None:
        self.frames.append(frame)


class HeadlessIndicator(Indicator):
    """The real Indicator with the Windows window swapped for a recorder."""

    def __init__(self, root) -> None:
        super().__init__(root)
        self.pills: list[FakePill] = []

    def _new_pill(self) -> FakePill:
        pill = FakePill()
        self.pills.append(pill)
        return pill


@pytest.fixture
def root():
    return FakeRoot()


@pytest.fixture
def ind(root):
    return HeadlessIndicator(root)


def pumps(indicator, n=1):
    """Advance the pill by n scheduled ticks (n * 50ms of wall clock)."""
    for _ in range(n):
        indicator.pump()


def frames(indicator):
    return indicator.pills[0].frames


def test_timing_constants_line_up_with_the_50ms_pump():
    assert PUMP_INTERVAL_MS == 50
    assert FLASH_TICKS * PUMP_INTERVAL_MS == 1500
    assert PROCESSING_TICKS_PER_FRAME * PUMP_INTERVAL_MS == 350
    assert len(PROCESSING_FRAMES) == 3


def test_nothing_is_built_or_drawn_before_the_first_pump(ind):
    # Public methods run on other threads; they must not touch the window.
    ind.show_recording()
    assert ind.pills == []


def test_the_window_is_built_once_and_the_hidden_state_is_drawn_once(ind):
    pumps(ind, 20)
    assert len(ind.pills) == 1
    assert frames(ind) == [HIDDEN]


def test_pump_reschedules_itself_every_50ms(ind, root):
    pumps(ind, 3)
    assert [delay for delay, _ in root.after_calls] == [PUMP_INTERVAL_MS] * 3
    assert all(callback == ind.pump for _, callback in root.after_calls)


@pytest.mark.parametrize(
    "trigger, expected",
    [
        ("show_recording", Frame(visible=True, dot=True, text="")),
        ("show_processing", Frame(visible=True, dot=False, text=PROCESSING_FRAMES[0])),
        ("hide", Frame(visible=False, dot=False, text="")),
    ],
)
def test_each_state_draws_its_own_frame(ind, trigger, expected):
    getattr(ind, trigger)()
    pumps(ind)
    assert frames(ind)[-1] == expected


def test_a_steady_state_is_not_redrawn_every_tick(ind):
    ind.show_recording()
    pumps(ind, 40)  # two seconds of holding the hotkey
    assert frames(ind) == [RECORDING]


def test_processing_dots_animate_and_wrap(ind):
    ind.show_processing()
    pumps(ind)
    assert frames(ind)[-1] == Frame(visible=True, dot=False, text=PROCESSING_FRAMES[0])

    pumps(ind, PROCESSING_TICKS_PER_FRAME - 1)
    assert frames(ind)[-1].text == PROCESSING_FRAMES[0]  # not yet

    pumps(ind, 1)
    assert frames(ind)[-1].text == PROCESSING_FRAMES[1]

    pumps(ind, PROCESSING_TICKS_PER_FRAME)
    assert frames(ind)[-1].text == PROCESSING_FRAMES[2]

    pumps(ind, PROCESSING_TICKS_PER_FRAME)
    assert frames(ind)[-1].text == PROCESSING_FRAMES[0]  # wraps round


def test_flash_shows_the_message_then_hides_itself(ind):
    ind.flash("nothing heard")
    pumps(ind, FLASH_TICKS)
    assert frames(ind)[-1] == Frame(visible=True, dot=False, text="nothing heard")

    pumps(ind, 1)
    assert frames(ind)[-1] == HIDDEN


def test_a_new_state_cancels_a_running_flash(ind):
    ind.flash("nothing heard")
    pumps(ind, 5)
    ind.show_recording()
    pumps(ind)
    assert frames(ind)[-1] == RECORDING

    # ...and recording, unlike a flash, never auto-hides.
    pumps(ind, FLASH_TICKS + 5)
    assert frames(ind)[-1] == RECORDING


def test_the_last_command_in_a_batch_wins(ind):
    ind.show_recording()
    ind.show_processing()
    ind.hide()
    ind.flash("mic error")
    pumps(ind)
    assert frames(ind) == [Frame(visible=True, dot=False, text="mic error")]


def test_public_methods_are_safe_to_call_from_other_threads(ind):
    # tkinter is not thread-safe and the controller thread is the one that knows
    # when recording starts, so the queue is the whole point of this class.
    start = threading.Barrier(4)

    def hammer():
        start.wait()
        for _ in range(200):
            ind.show_recording()
            ind.show_processing()
            ind.flash("busy")
            ind.hide()

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert ind.pills == []  # 3200 calls, not one window touched

    pumps(ind)  # a single pump drains the whole backlog
    assert len(ind.pills) == 1
    assert frames(ind) == [HIDDEN]  # every thread's last call was hide()
