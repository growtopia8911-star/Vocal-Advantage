"""Unit tests for the per-stage stopwatch (spec item 11).

Pure Python with an injected clock, so these run at full speed and assert on
exact milliseconds rather than "roughly".
"""

from __future__ import annotations

import pytest

from vocal_advantage.timings import Timings


class FakeClock:
    """A clock that only moves when a test tells it to."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# --- 11a: keypress to first audio -------------------------------------------


def test_keypress_to_first_audio_is_measured_from_the_press(clock):
    t = Timings(clock=clock)
    t.start()
    clock.advance(0.042)
    t.first_audio()
    assert t.first_audio_ms == pytest.approx(42.0)


def test_first_audio_only_records_the_first_call(clock):
    """Blocks keep arriving; only the first one ends this stage."""
    t = Timings(clock=clock)
    t.start()
    clock.advance(0.01)
    t.first_audio()
    clock.advance(5.0)
    t.first_audio()
    assert t.first_audio_ms == pytest.approx(10.0)


# --- 11b/11c: per-chunk and final chunk -------------------------------------


def test_each_chunk_is_timed_separately(clock):
    t = Timings(clock=clock)
    t.start()
    with t.chunk():
        clock.advance(0.120)
    with t.chunk():
        clock.advance(0.080)
    assert t.chunk_ms == [pytest.approx(120.0), pytest.approx(80.0)]


def test_the_final_chunk_is_kept_apart_from_the_rolling_ones(clock):
    """11c: the tail transcribed on stop is its own number, not chunk N+1."""
    t = Timings(clock=clock)
    t.start()
    with t.chunk():
        clock.advance(0.100)
    with t.final_chunk():
        clock.advance(0.250)
    assert t.chunk_ms == [pytest.approx(100.0)]
    assert t.final_chunk_ms == pytest.approx(250.0)


def test_a_stage_that_raises_is_still_timed(clock):
    """A failed chunk must not lose the timing block for the whole dictation."""
    t = Timings(clock=clock)
    t.start()
    with pytest.raises(ValueError):
        with t.chunk():
            clock.advance(0.030)
            raise ValueError("model fell over")
    assert t.chunk_ms == [pytest.approx(30.0)]


# --- 11d/11e: cleanup and insertion -----------------------------------------


def test_cleanup_and_insertion_are_reported(clock):
    t = Timings(clock=clock)
    t.start()
    with t.cleanup():
        clock.advance(0.400)
    with t.insertion():
        clock.advance(0.220)
    assert t.cleanup_ms == pytest.approx(400.0)
    assert t.insertion_ms == pytest.approx(220.0)


# --- 11f: the printed block -------------------------------------------------


def test_report_names_every_stage(clock):
    t = Timings(clock=clock)
    t.start()
    clock.advance(0.005)
    t.first_audio()
    with t.chunk():
        clock.advance(0.100)
    with t.chunk():
        clock.advance(0.110)
    with t.final_chunk():
        clock.advance(0.090)
    with t.cleanup():
        clock.advance(0.300)
    with t.insertion():
        clock.advance(0.200)

    report = t.report()
    for expected in ("keypress", "first audio", "chunk", "final", "cleanup", "insert"):
        assert expected in report.lower(), f"{expected!r} missing from:\n{report}"


def test_report_shows_each_chunk_individually(clock):
    """11b: two chunks means two numbers, not an average."""
    t = Timings(clock=clock)
    t.start()
    with t.chunk():
        clock.advance(0.100)
    with t.chunk():
        clock.advance(0.250)
    report = t.report()
    assert "100" in report
    assert "250" in report


def test_report_works_when_nothing_was_heard(clock):
    """11f: an empty dictation still prints a block rather than crashing."""
    t = Timings(clock=clock)
    t.start()
    clock.advance(0.010)
    t.first_audio()
    report = t.report()
    assert isinstance(report, str) and report.strip()


def test_report_carries_a_total(clock):
    t = Timings(clock=clock)
    t.start()
    clock.advance(1.234)
    assert "1234" in t.report()
