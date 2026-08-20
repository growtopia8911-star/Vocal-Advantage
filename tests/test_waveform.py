"""The waveform maths: audio level in, bar heights out.

Everything here is pure. The renderer turns these numbers into pixels and the
NSPanel/layered window put them on screen, but nothing in this file touches a
screen -- which is the whole reason the motion can be tested at all.

Heights are normalised: 0.0 is a flat line, 1.0 is the tallest a bar may draw.
The renderer scales them to its own half-height, so nothing here knows about
pixels except `bar_layout`, which is told the width.
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


# --- Envelope ---------------------------------------------------------------

def test_envelope_starts_at_zero():
    assert wf.Envelope().value == 0.0


def test_envelope_rises_faster_than_it_falls():
    # Fast attack, slow release. This is what stops the bars flickering on the
    # gaps between words while still catching the start of a syllable.
    rising = wf.Envelope()
    rising.update(1.0)

    falling = wf.Envelope()
    falling._value = 1.0
    falling.update(0.0)

    assert rising.value > (1.0 - falling.value)


def test_envelope_never_overshoots_its_target():
    env = wf.Envelope()
    for _ in range(200):
        env.update(1.0)
        assert env.value <= 1.0


def test_envelope_converges_on_a_held_level():
    env = wf.Envelope()
    for _ in range(500):
        env.update(0.42)
    assert env.value == pytest.approx(0.42, abs=1e-3)


def test_envelope_recovers_from_a_nan_level():
    # Belt and braces with block_rms's nan guard: one nan that got through
    # would otherwise stick to the envelope for the life of the process.
    env = wf.Envelope()
    env.update(float("nan"))
    env.update(0.5)
    assert not math.isnan(env.value)


# --- centre_weights ---------------------------------------------------------

def test_centre_weights_are_symmetric():
    weights = wf.centre_weights(13)
    assert weights == tuple(reversed(weights))


def test_centre_weights_peak_at_the_centre():
    weights = wf.centre_weights(13)
    assert weights[6] == pytest.approx(1.0)
    assert max(weights) == pytest.approx(1.0)


def test_centre_weights_reach_edge_value_at_the_ends():
    weights = wf.centre_weights(13, edge=0.45)
    assert weights[0] == pytest.approx(0.45)
    assert weights[-1] == pytest.approx(0.45)


def test_centre_weights_decrease_outward_from_the_centre():
    # "Bars nearer the centre should react a touch more than those at the
    # edges." A flat profile would read as a row of independent meters.
    weights = wf.centre_weights(13)
    left_half = weights[:7]
    assert list(left_half) == sorted(left_half)


def test_centre_weights_handles_a_single_bar():
    assert wf.centre_weights(1) == (1.0,)


def test_centre_weights_handles_an_even_count():
    weights = wf.centre_weights(12)
    assert len(weights) == 12
    assert weights == tuple(reversed(weights))


# --- bar_targets ------------------------------------------------------------

def test_bar_targets_at_silence_are_the_idle_row():
    # Silence must look exactly like idle, whatever the tick. Without this the
    # pill twitches during the pauses between sentences.
    for tick in (0, 7, 250):
        assert wf.bar_targets(0.0, 13, tick) == pytest.approx(
            wf.idle_heights(13)
        )


def test_bar_targets_stay_within_range():
    for level in (0.0, 0.25, 0.5, 0.99, 1.0):
        for tick in range(0, 120, 11):
            targets = wf.bar_targets(level, 13, tick)
            assert all(wf.IDLE_HEIGHT - 1e-9 <= h <= 1.0 for h in targets)


def test_bar_targets_grow_with_level():
    quiet = wf.bar_targets(0.2, 13, tick=0, wobble=0.0)
    loud = wf.bar_targets(0.9, 13, tick=0, wobble=0.0)
    assert all(l > q for q, l in zip(quiet, loud))


def test_bar_targets_centre_exceeds_edges():
    targets = wf.bar_targets(0.8, 13, tick=0, wobble=0.0)
    assert targets[6] > targets[0]
    assert targets[6] > targets[-1]


def test_bar_targets_wobble_breaks_left_right_symmetry():
    # Mirroring is vertical (each bar grows up and down equally); left-to-right
    # the row must NOT be a perfect arch or it reads as a graphic, not a voice.
    targets = wf.bar_targets(0.8, 13, tick=5)
    assert targets != tuple(reversed(targets))


def test_bar_targets_are_deterministic_in_tick():
    assert wf.bar_targets(0.6, 13, tick=9) == wf.bar_targets(0.6, 13, tick=9)


def test_bar_targets_move_between_consecutive_ticks():
    assert wf.bar_targets(0.6, 13, tick=9) != wf.bar_targets(0.6, 13, tick=10)


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
    # It must not look like it is still listening.
    loudest_sweep = max(
        max(wf.transcribing_heights(13, t)) for t in range(0, 400)
    )
    assert loudest_sweep < max(wf.bar_targets(1.0, 13, tick=0, wobble=0.0))


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
