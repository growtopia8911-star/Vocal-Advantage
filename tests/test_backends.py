"""Unit tests for backend choice and the startup log line (spec item 3).

THE SEAM: ``choose_backend`` takes every fact about the machine as an argument
-- platform, architecture, whether CUDA answered, whether mlx imported. So the
Mac's selection logic is tested on Windows and vice versa, and neither test run
needs the hardware it is deciding about.

The real probes (``has_cuda``, ``has_mlx``) are thin and tested separately for
"does not raise", because that is genuinely all they promise on a machine
without the hardware.
"""

from __future__ import annotations

import numpy as np
import pytest

from vocal_advantage.backends import (
    BackendChoice,
    Segment,
    choose_backend,
    describe_choice,
    has_cuda,
    has_mlx,
    mlx_repo_for,
    normalise_segments,
)


def choice(**kwargs) -> BackendChoice:
    """choose_backend with a fully-specified machine, overridable per test."""
    machine = {
        "device_setting": "auto",
        "platform": "darwin",
        "machine": "arm64",
        "cuda": False,
        "mlx": True,
    }
    machine.update(kwargs)
    return choose_backend(**machine)


# --- 3a: Metal on an Apple Silicon Mac --------------------------------------


def test_apple_silicon_with_mlx_chooses_metal():
    c = choice()
    assert c.backend == "mlx-whisper"
    assert c.device == "metal"


def test_apple_silicon_without_mlx_falls_back_to_cpu():
    """mlx-whisper is optional. Its absence must not stop the app starting."""
    c = choice(mlx=False)
    assert c.backend == "faster-whisper"
    assert c.device == "cpu"


def test_an_intel_mac_does_not_choose_metal():
    """MLX is Apple Silicon only; an x86 Mac has no Metal compute path here."""
    c = choice(machine="x86_64", mlx=True)
    assert c.device == "cpu"


# --- 3b: CUDA on Windows ----------------------------------------------------


def test_windows_with_a_card_chooses_cuda():
    c = choice(platform="win32", machine="AMD64", cuda=True, mlx=False)
    assert c.backend == "faster-whisper"
    assert c.device == "cuda"
    assert c.compute_type == "int8_float16"


def test_windows_without_a_card_chooses_cpu():
    c = choice(platform="win32", machine="AMD64", cuda=False, mlx=False)
    assert c.device == "cpu"
    assert c.compute_type == "int8"


# --- 3c: CPU is always reachable --------------------------------------------


def test_with_no_acceleration_at_all_cpu_is_chosen_and_nothing_raises():
    c = choice(platform="linux", machine="x86_64", cuda=False, mlx=False)
    assert c.device == "cpu"


def test_every_choice_carries_a_fallback_chain_ending_at_cpu():
    """Whatever is picked, there is always somewhere to demote to."""
    for kwargs in (
        {},
        {"cuda": True, "platform": "win32", "mlx": False},
        {"mlx": False},
    ):
        c = choice(**kwargs)
        assert c.fallbacks, "no fallback chain"
        assert c.fallbacks[-1] == ("faster-whisper", "cpu", "int8")


# --- 3e: an explicit device setting still wins ------------------------------

def test_explicit_cpu_is_obeyed_even_on_a_metal_machine():
    assert choice(device_setting="cpu").device == "cpu"


def test_explicit_metal_is_obeyed():
    c = choice(device_setting="metal", platform="darwin", machine="arm64")
    assert c.backend == "mlx-whisper"


def test_explicit_cuda_is_obeyed_even_with_no_card_detected():
    """An explicit request deserves an explicit failure, not a silent demotion."""
    c = choice(device_setting="cuda", platform="win32", cuda=False, mlx=False)
    assert c.device == "cuda"


def test_an_unknown_device_setting_warns_and_behaves_like_auto():
    c = choice(device_setting="banana")
    assert c.device == "metal"
    assert c.warning is not None


def test_explicit_metal_on_a_machine_without_mlx_falls_back_and_warns():
    c = choice(device_setting="metal", mlx=False)
    assert c.device == "cpu"
    assert c.warning is not None


# --- 3d: exactly one log line -----------------------------------------------


def test_the_description_names_backend_device_and_compute_type():
    line = describe_choice(choice(platform="win32", cuda=True, mlx=False))
    assert "faster-whisper" in line
    assert "cuda" in line
    assert "int8_float16" in line


def test_the_description_is_a_single_line():
    """3d: one line. A block here is noise on every launch."""
    assert "\n" not in describe_choice(choice())


def test_the_metal_description_says_metal():
    assert "metal" in describe_choice(choice()).lower()


# --- model naming -----------------------------------------------------------


def test_a_plain_model_name_maps_to_an_mlx_community_repo():
    assert mlx_repo_for("small") == "mlx-community/whisper-small-mlx"
    assert mlx_repo_for("tiny") == "mlx-community/whisper-tiny-mlx"


def test_a_full_repo_id_is_passed_through_untouched():
    """Users may name any HuggingFace repo; do not mangle one that has a slash."""
    assert mlx_repo_for("mlx-community/whisper-turbo") == "mlx-community/whisper-turbo"


def test_an_unknown_short_name_still_produces_a_plausible_repo():
    assert mlx_repo_for("medium") == "mlx-community/whisper-medium-mlx"


# --- segment normalisation --------------------------------------------------


class FakeFwSegment:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


def test_faster_whisper_objects_normalise():
    segs = normalise_segments([FakeFwSegment(" hello ", 0.1, -0.2)])
    assert segs[0].text == " hello "
    assert segs[0].no_speech_prob == pytest.approx(0.1)
    assert segs[0].avg_logprob == pytest.approx(-0.2)


def test_mlx_dicts_normalise_to_the_same_shape():
    """MLX hands back dicts with the very same two guard fields."""
    segs = normalise_segments(
        [{"text": " hello ", "no_speech_prob": 0.1, "avg_logprob": -0.2}]
    )
    assert isinstance(segs[0], Segment)
    assert segs[0].text == " hello "
    assert segs[0].no_speech_prob == pytest.approx(0.1)


def test_a_segment_missing_the_guard_fields_is_kept_not_dropped():
    """Absent metadata must read as "no reason to bin this", not as silence."""
    segs = normalise_segments([{"text": "hello"}])
    assert segs[0].no_speech_prob == 0.0
    assert segs[0].avg_logprob == 0.0


# --- the real probes --------------------------------------------------------


def test_the_probes_answer_a_bool_on_this_machine_whatever_it_is():
    assert isinstance(has_cuda(), bool)
    assert isinstance(has_mlx(), bool)


# --- the VAD that switching engines silently removed ------------------------
#
# faster-whisper runs Silero over every utterance (vad_filter=True). mlx-whisper
# has none, so moving to Metal dropped a preprocessing step worth ~6 points of
# WER on tests/fixtures/accuracy. apply_vad puts it back.


def _speech(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * 16000), dtype=np.float32) / 16000
    return (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * 16000), dtype=np.float32)


def test_the_vad_never_raises_and_always_returns_audio():
    """The contract that matters: this is an improvement, not a dependency."""
    from vocal_advantage.backends import apply_vad

    out = apply_vad(np.concatenate([_silence(1.0), _speech(1.0), _silence(1.0)]))
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float32


def test_the_vad_returns_the_original_when_it_finds_no_speech():
    """Handing the decoder nothing is far worse than handing it everything."""
    from vocal_advantage.backends import apply_vad

    clip = _silence(2.0)
    assert apply_vad(clip).size == clip.size


def test_the_vad_handles_empty_audio():
    from vocal_advantage.backends import apply_vad

    assert apply_vad(np.empty(0, dtype=np.float32)).size == 0


def test_a_broken_vad_falls_back_to_the_original_audio(monkeypatch):
    """A missing or broken faster_whisper must not empty a dictation."""
    import vocal_advantage.backends as backends_module

    monkeypatch.setattr(backends_module, "_VAD_UNAVAILABLE", False)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def explode(name, *args, **kwargs):
        if name == "faster_whisper.vad":
            raise ImportError("no faster_whisper here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", explode)
    clip = _speech(1.0)
    assert backends_module.apply_vad(clip).size == clip.size


def test_the_vad_settings_match_what_faster_whisper_is_given():
    """Both engines must see the same audio or the comparison is meaningless."""
    from vocal_advantage import backends as b
    from vocal_advantage.transcriber import Transcriber

    kwargs = Transcriber("small", "cpu", "en", 0.4)._transcribe_kwargs()
    assert kwargs["vad_parameters"]["min_silence_duration_ms"] == b.VAD_MIN_SILENCE_MS
    assert kwargs["vad_parameters"]["speech_pad_ms"] == b.VAD_SPEECH_PAD_MS
