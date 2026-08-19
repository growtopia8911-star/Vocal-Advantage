"""Unit tests for microphone capture.

THE SEAM: vocal_advantage/recorder.py binds the sounddevice library once, at
import time, to the module-level name ``sd``.  Every test here monkeypatches
that single name with FakeSounddevice below, so all of the recorder's real
logic runs while nothing touches PortAudio or a microphone.  These tests pass
on a machine with no sound card at all.  The one thing that genuinely needs a
microphone is the manual smoke test, tools/smoke_recorder.py.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from vocal_advantage import recorder as recorder_module
from vocal_advantage.recorder import SAMPLE_RATE, Recorder, RecorderError


# ---------------------------------------------------------------------------
# The fake sounddevice module
# ---------------------------------------------------------------------------


class FakePortAudioError(Exception):
    """Stands in for sounddevice.PortAudioError."""


class FakeWasapiSettings:
    def __init__(self, exclusive: bool = False, auto_convert: bool = False) -> None:
        self.exclusive = exclusive
        self.auto_convert = auto_convert


class FakeStream:
    def __init__(self, module: "FakeSounddevice", **kwargs) -> None:
        self._module = module
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        if self._module.fail_stream_start:
            raise FakePortAudioError("Error starting stream [PaErrorCode -9988]")
        if not self._module.device_present:
            raise FakePortAudioError("Error starting stream: Device unavailable")
        self.started = True

    def stop(self) -> None:
        if not self._module.device_present:
            raise FakePortAudioError("Error stopping stream: Device unavailable")
        self.stopped = True

    def close(self) -> None:
        if not self._module.device_present:
            raise FakePortAudioError("Error closing stream: Device unavailable")
        self.closed = True

    def feed(self, samples) -> None:
        """Deliver one block of audio the way PortAudio's thread would."""
        block = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        self.kwargs["callback"](block, len(block), None, 0)


class FakeSounddevice:
    """Only the four names recorder.py actually uses.

    recorder.py never names FakePortAudioError - it catches bare Exception,
    because a real PortAudio failure surfaces as PortAudioError, OSError or
    ValueError depending on which layer failed.  The attribute is here so the
    fake raises something recognisable when a test reads the traceback.
    """

    PortAudioError = FakePortAudioError
    WasapiSettings = FakeWasapiSettings

    def __init__(self) -> None:
        self.streams: list[FakeStream] = []
        self.device_present = True
        self.reject_extra_settings = False  # "the host API is not WASAPI"
        self.fail_stream_start = False
        self.reinit_recovers = True
        self.initialize_calls = 0
        self.terminate_calls = 0

    def InputStream(self, **kwargs) -> FakeStream:  # noqa: N802 - mirrors sounddevice
        if not self.device_present:
            raise FakePortAudioError(
                "Error opening InputStream: Device unavailable [PaErrorCode -9985]"
            )
        if self.reject_extra_settings and kwargs.get("extra_settings") is not None:
            raise FakePortAudioError(
                "Error opening InputStream: Incompatible host API specific "
                "stream info [PaErrorCode -9984]"
            )
        stream = FakeStream(self, **kwargs)
        self.streams.append(stream)
        return stream

    def _initialize(self) -> None:
        self.initialize_calls += 1
        if self.reinit_recovers:
            self.device_present = True

    def _terminate(self) -> None:
        self.terminate_calls += 1

    # -- helpers for the tests, not part of the sounddevice API --
    @property
    def stream(self) -> FakeStream:
        assert self.streams, "no stream has been opened"
        return self.streams[-1]

    def unplug(self) -> None:
        self.device_present = False


@pytest.fixture
def fake_sd(monkeypatch):
    fake = FakeSounddevice()
    monkeypatch.setattr(recorder_module, "sd", fake)
    return fake


# ---------------------------------------------------------------------------
# Normal capture
# ---------------------------------------------------------------------------


def test_sample_rate_is_16k():
    # Whisper resamples anything else to 16kHz internally; handing it 16kHz
    # already is what lets us skip ffmpeg entirely (SPEC, "Audio").
    assert SAMPLE_RATE == 16000


def test_constructing_a_recorder_opens_no_stream(fake_sd):
    # Idle must leave the Windows mic-in-use indicator OFF (acceptance list).
    rec = Recorder()
    assert fake_sd.streams == []
    assert rec.is_recording is False


def test_start_opens_one_stream_with_the_spec_audio_settings(fake_sd):
    rec = Recorder()
    rec.start()

    assert len(fake_sd.streams) == 1
    kwargs = fake_sd.stream.kwargs
    assert kwargs["samplerate"] == 16000
    assert kwargs["channels"] == 1
    assert kwargs["dtype"] == "float32"
    assert kwargs["blocksize"] == 1024
    assert callable(kwargs["callback"])
    assert fake_sd.stream.started is True
    assert rec.is_recording is True


def test_start_asks_for_wasapi_auto_convert(fake_sd):
    # auto_convert lets the driver resample a 44.1/48kHz mic down to our 16kHz
    # instead of PortAudio refusing the rate outright (SPEC, "Audio").
    rec = Recorder()
    rec.start()

    extra = fake_sd.stream.kwargs["extra_settings"]
    assert isinstance(extra, FakeWasapiSettings)
    assert extra.auto_convert is True


def test_custom_samplerate_is_passed_through(fake_sd):
    Recorder(samplerate=48000).start()
    assert fake_sd.stream.kwargs["samplerate"] == 48000


def test_stop_returns_the_captured_audio_as_flat_float32(fake_sd):
    rec = Recorder()
    rec.start()
    fake_sd.stream.feed([0.1, 0.2])
    fake_sd.stream.feed([0.3, 0.4, 0.5])

    audio = rec.stop()

    assert audio.dtype == np.float32
    assert audio.ndim == 1  # Whisper wants mono samples, not (frames, channels)
    np.testing.assert_allclose(audio, [0.1, 0.2, 0.3, 0.4, 0.5], rtol=1e-6)


def test_stop_closes_the_stream_so_the_mic_indicator_goes_out(fake_sd):
    rec = Recorder()
    rec.start()
    stream = fake_sd.stream

    rec.stop()

    assert stream.closed is True
    assert rec.is_recording is False


@pytest.mark.parametrize(
    "scenario", ["never started", "started but silent", "already stopped"]
)
def test_stop_returns_an_empty_float32_array_when_nothing_was_captured(
    fake_sd, scenario
):
    rec = Recorder()
    if scenario == "started but silent":
        rec.start()
    elif scenario == "already stopped":
        rec.start()
        fake_sd.stream.feed([0.1, 0.2])
        rec.stop()

    audio = rec.stop()

    assert audio.size == 0
    assert audio.dtype == np.float32
    assert rec.is_recording is False


def test_start_twice_does_not_open_a_second_stream(fake_sd):
    # A repeated key-down (OS autorepeat) must not open a second mic stream or
    # throw away what has been recorded so far.
    rec = Recorder()
    rec.start()
    fake_sd.stream.feed([0.1])
    rec.start()
    fake_sd.stream.feed([0.2])

    audio = rec.stop()

    assert len(fake_sd.streams) == 1
    np.testing.assert_allclose(audio, [0.1, 0.2], rtol=1e-6)


def test_a_new_recording_does_not_inherit_the_previous_one(fake_sd):
    rec = Recorder()
    rec.start()
    fake_sd.stream.feed([0.1, 0.2])
    rec.stop()

    rec.start()
    fake_sd.stream.feed([0.9])

    np.testing.assert_allclose(rec.stop(), [0.9], rtol=1e-6)


def test_the_callback_copies_portaudios_buffer(fake_sd):
    # PortAudio hands the callback a view over one buffer that it refills for
    # the next block. Keeping the view would make the whole recording come out
    # as the final block repeated.
    rec = Recorder()
    rec.start()
    callback = fake_sd.stream.kwargs["callback"]

    buffer = np.zeros((2, 1), dtype=np.float32)
    buffer[:, 0] = [0.1, 0.2]
    callback(buffer, 2, None, 0)
    buffer[:, 0] = [0.8, 0.9]
    callback(buffer, 2, None, 0)

    np.testing.assert_allclose(rec.stop(), [0.1, 0.2, 0.8, 0.9], rtol=1e-6)


def test_blocks_arriving_from_several_threads_are_all_kept(fake_sd):
    # The audio callback runs on PortAudio's thread while the controller thread
    # calls stop(); the lock is what stops a block going missing.
    rec = Recorder()
    rec.start()
    callback = fake_sd.stream.kwargs["callback"]
    gate = threading.Barrier(4)

    def feeder(value: float) -> None:
        block = np.full((16, 1), value, dtype=np.float32)
        gate.wait()
        for _ in range(50):
            callback(block, 16, None, 0)

    threads = [
        threading.Thread(target=feeder, args=(v,)) for v in (0.1, 0.2, 0.3, 0.4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    audio = rec.stop()

    assert audio.size == 4 * 50 * 16
    for value in (0.1, 0.2, 0.3, 0.4):
        assert np.count_nonzero(np.isclose(audio, value)) == 50 * 16


# ---------------------------------------------------------------------------
# Things going wrong
# ---------------------------------------------------------------------------


def test_host_api_without_wasapi_falls_back_to_a_plain_stream(fake_sd):
    # e.g. an MME / DirectSound / WDM-KS device: WasapiSettings is rejected, and
    # the recording must still work without it.
    fake_sd.reject_extra_settings = True
    rec = Recorder()

    rec.start()

    assert fake_sd.stream.kwargs["extra_settings"] is None
    assert fake_sd.initialize_calls == 0  # not a PortAudio fault: no re-init
    fake_sd.stream.feed([0.5])
    np.testing.assert_allclose(rec.stop(), [0.5], rtol=1e-6)


def test_a_portaudio_error_on_open_reinitialises_and_retries_once(fake_sd):
    fake_sd.unplug()  # PortAudio's global state has gone stale
    fake_sd.reinit_recovers = True
    rec = Recorder()

    rec.start()

    assert fake_sd.terminate_calls == 1
    assert fake_sd.initialize_calls == 1
    assert rec.is_recording is True
    fake_sd.stream.feed([0.7])
    np.testing.assert_allclose(rec.stop(), [0.7], rtol=1e-6)


def test_a_microphone_that_stays_broken_raises_a_clear_error(fake_sd):
    fake_sd.unplug()
    fake_sd.reinit_recovers = False
    rec = Recorder()

    with pytest.raises(RecorderError) as excinfo:
        rec.start()

    message = str(excinfo.value)
    assert "microphone" in message.lower()
    assert "Device unavailable" in message  # PortAudio's own words are kept
    assert rec.is_recording is False  # no half-open state left behind
    assert fake_sd.terminate_calls == 1  # retried once, not in a loop
    assert fake_sd.initialize_calls == 1


def test_a_stream_that_fails_to_start_is_closed_not_leaked(fake_sd):
    # A leaked half-open stream keeps the Windows mic light on for good.
    fake_sd.fail_stream_start = True
    rec = Recorder()

    with pytest.raises(RecorderError):
        rec.start()

    assert fake_sd.streams, "the fake should have handed out streams to close"
    assert all(stream.closed for stream in fake_sd.streams)
    assert rec.is_recording is False


def test_a_device_unplugged_mid_recording_surfaces_on_the_next_start(fake_sd):
    rec = Recorder()
    rec.start()
    fake_sd.stream.feed([0.1, 0.2])
    fake_sd.unplug()  # the USB mic is pulled out mid-sentence
    fake_sd.reinit_recovers = False

    audio = rec.stop()  # must not raise: what was heard is still worth keeping

    np.testing.assert_allclose(audio, [0.1, 0.2], rtol=1e-6)
    assert rec.is_recording is False

    with pytest.raises(RecorderError):
        rec.start()
    assert rec.is_recording is False


def test_missing_sounddevice_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(recorder_module, "sd", None)

    with pytest.raises(RecorderError) as excinfo:
        Recorder().start()

    assert "sounddevice" in str(excinfo.value)
