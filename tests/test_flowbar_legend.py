"""The legend: the line of text that says how to stop what you started.

Gate 1 of `docs/plans/2026-08-25-interface-design.md`. Today the hotkey is
knowable only from the README -- the bar shows a waveform and nothing else, so
a recording in progress offers no clue how to end it or throw it away.

The researched app solves this with a permanent control strip. That does not
port literally: `PILL_WIDTH` is 78 points, so a strip under the pill would be
78 points wide and fit nothing. The pill already knows how to widen -- that is
what `message_width` does for a flash -- so the legend widens it the same way,
and only while it is relevant. Idle stays the small quiet lozenge it is,
because that is the state it sits in over your work all day.

Driven headlessly through `next_frame()`, exactly as `test_flowbar.py` does.
No window is created here.
"""

from __future__ import annotations

import pytest

from vocal_advantage import main as va_main
from vocal_advantage import waveform as wf
from vocal_advantage.flowbar import Indicator
from vocal_advantage.hotkey_spec import parse_hotkey

LEGEND = "⌥ Right Option · esc cancels"


def drain(indicator, frames):
    """Advance the render loop `frames` times and return the last Frame."""
    frame = None
    for _ in range(frames):
        frame = indicator.next_frame()
    return frame


def settle(indicator, frames=90):
    """Long enough for the width easing to arrive, not just set off."""
    return drain(indicator, frames)


# --- the legend appears exactly where it earns its space --------------------

def test_recording_shows_the_legend():
    """Gate 1b/1c: while recording, the bar says how to stop and how to cancel."""
    indicator = Indicator(legend=LEGEND)
    indicator.show_recording()
    assert drain(indicator, 2).legend == LEGEND


def test_idle_shows_no_legend():
    """Gate 1e: the resting pill is unchanged. It is what you look at all day."""
    indicator = Indicator(legend=LEGEND)
    assert drain(indicator, 2).legend == ""


def test_transcribing_drops_the_legend():
    """Nothing it could say would be true.

    Esc cancels a *recording* (gate 3); once the model has the audio there is
    no key that stops it and none that bins the result. Leaving the legend up
    through transcribing would advertise both. The pill narrowing back is the
    honest signal that the moment for changing your mind has passed.
    """
    indicator = Indicator(legend=LEGEND)
    indicator.show_processing()
    assert drain(indicator, 2).legend == ""


def test_returning_to_idle_takes_the_legend_away():
    indicator = Indicator(legend=LEGEND)
    indicator.show_recording()
    drain(indicator, 5)
    indicator.hide()
    assert drain(indicator, 2).legend == ""


# --- the pill widens to hold it --------------------------------------------

def test_the_pill_widens_to_fit_the_legend():
    """A 78pt pill cannot hold a sentence, so recording makes room for one."""
    indicator = Indicator(legend=LEGEND)
    resting = settle(indicator).width

    indicator.show_recording()
    recording = settle(indicator).width

    assert recording > resting
    assert recording >= wf.PILL_WIDTH + len(LEGEND) * 2


def test_the_pill_returns_to_its_resting_width():
    """Approximate, not exact: the easing converges without ever arriving, and
    the renderers already ignore a change smaller than half a point."""
    indicator = Indicator(legend=LEGEND)
    resting = settle(indicator).width

    indicator.show_recording()
    settle(indicator)
    indicator.hide()

    assert settle(indicator).width == pytest.approx(resting, abs=0.5)


def test_the_width_glides_rather_than_snapping():
    """Same easing as every other transition -- a cut would read as a glitch."""
    indicator = Indicator(legend=LEGEND)
    settle(indicator)
    indicator.show_recording()

    first = drain(indicator, 1).width
    eventual = settle(indicator).width

    assert first < eventual


# --- it never fights the other things the pill has to say -------------------

def test_a_flash_message_wins_over_the_legend():
    """"could not paste" is urgent; the hotkey reminder is not."""
    indicator = Indicator(legend=LEGEND)
    indicator.show_recording()
    drain(indicator, 3)
    indicator.flash("nothing heard")

    frame = drain(indicator, 2)
    assert frame.text == "nothing heard"
    assert frame.legend == ""


# --- an app with nothing to say says nothing --------------------------------

# --- composing it from the hotkey -------------------------------------------
#
# `flowbar.py` deliberately knows nothing about hotkeys, so the wording is
# built in main.py, where the spec already lives, and handed in as a string.

def test_the_legend_names_the_hotkey_and_esc():
    """Gate 1b/1c: both actions, each with the key that performs it.

    "Right Alt" rather than "Right Option" even on a Mac: `hotkey_spec` accepts
    "right option" as input but has no darwin display override for it, unlike
    the one it has for Cmd. Asserted as-is here rather than quietly changed --
    the display table is a tested contract, and the legend putting that name
    permanently on screen is an argument for revisiting it, not licence to.
    """
    legend = va_main.legend_for(parse_hotkey("right alt"))
    assert "Right Alt" in legend
    assert "esc" in legend.lower()


def test_the_legend_uses_the_hotkeys_own_display_name():
    """Whatever the user actually bound, spelled the way the app spells it."""
    assert "F8" in va_main.legend_for(parse_hotkey("f8"))


def test_a_combo_hotkey_is_named_in_full():
    legend = va_main.legend_for(parse_hotkey("ctrl+alt+space"))
    for part in ("Ctrl", "Alt", "Space"):
        assert part in legend


def test_a_hotkey_of_esc_does_not_claim_that_esc_cancels():
    """Esc is the dictation key here, so it cannot also be the cancel key --
    the controller gives the hotkey precedence, and the legend must agree."""
    legend = va_main.legend_for(parse_hotkey("esc"))
    assert "cancel" not in legend.lower()


@pytest.mark.parametrize(
    "platform_name,expected",
    [
        ("darwin", True),
        # No legend on Windows, and not an oversight. `flowbar_win.render_frame`
        # draws no text at all -- there is no font in that file and never has
        # been, so `frame.text` is dropped and flash messages have never
        # appeared on the Windows bar either. `_mirrored()` forces exact
        # vertical symmetry, which would destroy glyphs regardless. Handing it a
        # legend would widen the pill and stretch the trace to show nothing.
        ("win32", False),
    ],
)
def test_each_launcher_hands_the_bar_a_legend(
    platform_name, expected, tmp_path, monkeypatch
):
    """Neither launcher is covered end to end -- every test that drives one
    stops at the model load -- so the wiring has to be checked where it happens.

    Same shape as the paster wiring test, and for the same reason: a legend
    that silently never reaches the Indicator looks exactly like a bar that
    has nothing to say.
    """
    # Imported before sys.platform is faked, and deliberately. `flowbar_win`
    # builds `ctypes.WinDLL("user32")` at module scope behind a platform guard,
    # so importing it *while* pretending to be Windows runs that on a Mac and
    # dies. Getting it into sys.modules first makes the guard a no-op. Without
    # this the test still passes in a full run -- test_flowbar_win.py happens
    # to import it earlier -- which is exactly the kind of order dependence
    # that only shows up when someone runs one file.
    from vocal_advantage import flowbar as fb
    from vocal_advantage import flowbar_win, hotkey_win, paste_win  # noqa: F401

    monkeypatch.setattr(va_main.sys, "platform", platform_name)

    class StopAfterWiring(Exception):
        pass

    built: list = []

    def spy(*args, **kwargs):
        built.append(kwargs.get("legend", ""))
        raise StopAfterWiring

    monkeypatch.setattr(fb, "Indicator", spy)

    launcher = (
        va_main._run_app_mac if platform_name == "darwin"
        else va_main._run_app_windows
    )
    with pytest.raises(StopAfterWiring):
        launcher(tmp_path / "config.json")

    assert built, "no Indicator was constructed"
    assert bool(built[0]) is expected, built[0]
    if expected:
        assert "cancels" in built[0], built[0]


def test_no_legend_configured_leaves_the_pill_exactly_as_it_was():
    """The legend is additive. Construct without one and nothing changes."""
    indicator = Indicator()
    resting = settle(indicator).width

    indicator.show_recording()
    frame = settle(indicator)

    assert frame.legend == ""
    assert frame.width == resting
