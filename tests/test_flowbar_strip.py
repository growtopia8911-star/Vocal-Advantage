"""The pill and panel shapes, and what the control strip says in each state.

Until 2026-08-25 the two shapes were also joined by an eased grow, and this
file's tests were largely about it -- see `test_open_snaps_to_the_panel_on_
the_very_first_frame` for what changed and why.

Supersedes tests/test_flowbar_legend.py: the legend was a line of prose beside
the trace, and it is now a laid-out strip of controls.
"""

import pytest

from vocal_advantage import flowbar, panel
from vocal_advantage import waveform as wf


def an_indicator(**kwargs):
    kwargs.setdefault("hotkey", "F8")
    kwargs.setdefault("cancel_key", "Esc")
    return flowbar.Indicator(**kwargs)


def settle(indicator, frames=200):
    """Run the ease to completion and return the final frame."""
    frame = None
    for _ in range(frames):
        frame = indicator.next_frame()
    return frame


# --- the two shapes ----------------------------------------------------------
#
# Was "the grow": before 2026-08-25 the panel eased open and shut, and this
# section's name and several of its test names said so. The grow itself is
# gone -- see `test_open_snaps_to_the_panel_on_the_very_first_frame` below --
# but the shapes it eased between are unchanged, so most of these still hold.

def test_idle_rests_as_the_pill():
    frame = settle(an_indicator())
    assert frame.open == pytest.approx(0.0, abs=1e-3)
    assert frame.width == pytest.approx(wf.PILL_WIDTH, abs=0.5)
    assert frame.height == pytest.approx(wf.PILL_HEIGHT, abs=0.5)
    assert frame.radius == pytest.approx(panel.PILL_RADIUS, abs=0.1)






def test_hiding_returns_to_the_pill():
    indicator = an_indicator()
    indicator.show_recording()
    settle(indicator)
    indicator.hide()
    frame = settle(indicator)
    assert frame.open == pytest.approx(0.0, abs=1e-3)


def test_a_message_never_opens_the_panel():
    """Gate 3f. A panel is for dictating; 'could not paste' is not."""
    indicator = an_indicator()
    indicator.flash("could not paste - press Cmd+V")
    for _ in range(30):
        frame = indicator.next_frame()
        assert frame.open == pytest.approx(0.0, abs=1e-3)
    assert frame.width > wf.PILL_WIDTH


# --- the trace across both shapes ---------------------------------------------


def test_the_pill_windows_the_newest_bars():
    """Gate 3e. Not a separate buffer -- a window onto the same history."""
    frame = settle(an_indicator())
    assert len(frame.heights) == wf.BAR_COUNT










def test_transcribing_shows_no_controls():
    """Gate 2d, and the reasoning LEGEND_STATES already carried: once the
    model has the audio, no key stops it and none bins the result, so
    anything shown here would be false."""
    indicator = an_indicator()
    indicator.show_processing()
    frame = settle(indicator)
    assert frame.strip == ()


def test_idle_shows_no_controls():
    assert settle(an_indicator()).strip == ()


def test_a_message_shows_no_controls():
    indicator = an_indicator()
    indicator.flash("could not paste")
    assert indicator.next_frame().strip == ()


# --- hover --------------------------------------------------------------------

def test_hover_defaults_to_nothing():
    assert settle(an_indicator()).hover == ""


def test_hover_is_carried_through_to_the_frame():
    indicator = an_indicator()
    indicator.show_recording()
    frame = indicator.next_frame(hover="cancel")
    assert frame.hover == "cancel"


def test_the_legend_is_gone():
    """It was prose beside the trace; it is a laid-out strip now."""
    assert not hasattr(flowbar.Frame, "legend")
    assert not hasattr(flowbar, "legend_width")


# --- the compact indicator (replaced the two-band panel, 2026-08-25) --------

def test_recording_shows_the_compact_indicator():
    indicator = an_indicator()
    indicator.show_recording()
    frame = settle(indicator)
    assert frame.width == panel.COMPACT_WIDTH
    assert frame.height == float(wf.PILL_HEIGHT)
    assert frame.visible is True


def test_the_compact_indicator_is_full_size_on_the_first_frame():
    indicator = an_indicator()
    indicator.show_recording()
    assert indicator.next_frame().width == panel.COMPACT_WIDTH


def test_recording_and_transcribing_differ_only_by_the_dot():
    """The strip's labels are gone, so colour is what tells them apart."""
    rec, tra = an_indicator(), an_indicator()
    rec.show_recording()
    tra.show_processing()
    a, b = settle(rec), settle(tra)
    assert (a.width, a.height) == (b.width, b.height)
    assert a.dot == panel.DOT_RECORDING_RGB
    assert b.dot == panel.DOT_TRANSCRIBING_RGB


def test_idle_and_message_have_no_dot():
    """A dot that never changes is decoration, so it is only drawn when it
    is reporting something."""
    idle = an_indicator()
    assert settle(idle).dot is None
    msg = an_indicator()
    msg.flash("could not paste")
    assert msg.next_frame().dot is None


def test_the_strip_is_always_empty_now():
    """There is no strip at 30pt tall. The keys still work; the on-screen
    reminder of them does not exist. See `Indicator._strip`."""
    indicator = an_indicator(hotkey="F8", cancel_key="Esc")
    indicator.show_recording()
    assert settle(indicator).strip == ()


def test_set_keys_is_still_accepted_even_though_nothing_draws_them():
    """The hotkey is still tracked, so a taller shape could show it again
    without rebuilding the plumbing."""
    indicator = an_indicator(hotkey="F8", cancel_key="Esc")
    indicator.set_keys("ctrl+alt+d", "Esc")
    indicator.show_recording()
    assert settle(indicator).strip == ()


def test_always_visible_keeps_the_bar_at_idle():
    indicator = an_indicator(always_visible=True)
    assert settle(indicator).visible is True


def test_always_visible_is_off_by_default():
    assert settle(an_indicator()).visible is False


def test_hide_for_an_hour_beats_an_active_dictation():
    """Hiding that still showed a bar while you dictated would not be hiding."""
    indicator = an_indicator()
    indicator.set_suppressed(True)
    indicator.show_recording()
    assert indicator.next_frame().visible is False


def test_move_bar_beats_hide_for_an_hour():
    """You cannot drag something that is not drawn."""
    indicator = an_indicator()
    indicator.set_suppressed(True)
    indicator.set_movable(True)
    assert settle(indicator).visible is True


def test_unhiding_brings_the_bar_back():
    indicator = an_indicator()
    indicator.set_suppressed(True)
    indicator.show_recording()
    assert indicator.next_frame().visible is False
    indicator.set_suppressed(False)
    assert indicator.next_frame().visible is True
