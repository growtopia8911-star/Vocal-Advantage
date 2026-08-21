"""Transcription output guards and the faster-whisper wrapper."""

import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from vocal_advantage import transcriber
from vocal_advantage import transcriber as transcriber_module
from vocal_advantage.transcriber import (
    SAMPLE_RATE,
    normalise_level,
    Transcriber,
    assemble_text,
    keep_segment,
)


@dataclass(frozen=True)
class FakeSegment:
    """The three fields of a faster-whisper Segment that our guards read."""

    text: str = "hello"
    no_speech_prob: float = 0.0
    avg_logprob: float = -0.2


# name, no_speech_prob, avg_logprob, keep?
KEEP_CASES = [
    ("clean loud speech", 0.01, -0.20, True),
    ("both guards tripped -> drop", 0.90, -2.00, False),
    ("classic silence hallucination -> drop", 0.85, -1.50, False),
    ("no_speech exactly 0.6 -> keep (rule is strictly greater)", 0.60, -2.00, True),
    ("logprob exactly -1.0 -> keep (rule is strictly less)", 0.61, -1.00, True),
    ("both exactly on the thresholds -> keep", 0.60, -1.00, True),
    ("a hair past both thresholds -> drop", 0.61, -1.01, False),
    ("only no_speech tripped -> keep", 0.99, -0.10, True),
    ("only logprob tripped -> keep", 0.05, -3.00, True),
    ("no_speech 0.0 and logprob 0.0 -> keep", 0.00, 0.00, True),
]


@pytest.mark.parametrize(
    "no_speech_prob,avg_logprob,expected",
    [(case[1], case[2], case[3]) for case in KEEP_CASES],
    ids=[case[0] for case in KEEP_CASES],
)
def test_keep_segment(no_speech_prob, avg_logprob, expected):
    segment = FakeSegment(
        text="Thank you.",
        no_speech_prob=no_speech_prob,
        avg_logprob=avg_logprob,
    )
    assert keep_segment(segment) is expected


def test_assemble_text_of_nothing_is_empty():
    assert assemble_text([]) == ""


def test_assemble_text_joins_kept_segments_with_single_spaces():
    segments = [FakeSegment(text="testing one"), FakeSegment(text="two three")]

    assert assemble_text(segments) == "testing one two three"


def test_assemble_text_strips_each_segment():
    # faster-whisper puts a leading space on nearly every segment.
    segments = [FakeSegment(text=" testing "), FakeSegment(text="  one two  ")]

    assert assemble_text(segments) == "testing one two"


def test_assemble_text_drops_hallucinated_segments_and_keeps_the_order():
    segments = [
        FakeSegment(text="testing one"),
        FakeSegment(text="Thank you.", no_speech_prob=0.9, avg_logprob=-2.0),
        FakeSegment(text="two three"),
    ]

    assert assemble_text(segments) == "testing one two three"


def test_assemble_text_is_empty_when_every_segment_is_dropped():
    segments = [
        FakeSegment(text="Thank you.", no_speech_prob=0.9, avg_logprob=-2.0),
        FakeSegment(text=" you", no_speech_prob=0.7, avg_logprob=-1.4),
    ]

    assert assemble_text(segments) == ""


def test_assemble_text_ignores_blank_segments_without_leaving_double_spaces():
    segments = [
        FakeSegment(text="testing"),
        FakeSegment(text="   "),
        FakeSegment(text="one"),
    ]

    assert assemble_text(segments) == "testing one"


def test_assemble_text_accepts_a_generator():
    # The real input is faster-whisper's lazy generator, not a list.
    segments = (FakeSegment(text=word) for word in ["testing", "one", "two"])

    assert assemble_text(segments) == "testing one two"


class FakeModel:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, segments=()):
        self.segments = list(segments)
        self.calls = []  # (audio, kwargs) per transcribe() call
        self.generator_was_consumed = False

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))

        def lazy_segments():
            # The real API does no work until the generator is iterated.
            self.generator_was_consumed = True
            yield from self.segments

        info = object()  # faster-whisper returns (segments, info)
        return lazy_segments(), info


class FakeFactory:
    """Stands in for the WhisperModel constructor."""

    def __init__(self, model=None, fail_on=()):
        self.model = model if model is not None else FakeModel()
        self.fail_on = set(fail_on)  # device names that blow up
        self.calls = []  # (model_name, device, compute_type)

    def __call__(self, model_name, device, compute_type):
        self.calls.append((model_name, device, compute_type))
        if device in self.fail_on:
            raise RuntimeError(f"no {device} runtime on this machine")
        return self.model


def make_transcriber(factory, *, device="auto", min_duration_s=0.4, language="en"):
    transcriber = Transcriber(
        model_name="large-v3-turbo",
        device=device,
        language=language,
        min_duration_s=min_duration_s,
    )
    transcriber.model_factory = factory  # instance attribute shadows the class one
    return transcriber


def silence(seconds):
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


def test_constructing_a_transcriber_does_not_load_the_model():
    # Startup must not block on a 1.6 GB download until we say so.
    factory = FakeFactory()

    make_transcriber(factory)

    assert factory.calls == []


def test_audio_shorter_than_min_duration_returns_empty_without_loading_the_model():
    # Guard #1: a quick tap of the hotkey never reaches Whisper, so it can
    # never hallucinate "Thank you." out of it.
    factory = FakeFactory(FakeModel([FakeSegment(text="Thank you.")]))
    transcriber = make_transcriber(factory, min_duration_s=0.4)

    assert transcriber.transcribe(silence(0.3)) == ""
    assert factory.calls == []


def test_empty_audio_returns_empty():
    factory = FakeFactory()
    transcriber = make_transcriber(factory)

    assert transcriber.transcribe(np.zeros(0, dtype=np.float32)) == ""
    assert factory.calls == []


def test_audio_over_min_duration_is_transcribed():
    model = FakeModel([FakeSegment(text=" testing one two three")])
    transcriber = make_transcriber(FakeFactory(model), min_duration_s=0.4)

    assert transcriber.transcribe(silence(0.6)) == "testing one two three"


def test_transcribe_passes_the_exact_kwargs_the_spec_pins():
    # Each value is load-bearing: beam_size 1 matches beam 5 for dictation at a
    # fraction of the latency; the 500ms/400ms VAD numbers replace library
    # defaults that add ~2s; condition_on_previous_text=False kills Whisper's
    # repetition loops. A silent regression here costs latency or accuracy.
    model = FakeModel([FakeSegment(text="hi")])
    transcriber = make_transcriber(FakeFactory(model), language="en")
    audio = silence(1.0)

    transcriber.transcribe(audio)

    passed_audio, kwargs = model.calls[0]
    assert passed_audio is audio
    assert kwargs == {
        "language": "en",
        "beam_size": 1,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500, "speech_pad_ms": 400},
        "condition_on_previous_text": False,
        "without_timestamps": True,
    }


def test_transcribe_consumes_the_lazy_generator_before_returning():
    # faster-whisper returns a generator; nothing runs until it is iterated.
    model = FakeModel([FakeSegment(text="hi")])
    transcriber = make_transcriber(FakeFactory(model))

    result = transcriber.transcribe(silence(1.0))

    assert isinstance(result, str)
    assert model.generator_was_consumed is True


def test_transcribe_applies_the_output_guards():
    model = FakeModel(
        [FakeSegment(text="Thank you.", no_speech_prob=0.9, avg_logprob=-2.0)]
    )
    transcriber = make_transcriber(FakeFactory(model))

    # Empty result -> main.py pastes nothing and flashes "nothing heard".
    assert transcriber.transcribe(silence(2.0)) == ""


def test_the_model_is_loaded_once_and_kept_resident():
    # ~2GB of VRAM is the price of 1-2s latency; reloading per utterance is not.
    factory = FakeFactory(FakeModel([FakeSegment(text="hi")]))
    transcriber = make_transcriber(factory)

    transcriber.transcribe(silence(1.0))
    transcriber.transcribe(silence(1.0))

    assert len(factory.calls) == 1


def test_auto_device_prefers_cuda_int8_float16(monkeypatch):
    # "auto" only reaches for CUDA off a Mac; on darwin it goes straight to
    # the CPU, so this test has to say which platform it is about.
    monkeypatch.setattr(transcriber_module.sys, "platform", "win32")
    factory = FakeFactory(FakeModel([FakeSegment(text="hi")]))
    transcriber = make_transcriber(factory, device="auto")

    transcriber.transcribe(silence(1.0))

    assert factory.calls == [("large-v3-turbo", "cuda", "int8_float16")]
    assert transcriber.device_in_use == "cuda"
    assert transcriber.compute_type_in_use == "int8_float16"


def test_cuda_failure_falls_back_to_cpu_with_a_console_warning(capsys, monkeypatch):
    # "auto" only reaches for CUDA off a Mac; on darwin it goes straight to
    # the CPU, so this test has to say which platform it is about.
    monkeypatch.setattr(transcriber_module.sys, "platform", "win32")
    factory = FakeFactory(FakeModel([FakeSegment(text="hi")]), fail_on={"cuda"})
    transcriber = make_transcriber(factory, device="auto")

    assert transcriber.transcribe(silence(1.0)) == "hi"

    assert factory.calls == [
        ("large-v3-turbo", "cuda", "int8_float16"),
        ("large-v3-turbo", "cpu", "int8"),
    ]
    assert transcriber.device_in_use == "cpu"
    warnings = capsys.readouterr().err
    assert "cuda" in warnings
    assert "CPU" in warnings


def test_device_cpu_never_tries_cuda():
    factory = FakeFactory(FakeModel([FakeSegment(text="hi")]))
    transcriber = make_transcriber(factory, device="cpu")

    transcriber.transcribe(silence(1.0))

    assert factory.calls == [("large-v3-turbo", "cpu", "int8")]


def test_an_unrecognised_device_setting_warns_and_behaves_like_auto(capsys):
    # config.json is hand-editable, so a typo must not be fatal.
    factory = FakeFactory(FakeModel([FakeSegment(text="hi")]))
    transcriber = make_transcriber(factory, device="gpu")

    transcriber.transcribe(silence(1.0))

    assert factory.calls[0] == ("large-v3-turbo", "cuda", "int8_float16")
    assert "gpu" in capsys.readouterr().err


def test_a_total_load_failure_raises_with_both_attempts_named(monkeypatch):
    # "auto" only reaches for CUDA off a Mac; on darwin it goes straight to
    # the CPU, so this test has to say which platform it is about.
    monkeypatch.setattr(transcriber_module.sys, "platform", "win32")
    factory = FakeFactory(fail_on={"cuda", "cpu"})
    transcriber = make_transcriber(factory, device="auto")

    with pytest.raises(RuntimeError) as excinfo:
        transcriber.transcribe(silence(1.0))

    message = str(excinfo.value)
    assert "cuda/int8_float16" in message
    assert "cpu/int8" in message


def test_warm_up_runs_half_a_second_of_zeros_through_the_model():
    # First real transcribe() otherwise pays 1-3s of CUDA init while the user
    # is waiting for their words.
    model = FakeModel([])
    transcriber = make_transcriber(FakeFactory(model))

    transcriber.warm_up()

    audio, _kwargs = model.calls[0]
    assert audio.shape == (SAMPLE_RATE // 2,)
    assert audio.dtype == np.float32
    assert not audio.any()
    assert model.generator_was_consumed is True


def test_warm_up_ignores_min_duration_s():
    # A user could set min_duration_s above 0.5 and silently lose the warm-up.
    model = FakeModel([])
    transcriber = make_transcriber(FakeFactory(model), min_duration_s=1.0)

    transcriber.warm_up()

    assert len(model.calls) == 1


def test_importing_the_transcriber_does_not_import_faster_whisper():
    # The load-bearing ordering rule: faster_whisper must not be imported until
    # cuda_dlls.prepare() has run, so it must not be imported at module scope.
    repo_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; import vocal_advantage.transcriber; "
        "print('faster_whisper' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"


FIXTURE_WAV = Path(__file__).resolve().parent / "fixtures" / "testing_one_two_three.wav"


@pytest.mark.slow
def test_the_real_model_transcribes_the_fixture_wav():
    """Real model, real audio, no fakes. Not part of the default run.

    Record the fixture once (see the task notes), then:
        python -m pytest -m slow tests/test_transcriber.py

    Uses whatever config.json names rather than a hardcoded model. It used to
    pin large-v3-turbo, so running the suite silently pulled 1.5 GB -- which it
    duly did on 2026-08-20, re-downloading a model deleted an hour earlier. A
    test that costs a gigabyte to run is a test people stop running, and this
    one exercises the wiring, not any particular model.
    """
    if not FIXTURE_WAV.exists():
        pytest.skip(f"no fixture at {FIXTURE_WAV}; record it first")

    with wave.open(str(FIXTURE_WAV), "rb") as wav:
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getframerate() != SAMPLE_RATE
        ):
            pytest.skip("fixture must be 16kHz mono 16-bit; re-record it")
        raw = wav.readframes(wav.getnframes())

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    from vocal_advantage.config import load_config

    cfg = load_config()
    transcriber = Transcriber(
        model_name=cfg["model"],
        device=cfg["device"],
        language=cfg["language"],
        min_duration_s=float(cfg["min_duration_s"]),
    )
    text = transcriber.transcribe(audio)

    assert "testing" in text.lower()


# -- download size note ----------------------------------------------------


def test_download_note_names_the_size_of_the_model_actually_being_loaded():
    """The startup line must not quote one model's size while loading another."""
    assert "75 MB" in transcriber.download_note("tiny")
    assert "141 MB" in transcriber.download_note("base")
    assert "1.5 GB" in transcriber.download_note("large-v3-turbo")


def test_download_note_invents_no_number_for_an_unknown_model():
    """Users may name any HuggingFace model; guessing its size would mislead."""
    note = transcriber.download_note("someone/a-finetune-we-never-shipped")
    assert note.strip()
    assert "MB" not in note and "GB" not in note


# -- dropped segments must not be silent ------------------------------------


def test_assemble_text_reports_what_it_dropped():
    """A segment binned as silence is invisible otherwise.

    Kevin said "let's meet Tuesday, no, Wednesday" and only "No Wednesday."
    reached the document. A guard that deletes half a sentence without saying
    so makes that indistinguishable from the microphone missing it.
    """
    dropped = []
    segments = [
        FakeSegment(text="Let's meet Tuesday,", no_speech_prob=0.9, avg_logprob=-1.5),
        FakeSegment(text="no, Wednesday.", no_speech_prob=0.1, avg_logprob=-0.2),
    ]
    text = assemble_text(segments, on_dropped=dropped.append)
    assert text == "no, Wednesday."
    assert [seg.text for seg in dropped] == ["Let's meet Tuesday,"]


def test_assemble_text_reports_nothing_when_nothing_is_dropped():
    dropped = []
    assemble_text([FakeSegment(text="hello")], on_dropped=dropped.append)
    assert dropped == []


def test_assemble_text_still_works_without_a_reporter():
    assert assemble_text([FakeSegment(text="hello")]) == "hello"


def test_the_cpu_notice_does_not_promise_a_speed_it_cannot_know(capsys, monkeypatch):
    # "auto" only reaches for CUDA off a Mac; on darwin it goes straight to
    # the CPU, so this test has to say which platform it is about.
    monkeypatch.setattr(transcriber_module.sys, "platform", "win32")
    """It used to say "several seconds per utterance instead of 1-2", which was
    true of large-v3-turbo and is nonsense for base: 0.30s on this Mac's CPU.
    Speed depends on the model, so the notice must not quote a number."""
    factory = FakeFactory(fail_on={"cuda"})
    t = Transcriber("base", "auto", "en", 0.4)
    t.model_factory = factory
    t._ensure_model()
    err = capsys.readouterr().err
    assert "CPU" in err
    assert "several seconds" not in err
    assert "1-2" not in err


def test_auto_does_not_try_cuda_on_a_mac(monkeypatch):
    """CTranslate2 has no CUDA build for Apple Silicon, so attempting it warns
    about a failure that can never be anything else. A warning printed on every
    launch for an impossibility trains people to ignore warnings."""
    monkeypatch.setattr(transcriber.sys, "platform", "darwin")
    t = Transcriber("base", "auto", "en", 0.4)
    assert t._device_plan() == (("cpu", "int8"),)


def test_auto_still_tries_cuda_on_windows(monkeypatch):
    monkeypatch.setattr(transcriber.sys, "platform", "win32")
    t = Transcriber("base", "auto", "en", 0.4)
    assert t._device_plan()[0] == ("cuda", "int8_float16")


def test_asking_for_cuda_explicitly_on_a_mac_still_tries_and_warns(monkeypatch):
    """An explicit request deserves an explicit failure, not silence."""
    monkeypatch.setattr(transcriber.sys, "platform", "darwin")
    t = Transcriber("base", "cuda", "en", 0.4)
    assert t._device_plan()[0] == ("cuda", "int8_float16")


# -- input level ------------------------------------------------------------


def test_quiet_audio_is_brought_up_before_transcription():
    """Kevin's recordings peaked at 0.10-0.19 -- about 15% of the available
    range. Scaling them up scored 8.8% against 9.9% on the same clips with the
    same model, which is a larger gain than moving base -> small cost him in
    speed."""
    quiet = (np.sin(np.linspace(0, 400, SAMPLE_RATE)) * 0.1).astype(np.float32)
    louder = normalise_level(quiet)
    assert float(np.max(np.abs(louder))) == pytest.approx(0.5, abs=0.01)


def test_audio_that_is_already_loud_is_left_alone():
    """Never attenuate: clipping a good signal would be a self-inflicted wound."""
    loud = (np.sin(np.linspace(0, 400, SAMPLE_RATE)) * 0.8).astype(np.float32)
    assert np.array_equal(normalise_level(loud), loud)


def test_silence_is_not_amplified_into_noise():
    """A silent room scaled up 100x is a hallucination generator."""
    silence = np.full(SAMPLE_RATE, 0.0005, dtype=np.float32)
    assert np.array_equal(normalise_level(silence), silence)


def test_the_gain_is_capped():
    """Very quiet speech gets help, but not unlimited help -- the noise floor
    comes up with the voice."""
    faint = (np.sin(np.linspace(0, 400, SAMPLE_RATE)) * 0.01).astype(np.float32)
    assert float(np.max(np.abs(normalise_level(faint)))) < 0.5


def test_an_empty_array_is_handled():
    assert normalise_level(np.empty(0, dtype=np.float32)).size == 0


# ---------------------------------------------------------------------------
# Loading the model proves less than it looks
# ---------------------------------------------------------------------------


class LoadsThenFailsModel:
    """Accepts transcribe(), then blows up when the generator is consumed.

    This is CTranslate2's real behaviour with a missing CUDA library: the
    model constructs happily, and ``Library cublas64_12.dll is not found``
    only arrives on the first encode.
    """

    def __init__(self, error="Library cublas64_12.dll is not found"):
        self.error = error

    def transcribe(self, audio, **kwargs):
        def lazy_segments():
            raise RuntimeError(self.error)
            yield  # pragma: no cover - never reached, makes this a generator

        return lazy_segments(), object()


class FactoryByDevice:
    """Hands back a different model per device."""

    def __init__(self, per_device):
        self.per_device = per_device
        self.calls = []

    def __call__(self, model_name, device, compute_type):
        self.calls.append((model_name, device, compute_type))
        return self.per_device[device]


def test_a_device_that_fails_while_transcribing_is_demoted_not_fatal(monkeypatch):
    """The whole reason the packaged .exe is shippable.

    It carries no NVIDIA wheels, so on any machine with a graphics card
    CTranslate2 says "cuda" is fine and then cannot find cublas. The same
    happens on a machine whose CUDA libraries are present but unloadable -- a
    mismatched driver. Before this, that was a crash on the first sentence.
    """
    monkeypatch.setattr(transcriber_module.sys, "platform", "win32")
    good = FakeModel(segments=[FakeSegment(text="It fell back and kept working.")])
    factory = FactoryByDevice({"cuda": LoadsThenFailsModel(), "cpu": good})

    t = make_transcriber(factory, device="auto")
    text = t.transcribe(silence(2.0))

    assert text == "It fell back and kept working."
    assert t.device_in_use == "cpu"
    # cuda was tried first and is not retried once it has proven itself bad.
    assert [c[1] for c in factory.calls][0] == "cuda"
    assert ("cuda", "int8_float16") in t._rejected


def test_a_cpu_failure_still_raises(monkeypatch):
    """Demotion is not a licence to swallow errors. With nowhere left to fall
    back to, the failure must reach the caller."""
    monkeypatch.setattr(transcriber_module.sys, "platform", "win32")
    factory = FactoryByDevice({
        "cuda": LoadsThenFailsModel(),
        "cpu": LoadsThenFailsModel("cpu is broken too"),
    })
    t = make_transcriber(factory, device="auto")

    with pytest.raises(RuntimeError, match="cpu is broken too"):
        t.transcribe(silence(2.0))
