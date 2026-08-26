"""The pill/panel grow, and what the control strip says in each state.

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


# --- the grow ---------------------------------------------------------------

def test_idle_rests_as_the_pill():
    frame = settle(an_indicator())
    assert frame.open == pytest.approx(0.0, abs=1e-3)
    assert frame.width == pytest.approx(wf.PILL_WIDTH, abs=0.5)
    assert frame.height == pytest.approx(wf.PILL_HEIGHT, abs=0.5)
    assert frame.radius == pytest.approx(panel.PILL_RADIUS, abs=0.1)


def test_recording_grows_to_the_panel():
    indicator = an_indicator()
    indicator.show_recording()
    frame = settle(indicator)
    assert frame.open == pytest.approx(1.0, abs=1e-3)
    assert frame.width == pytest.approx(panel.PANEL_WIDTH, abs=0.5)
    assert frame.height == pytest.approx(panel.PANEL_HEIGHT, abs=0.5)
    assert frame.radius == pytest.approx(panel.PANEL_RADIUS, abs=0.1)


def test_width_height_and_radius_never_move_independently():
    """Gate 3b. One scalar drives all three, so a frame can never be a wide
    pill or a tall lozenge."""
    indicator = an_indicator()
    indicator.show_recording()
    for _ in range(40):
        frame = indicator.next_frame()
        assert frame.width == pytest.approx(
            panel.lerp(wf.PILL_WIDTH, panel.PANEL_WIDTH, frame.open), abs=0.6
        )
        assert frame.height == pytest.approx(
            panel.lerp(wf.PILL_HEIGHT, panel.PANEL_HEIGHT, frame.open), abs=0.6
        )
        assert frame.radius == pytest.approx(
            panel.lerp(panel.PILL_RADIUS, panel.PANEL_RADIUS, frame.open),
            abs=0.2,
        )


def test_the_grow_is_a_fade_not_a_cut():
    indicator = an_indicator()
    indicator.show_recording()
    first = indicator.next_frame()
    assert 0.0 < first.open < 1.0


def test_transcribing_stays_open():
    indicator = an_indicator()
    indicator.show_recording()
    settle(indicator)
    indicator.show_processing()
    frame = settle(indicator)
    assert frame.open == pytest.approx(1.0, abs=1e-3)


def test_hiding_shrinks_back_to_the_pill():
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


# --- the trace across the grow -----------------------------------------------

def test_the_buffer_is_the_long_one():
    indicator = an_indicator()
    settle(indicator)
    indicator.show_recording()
    frame = settle(indicator)
    assert len(frame.heights) == wf.BUFFER_BARS


def test_the_pill_windows_the_newest_bars():
    """Gate 3e. Not a separate buffer -- a window onto the same history."""
    frame = settle(an_indicator())
    assert len(frame.heights) == wf.BAR_COUNT


def test_the_grow_reveals_history_rather_than_clearing_it():
    indicator = an_indicator()
    indicator.show_recording()
    counts = [len(indicator.next_frame().heights) for _ in range(60)]
    assert counts == sorted(counts)
    assert counts[0] >= wf.BAR_COUNT
    assert counts[-1] <= wf.BUFFER_BARS


# --- what the strip says ------------------------------------------------------

def test_recording_shows_stop_and_cancel():
    """Gate 2b."""
    indicator = an_indicator(hotkey="F8")
    indicator.show_recording()
    frame = settle(indicator)
    assert [item.id for item in frame.strip] == ["stop", "cancel"]


def test_the_stop_cap_is_the_configured_hotkey():
    """Gate 2e."""
    indicator = an_indicator(hotkey="ctrl+alt+d")
    indicator.show_recording()
    frame = settle(indicator)
    stop = next(item for item in frame.strip if item.id == "stop")
    assert stop.cap == "ctrl+alt+d"
    assert stop.label == "Stop"


def test_cancel_is_bound_to_esc():
    indicator = an_indicator()
    indicator.show_recording()
    frame = settle(indicator)
    cancel = next(item for item in frame.strip if item.id == "cancel")
    assert cancel.cap == "Esc"


def test_no_cancel_control_when_esc_is_itself_the_hotkey():
    """`legend_for` already enforced this and must not lose it: _handle_down
    gives the hotkey precedence, so a Cancel here would be a lie."""
    indicator = an_indicator(hotkey="esc", cancel_key="")
    indicator.show_recording()
    frame = settle(indicator)
    assert [item.id for item in frame.strip] == ["stop"]


# --- a runtime hotkey change (gate 2e) ----------------------------------------
#
# The tray's "Change hotkey" menu item can swap the hotkey while the app is
# running. There was no setter, so the Stop cap kept showing the old key until
# the app restarted -- the bar lying about the one thing it is on screen to say.


def test_set_keys_updates_the_stop_cap_on_the_next_frame():
    """Gate 2e. After a runtime hotkey change, the very next frame's Stop cap
    must show the new key, not the one the Indicator was constructed with."""
    indicator = an_indicator(hotkey="F8")
    indicator.show_recording()
    settle(indicator)

    indicator.set_keys("ctrl+alt+d", "Esc")

    frame = indicator.next_frame()
    stop = next(item for item in frame.strip if item.id == "stop")
    assert stop.cap == "ctrl+alt+d"


def test_changing_hotkey_to_esc_removes_the_cancel_control():
    """CRITICAL: `_handle_down` gives the hotkey precedence over Cancel, so a
    Cancel control that cannot fire would be a lie -- exactly the invariant
    `test_no_cancel_control_when_esc_is_itself_the_hotkey` enforces at
    construction time. The runtime setter must apply the identical rule."""
    indicator = an_indicator(hotkey="F8", cancel_key="Esc")
    indicator.show_recording()
    settle(indicator)

    indicator.set_keys("esc", "")

    frame = indicator.next_frame()
    assert [item.id for item in frame.strip] == ["stop"]


def test_changing_hotkey_away_from_esc_restores_the_cancel_control():
    indicator = an_indicator(hotkey="esc", cancel_key="")
    indicator.show_recording()
    settle(indicator)

    indicator.set_keys("F9", "Esc")

    frame = indicator.next_frame()
    assert [item.id for item in frame.strip] == ["stop", "cancel"]
    cancel = next(item for item in frame.strip if item.id == "cancel")
    assert cancel.cap == "Esc"


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
