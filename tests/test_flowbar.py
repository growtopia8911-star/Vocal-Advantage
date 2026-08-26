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

from vocal_advantage import panel
from vocal_advantage import waveform as wf
from vocal_advantage.flowbar import (
    IDLE,
    MESSAGE,
    MESSAGE_FRAMES,
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
    # RECORDING now opens the panel, so by 300 frames the trace has widened
    # past BAR_COUNT bars -- idle_heights sized to whatever the panel is
    # currently showing is the equivalent check, not a fixed BAR_COUNT one.
    indicator = Indicator(level_source=lambda: 0.0)
    indicator.show_recording()
    frame = drain(indicator, 300)
    assert frame.heights == pytest.approx(
        wf.idle_heights(len(frame.heights)), abs=1e-3
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

def test_hide_returns_to_the_idle_state():
    # Whether the bar is still *drawn* on screen at that point is a separate,
    # alpha-gated question -- see the "visibility" section below. `hide()`'s
    # job is only ever to move the state machine back to IDLE.
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


# --- visibility (2026-08-25: the grow removed, idle now shows nothing) ------
#
# The user disliked watching the bar animate open: "I don't want my UI to
# show me it enhancing in size... it's just unnecessary." The panel now
# appears at full size the instant recording starts and disappears when it
# ends, rather than growing and shrinking. `Frame.visible` is the one signal
# both renderers hide/show a window against -- see docs/plans/
# 2026-08-25-flow-bar-panel.md's amendments for the fuller reasoning.


def test_idle_is_not_visible():
    assert Indicator().next_frame().visible is False


def test_recording_is_visible():
    indicator = Indicator()
    indicator.show_recording()
    assert indicator.next_frame().visible is True


def test_the_indicator_is_full_size_on_the_first_frame():
    # The two-band panel was replaced by the compact pill on 2026-08-25; what
    # this still guards is that whatever the active shape is, it arrives at
    # full size rather than being eased into.
    indicator = Indicator()
    indicator.show_recording()
    frame = indicator.next_frame()
    assert (frame.width, frame.height) == (
        panel.COMPACT_WIDTH, panel.COMPACT_HEIGHT,
    )


def test_no_frame_is_ever_an_intermediate_size_while_recording():
    # The whole point: no eased width or height, ever -- not on the first
    # frame, and not on any frame after it either.
    indicator = Indicator()
    indicator.show_recording()
    for _ in range(120):
        frame = indicator.next_frame()
        assert (frame.width, frame.height) == (
            panel.COMPACT_WIDTH, panel.COMPACT_HEIGHT,
        )


def test_transcribing_stays_visible_and_full_size():
    indicator = Indicator()
    indicator.show_processing()
    frame = indicator.next_frame()
    assert frame.visible is True
    assert (frame.width, frame.height) == (
        panel.COMPACT_WIDTH, panel.COMPACT_HEIGHT,
    )


def test_a_flash_message_is_visible_and_pill_shaped_and_opens_no_panel():
    indicator = Indicator()
    indicator.flash("could not paste - press Ctrl+V")
    frame = indicator.next_frame()
    assert frame.visible is True
    assert frame.open == 0.0
    assert frame.height == panel.COMPACT_HEIGHT


def test_leaving_the_active_shape_snaps_but_still_fades_before_hiding():
    # `open` -- and the size it drives -- snaps immediately: there is no
    # intermediate width or height on the way out either. But the frame stays
    # `visible` for a few more frames while `pill_alpha` eases down, and only
    # goes not-visible once that fade has actually finished -- hiding the
    # instant the mode changes would cut the fade off before it is seen.
    indicator = Indicator()
    indicator.show_recording()
    for _ in range(30):
        indicator.next_frame()
    indicator.hide()
    frame = indicator.next_frame()
    assert (frame.width, frame.height) == (float(wf.PILL_WIDTH), panel.COMPACT_HEIGHT)
    assert frame.visible is True
    assert drain(indicator, 200).visible is False


def test_after_a_message_expires_the_bar_becomes_not_visible_again():
    indicator = Indicator()
    indicator.flash("could not paste - press Ctrl+V")
    assert drain(indicator, MESSAGE_FRAMES - 1).visible is True
    # Enough further frames for the mode to fall back to IDLE *and* for the
    # alpha fade that follows it to actually finish.
    assert drain(indicator, 400).visible is False


def test_while_movable_idle_is_visible():
    indicator = Indicator()
    indicator.set_movable(True)
    assert indicator.next_frame().visible is True


def test_while_movable_idle_actually_draws_something_not_just_a_flag():
    # `visible` alone is not the whole promise -- if the pill's own alpha
    # settled at 0 there would be nothing to see even in an on-screen window.
    # Movable idle reuses the old resting pill's alpha instead.
    indicator = Indicator()
    indicator.set_movable(True)
    frame = drain(indicator, 200)
    assert frame.pill_alpha > 0.5


def test_turning_movable_off_lets_idle_fade_out_again():
    indicator = Indicator()
    indicator.set_movable(True)
    drain(indicator, 200)
    indicator.set_movable(False)
    assert drain(indicator, 200).visible is False
