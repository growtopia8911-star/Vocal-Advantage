"""The Flow Bar state machine, driven headlessly.

No window is created here. `Indicator` is deliberately split so that everything
except the actual drawing can be tested: the controller's four methods only
enqueue, and `next_frame()` -- which the render thread calls 60 times a second
-- turns the queue plus the current audio level into one `Frame`. The renderer
receives a `Frame` and knows nothing else.

This is the same seam `test_indicator_win.py` used for the old pill, kept
because it is what makes the timing testable at full speed with no screen.
"""

from __future__ import annotations

import threading

import pytest

from vocal_advantage import waveform as wf
from vocal_advantage.flowbar import (
    IDLE,
    MESSAGE,
    RECORDING,
    TRANSCRIBING,
    Indicator,
    message_width,
)


def drain(indicator, frames):
    """Advance the render loop `frames` times and return the last Frame."""
    frame = None
    for _ in range(frames):
        frame = indicator.next_frame()
    return frame


# --- resting state ----------------------------------------------------------

def test_starts_idle():
    assert Indicator().next_frame().state == IDLE


def test_idle_is_completely_still():
    # "It sits there all day, so it must never catch my eye."
    indicator = Indicator()
    settled = drain(indicator, 200)
    assert drain(indicator, 60).heights == settled.heights


def test_idle_settles_on_the_idle_row():
    indicator = Indicator()
    frame = drain(indicator, 200)
    assert frame.heights == pytest.approx(wf.idle_heights(wf.BAR_COUNT))


def test_idle_ignores_the_audio_level():
    # The mic stream is closed when not recording, but a stale level must not
    # animate the idle row even if one is left behind.
    indicator = Indicator(level_source=lambda: 0.5)
    frame = drain(indicator, 200)
    assert frame.heights == pytest.approx(wf.idle_heights(wf.BAR_COUNT))


# --- recording --------------------------------------------------------------

def test_show_recording_enters_the_recording_state():
    indicator = Indicator()
    indicator.show_recording()
    assert indicator.next_frame().state == RECORDING


def test_recording_responds_to_the_audio_level():
    indicator = Indicator(level_source=lambda: 0.2)
    indicator.show_recording()
    loud = drain(indicator, 200)

    quiet = Indicator(level_source=lambda: 0.0)
    quiet.show_recording()
    silent = drain(quiet, 200)

    assert max(loud.heights) > max(silent.heights)


def test_recording_at_silence_looks_like_idle():
    indicator = Indicator(level_source=lambda: 0.0)
    indicator.show_recording()
    frame = drain(indicator, 300)
    assert frame.heights == pytest.approx(
        wf.idle_heights(wf.BAR_COUNT), abs=1e-3
    )


def test_louder_speech_makes_taller_bars():
    heights = []
    for rms in (0.005, 0.02, 0.08):
        indicator = Indicator(level_source=lambda r=rms: r)
        indicator.show_recording()
        heights.append(max(drain(indicator, 300).heights))
    assert heights == sorted(heights)


def test_new_audio_appears_at_the_left_edge():
    # Replaces a centre-weighting test. The bars are a history now: what you
    # just said is on the left, and the right is a second ago.
    indicator = Indicator(level_source=lambda: 0.08)
    indicator.show_recording()
    frame = drain(indicator, wf.SCROLL_FRAMES + 3)
    assert frame.heights[0] > frame.heights[-1]


def test_a_failing_level_source_does_not_break_the_frame():
    # The renderer must survive a recorder that was torn down underneath it.
    def exploding():
        raise RuntimeError("mic went away")

    indicator = Indicator(level_source=exploding)
    indicator.show_recording()
    assert indicator.next_frame().state == RECORDING


# --- motion quality ---------------------------------------------------------

def test_bars_glide_rather_than_snapping():
    # The requirement, as an assertion: one frame after a loud level arrives,
    # the bars must be nowhere near their target.
    indicator = Indicator(level_source=lambda: 0.5)
    indicator.show_recording()
    first = indicator.next_frame()
    settled = drain(indicator, 300)
    assert max(first.heights) < max(settled.heights) * 0.5


def test_bars_take_several_frames_to_settle():
    indicator = Indicator(level_source=lambda: 0.5)
    indicator.show_recording()
    settled = max(drain(indicator, 300).heights)

    fresh = Indicator(level_source=lambda: 0.5)
    fresh.show_recording()
    frames = 0
    while max(fresh.next_frame().heights) < settled * 0.9:
        frames += 1
        assert frames < 200, "never settled"
    assert frames >= 4


def test_the_trace_drains_away_instead_of_resetting():
    # "When I release the hotkey, let the existing wave scroll off to the left
    # rather than resetting instantly."
    indicator = Indicator(level_source=lambda: 0.08)
    indicator.show_recording()
    drain(indicator, 300)

    indicator.hide()
    assert max(drain(indicator, 3).heights) > wf.IDLE_HEIGHT + 0.2


def test_the_trace_does_eventually_empty():
    indicator = Indicator(level_source=lambda: 0.08)
    indicator.show_recording()
    drain(indicator, 300)
    indicator.hide()
    assert drain(indicator, 600).heights == pytest.approx(
        wf.idle_heights(wf.BAR_COUNT)
    )


def test_transcribing_lets_the_trace_drain_before_the_sweep_takes_over():
    # A hard switch to the sweep on release would be the instant reset the
    # scrolling exists to avoid, so the two are combined rather than swapped.
    indicator = Indicator(level_source=lambda: 0.08)
    indicator.show_recording()
    tall = max(drain(indicator, 300).heights)

    indicator.show_processing()
    assert max(drain(indicator, 3).heights) == pytest.approx(tall, abs=0.2)


def test_state_changes_also_glide():
    # Recording -> transcribing must not teleport the bars either.
    indicator = Indicator(level_source=lambda: 0.9)
    indicator.show_recording()
    tall = drain(indicator, 300).heights
    indicator.show_processing()
    first = indicator.next_frame().heights
    assert max(abs(c - p) for c, p in zip(first, tall)) < 0.25


# --- transcribing -----------------------------------------------------------

def test_show_processing_enters_the_transcribing_state():
    indicator = Indicator()
    indicator.show_processing()
    assert indicator.next_frame().state == TRANSCRIBING


def test_transcribing_moves():
    indicator = Indicator()
    indicator.show_processing()
    seen = {drain(indicator, 5).heights for _ in range(12)}
    assert len(seen) > 1


def test_transcribing_ignores_the_audio_level():
    # It has stopped listening; showing the mic level would say otherwise.
    listening = Indicator(level_source=lambda: 0.9)
    listening.show_processing()
    deaf = Indicator(level_source=lambda: 0.0)
    deaf.show_processing()
    assert drain(listening, 40).heights == pytest.approx(
        drain(deaf, 40).heights
    )


def test_transcribing_is_distinct_from_idle_and_recording():
    idle = drain(Indicator(), 120)

    working = Indicator()
    working.show_processing()
    assert any(
        drain(working, 4).heights != pytest.approx(idle.heights)
        for _ in range(20)
    )


# --- messages ---------------------------------------------------------------

def test_flash_shows_the_message():
    indicator = Indicator()
    indicator.flash("nothing heard")
    frame = indicator.next_frame()
    assert frame.state == MESSAGE
    assert frame.text == "nothing heard"


def test_flash_returns_to_idle_on_its_own():
    indicator = Indicator()
    indicator.flash("nothing heard")
    assert drain(indicator, wf.BAR_COUNT).state == MESSAGE
    assert indicator.next_frame  # sanity
    assert drain(indicator, 600).state == IDLE


def test_flash_widens_the_pill_and_it_eases_back():
    indicator = Indicator()
    indicator.flash("could not paste - press Ctrl+V")
    widened = drain(indicator, 120).width
    assert widened > wf.PILL_WIDTH

    settled = drain(indicator, 600)
    assert settled.width == pytest.approx(wf.PILL_WIDTH, abs=1.0)


def test_pill_width_glides_rather_than_jumping():
    indicator = Indicator()
    previous = indicator.next_frame().width
    indicator.flash("could not paste - press Ctrl+V")
    for _ in range(200):
        current = indicator.next_frame().width
        assert abs(current - previous) < 30
        previous = current


def test_message_width_grows_with_the_text():
    assert message_width("") <= message_width("nothing heard")
    assert message_width("nothing heard") < message_width(
        "could not paste - press Ctrl+V"
    )


def test_message_width_is_never_narrower_than_the_pill():
    assert message_width("") == wf.PILL_WIDTH


def test_hide_cancels_a_flash():
    indicator = Indicator()
    indicator.flash("nothing heard")
    indicator.next_frame()
    indicator.hide()
    assert indicator.next_frame().state == IDLE


# --- the controller's four methods, from any thread -------------------------

def test_hide_returns_to_idle_without_hiding_the_bar():
    # The bar is always visible now; hide() means "go quiet", not "disappear".
    indicator = Indicator()
    indicator.show_recording()
    indicator.next_frame()
    indicator.hide()
    assert indicator.next_frame().state == IDLE


def test_commands_can_be_sent_from_another_thread():
    indicator = Indicator()
    thread = threading.Thread(target=indicator.show_recording)
    thread.start()
    thread.join()
    assert indicator.next_frame().state == RECORDING


def test_the_last_command_in_a_burst_wins():
    indicator = Indicator()
    indicator.show_recording()
    indicator.show_processing()
    indicator.hide()
    assert indicator.next_frame().state == IDLE


def test_controller_methods_never_block_without_a_render_loop():
    # If the bar is switched off in config there is no render thread draining
    # the queue. The controller must not notice or stall.
    indicator = Indicator()
    for _ in range(5000):
        indicator.show_recording()
        indicator.show_processing()
        indicator.hide()


# --- the tray's status line -------------------------------------------------

def test_status_text_starts_idle():
    assert Indicator().status_text() == "Idle"


def test_status_text_follows_the_state():
    indicator = Indicator()
    indicator.show_recording()
    assert indicator.status_text() == "Recording"
    indicator.show_processing()
    assert indicator.status_text() == "Transcribing"
    indicator.hide()
    assert indicator.status_text() == "Idle"


def test_status_text_updates_without_a_render_loop():
    # With flow_bar off, nothing calls next_frame(). The tray must still be
    # right, so status cannot be computed from the drained queue.
    indicator = Indicator()
    indicator.show_recording()
    assert indicator.status_text() == "Recording"
