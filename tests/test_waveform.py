"""The waveform maths: audio level in, bar heights out.

Everything here is pure. The renderer turns these numbers into pixels and the
NSPanel/layered window put them on screen, but nothing in this file touches a
screen -- which is the whole reason the motion can be tested at all.

Heights are normalised: 0.0 is a flat line, 1.0 is the tallest a bar may draw.
The renderer scales them to its own half-height, so nothing here knows about
pixels except `bar_layout`, which is told the width.

The bars are a scrolling history: audio enters at the left, everything shifts
right, the oldest falls off. Most of what is worth asserting is therefore about
*movement over time* rather than about a single frame, which is why so many of
these tests drive `ScrollingWave` for a run of frames and then look at where
things ended up.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from vocal_advantage import waveform as wf


# --- block_rms --------------------------------------------------------------

def test_block_rms_of_silence_is_zero():
    assert wf.block_rms(np.zeros(1024, dtype=np.float32)) == 0.0


def test_block_rms_of_empty_block_is_zero():
    # The PortAudio callback can hand us a zero-length block on the very first
    # or last callback of a stream. It must not raise or produce nan.
    assert wf.block_rms(np.empty(0, dtype=np.float32)) == 0.0


def test_block_rms_of_full_scale_square_wave_is_one():
    block = np.array([1.0, -1.0] * 512, dtype=np.float32)
    assert wf.block_rms(block) == pytest.approx(1.0)


def test_block_rms_of_sine_is_root_two_over_two():
    # The textbook value. Catches a mean-of-abs or a peak implementation
    # wearing an RMS name.
    t = np.linspace(0, 2 * math.pi, 2048, endpoint=False, dtype=np.float32)
    assert wf.block_rms(np.sin(t).astype(np.float32)) == pytest.approx(
        math.sqrt(2) / 2, abs=1e-3
    )


def test_block_rms_survives_a_block_full_of_nan():
    # A half-open stream on an unplugged device has been seen to deliver these.
    # A nan reaching the easing would poison every bar height permanently,
    # because nan propagates through the whole ease chain and never recovers.
    assert wf.block_rms(np.full(256, np.nan, dtype=np.float32)) == 0.0


# --- level_from_rms ---------------------------------------------------------

def test_level_from_rms_floor_and_ceiling():
    assert wf.level_from_rms(0.0) == 0.0
    assert wf.level_from_rms(10 ** (wf.FLOOR_DB / 20)) == pytest.approx(0.0)
    assert wf.level_from_rms(10 ** (wf.CEIL_DB / 20)) == pytest.approx(1.0)


def test_level_from_rms_clamps_both_ends():
    assert wf.level_from_rms(1e-9) == 0.0
    assert wf.level_from_rms(4.0) == 1.0


def test_level_from_rms_is_monotonic():
    levels = [wf.level_from_rms(r) for r in (0.001, 0.005, 0.02, 0.08, 0.3)]
    assert levels == sorted(levels)


def test_level_from_rms_puts_quiet_speech_in_the_visible_range():
    # The point of the dB mapping rather than raw RMS. Ordinary speech sits
    # around -30 dBFS; on a linear scale that is rms 0.03, which would move a
    # 16px bar by half a pixel and the pill would look broken.
    quiet_speech = 10 ** (-30 / 20)
    assert 0.3 < wf.level_from_rms(quiet_speech) < 0.8


# --- ease_bars --------------------------------------------------------------

def test_ease_bars_moves_toward_the_target_without_reaching_it():
    eased = wf.ease_bars((0.0, 0.0), (1.0, 1.0), 0.25)
    assert eased == pytest.approx((0.25, 0.25))


def test_ease_bars_with_alpha_one_lands_exactly():
    assert wf.ease_bars((0.0, 0.3), (1.0, 0.1), 1.0) == pytest.approx((1.0, 0.1))


def test_ease_bars_with_alpha_zero_does_not_move():
    assert wf.ease_bars((0.2, 0.3), (1.0, 0.1), 0.0) == pytest.approx((0.2, 0.3))


def test_ease_bars_never_overshoots_over_many_steps():
    current = (0.0,) * 4
    target = (1.0,) * 4
    for _ in range(500):
        current = wf.ease_bars(current, target, 0.25)
        assert all(h <= 1.0 for h in current)
    assert current == pytest.approx(target, abs=1e-3)


def test_ease_bars_converges_downward_too():
    current = (1.0,) * 4
    target = (0.1,) * 4
    for _ in range(500):
        current = wf.ease_bars(current, target, 0.25)
    assert current == pytest.approx(target, abs=1e-3)


def test_ease_bars_rejects_a_length_mismatch():
    # A silent zip() truncation here would drop bars off the end of the pill
    # and look like a rendering bug rather than a wiring bug.
    with pytest.raises(ValueError):
        wf.ease_bars((0.0, 0.0), (1.0,), 0.25)


def test_ease_glides_rather_than_snapping():
    # The requirement in numbers: from rest to a full-scale target the bars
    # must take a visible number of frames, not one. At 60fps, 6 frames is
    # 100ms -- a glide. Reaching 90% in one or two frames would be a snap.
    current = (0.0,)
    frames = 0
    while current[0] < 0.9:
        current = wf.ease_bars(current, (1.0,), wf.EASE_ALPHA)
        frames += 1
    assert 5 <= frames <= 30


# --- idle_heights -----------------------------------------------------------

def test_idle_heights_are_a_flat_row():
    assert wf.idle_heights(13) == (wf.IDLE_HEIGHT,) * 13


def test_idle_bars_are_lines_not_dots():
    # Tied to the real geometry rather than a taste bound. A round-capped bar
    # drawn no taller than it is wide IS a circle, so the resting row silently
    # becomes dots -- which is what happened when the pill was made smaller and
    # the idle height was left alone.
    max_half = wf.PILL_HEIGHT / 2 - wf.BAR_MARGIN_Y
    drawn_height = wf.IDLE_HEIGHT * max_half * 2
    assert drawn_height >= wf.BAR_WIDTH * 2


def test_idle_bars_stay_well_short_of_full_height():
    # It sits there all day: resting must not be mistakable for quiet speech.
    assert wf.IDLE_HEIGHT < 0.45


# --- transcribing_heights ---------------------------------------------------

def test_transcribing_heights_stay_within_range():
    for tick in range(0, 400, 3):
        heights = wf.transcribing_heights(13, tick)
        assert len(heights) == 13
        assert all(wf.IDLE_HEIGHT - 1e-9 <= h <= 1.0 for h in heights)


def test_transcribing_heights_are_deterministic_in_tick():
    assert wf.transcribing_heights(13, 21) == wf.transcribing_heights(13, 21)


def test_transcribing_sweep_travels():
    # A distinct third state: something must actually move, and move in one
    # direction, or it is indistinguishable from idle.
    peaks = [
        max(range(13), key=lambda i: wf.transcribing_heights(13, t)[i])
        for t in range(0, 40, 4)
    ]
    assert len(set(peaks)) > 1


def test_transcribing_is_never_as_tall_as_loud_speech():
    # It must not look like it is still listening. Speech reaches 1.0; the
    # sweep has to stay obviously short of that.
    loudest_sweep = max(
        max(wf.transcribing_heights(13, t)) for t in range(0, 400)
    )
    assert loudest_sweep < 0.75


def test_transcribing_differs_from_idle():
    assert any(
        wf.transcribing_heights(13, t) != wf.idle_heights(13)
        for t in range(0, 60)
    )


# --- bar_layout -------------------------------------------------------------

def test_bar_layout_returns_one_x_per_bar():
    assert len(wf.bar_layout(150, 13, 4, 5)) == 13


def test_bar_layout_is_centred_in_the_pill():
    xs = wf.bar_layout(150, 13, 4, 5)
    assert (xs[0] + xs[-1]) / 2 == pytest.approx(75.0)


def test_bar_layout_spacing_is_uniform():
    xs = wf.bar_layout(150, 13, 4, 5)
    gaps = [round(b - a, 6) for a, b in zip(xs, xs[1:])]
    assert len(set(gaps)) == 1
    assert gaps[0] == pytest.approx(9.0)


def test_bar_layout_fits_inside_the_pill():
    xs = wf.bar_layout(150, 13, 4, 5)
    assert xs[0] - 4 / 2 >= 0
    assert xs[-1] + 4 / 2 <= 150


def test_bar_layout_handles_a_single_bar():
    assert wf.bar_layout(150, 1, 4, 5) == pytest.approx((75.0,))


# --- ScrollingWave ----------------------------------------------------------

def run(wave, level, frames):
    """Drive `frames` frames at a constant level; return the last heights."""
    heights = None
    for _ in range(frames):
        heights = wave.update(level)
    return heights


def test_wave_starts_as_a_flat_resting_row():
    assert wf.ScrollingWave(15).update(0.0) == pytest.approx(
        wf.idle_heights(15)
    )


def test_wave_always_returns_one_height_per_bar():
    wave = wf.ScrollingWave(15)
    for _ in range(200):
        assert len(wave.update(0.4)) == 15


def test_silence_stays_at_the_resting_row():
    assert run(wf.ScrollingWave(15), 0.0, 300) == pytest.approx(
        wf.idle_heights(15)
    )


def test_new_audio_enters_at_the_left_edge():
    # The defining behaviour. After one shift's worth of loud audio, the
    # left-hand bar must be the one that moved -- not the centre, and not all
    # of them together, which is what the old meter did.
    # Three frames past the first shift, not on it: a bar entering at zero is
    # still under the resting floor for its first ease step, so sampling exactly
    # on the shift boundary shows nothing and proves nothing.
    wave = wf.ScrollingWave(15)
    heights = run(wave, 1.0, wf.SCROLL_FRAMES + 3)
    assert heights[0] > heights[-1]
    assert heights[1:] == pytest.approx(wf.idle_heights(14))


def test_the_wave_travels_rightward():
    # One loud burst, then silence: the peak must walk toward the right edge.
    wave = wf.ScrollingWave(15)
    run(wave, 1.0, wf.SCROLL_FRAMES)

    positions = []
    for _ in range(6):
        heights = run(wave, 0.0, wf.SCROLL_FRAMES)
        positions.append(max(range(15), key=lambda i: heights[i]))

    assert positions == sorted(positions)
    assert positions[-1] > positions[0]


def test_the_oldest_bar_falls_off_the_right_edge():
    wave = wf.ScrollingWave(15)
    run(wave, 1.0, wf.SCROLL_FRAMES)
    # Fifteen more shifts of silence pushes that burst right off the pill.
    assert run(wave, 0.0, wf.SCROLL_FRAMES * 16) == pytest.approx(
        wf.idle_heights(15)
    )


def test_a_captured_bar_keeps_its_height_as_it_travels():
    # A bar's height is a fact about a past moment. If later audio could revise
    # it, the pill would be showing a smear rather than a history.
    wave = wf.ScrollingWave(15)
    run(wave, 0.8, wf.SCROLL_FRAMES)
    settled = run(wave, 0.0, wf.SCROLL_FRAMES * 2)
    peak = max(settled)

    # Now shout: the travelling bar must be unaffected by it.
    after = run(wave, 1.0, wf.SCROLL_FRAMES)
    assert max(after[1:]) == pytest.approx(peak, abs=0.02)


def test_a_new_bar_eases_in_rather_than_popping():
    # "The bar that just entered on the right can ease in from zero."
    wave = wf.ScrollingWave(15)
    run(wave, 1.0, wf.SCROLL_FRAMES)          # land just after a shift
    rising = [wave.update(1.0)[0] for _ in range(wf.SCROLL_FRAMES - 1)]

    assert rising == sorted(rising)           # climbing, never jumping
    assert rising[0] < 1.0                    # did not arrive at full height
    assert rising[-1] > rising[0]


def test_the_newest_bar_never_finishes_easing_before_it_moves_on():
    # A consequence of the design worth pinning down: a bar gets SCROLL_FRAMES
    # frames at the left edge, which at EASE_ALPHA is not enough to arrive. It
    # finishes climbing one position in, and that is what makes the leading edge
    # of the trace look alive rather than stamped.
    wave = wf.ScrollingWave(15)
    assert run(wave, 1.0, wf.SCROLL_FRAMES * 8)[0] < 0.9


def test_a_bar_reaches_full_height_a_couple_of_shifts_in():
    wave = wf.ScrollingWave(15)
    assert max(run(wave, 1.0, wf.SCROLL_FRAMES * 3)) > 0.9


def test_louder_audio_makes_taller_bars():
    peaks = [max(run(wf.ScrollingWave(15), lvl, 120)) for lvl in (0.2, 0.5, 0.9)]
    assert peaks == sorted(peaks)


def test_heights_never_leave_range():
    wave = wf.ScrollingWave(15)
    for frame in range(400):
        heights = wave.update((frame % 17) / 17)
        assert all(wf.IDLE_HEIGHT - 1e-9 <= h <= 1.0 for h in heights)


def test_the_wave_drains_instead_of_resetting():
    # "When I release the hotkey, let the existing wave scroll off to the left
    # rather than resetting instantly." Feeding silence must not blank it.
    wave = wf.ScrollingWave(15)
    run(wave, 1.0, wf.SCROLL_FRAMES * 15)
    loud = max(run(wave, 0.0, 1))

    one_shift_later = max(run(wave, 0.0, wf.SCROLL_FRAMES))
    assert one_shift_later > wf.IDLE_HEIGHT
    assert one_shift_later == pytest.approx(loud, abs=0.15)


def test_a_short_spike_between_shifts_is_not_lost():
    # Peak-hold, not sampling. A 6-frame gap spans about one and a half audio
    # blocks, so a consonant landing off the boundary would vanish entirely.
    wave = wf.ScrollingWave(15, scroll_frames=6)
    wave.update(0.0)
    wave.update(0.0)
    wave.update(1.0)          # the spike, mid-interval
    for _ in range(3):
        wave.update(0.0)
    assert max(run(wave, 0.0, 30)) > wf.IDLE_HEIGHT + 0.2


def test_history_spans_between_one_and_two_seconds_at_60fps():
    # The brief said "the last second or two". This is that, as arithmetic --
    # and it is what one-shift-per-frame would have failed.
    seconds = wf.BAR_COUNT * wf.SCROLL_FRAMES / 60
    assert 1.0 <= seconds <= 2.0


def test_wave_is_deterministic():
    a, b = wf.ScrollingWave(15), wf.ScrollingWave(15)
    for frame in range(120):
        assert a.update(frame / 120) == b.update(frame / 120)


def test_wave_survives_a_nan_level():
    wave = wf.ScrollingWave(15)
    wave.update(float("nan"))
    assert all(math.isfinite(h) for h in run(wave, 0.5, 60))


def test_wave_clamps_a_level_outside_the_unit_range():
    assert all(h <= 1.0 for h in run(wf.ScrollingWave(15), 4.0, 120))
