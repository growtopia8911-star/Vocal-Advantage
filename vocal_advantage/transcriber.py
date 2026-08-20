"""faster-whisper wrapper plus the guards on what it hands back.

Nothing here imports faster_whisper at module scope -- see
``_default_model_factory`` for why that matters.
"""

import sys
from collections.abc import Iterable

import numpy as np

from vocal_advantage import cuda_dlls

# Must match recorder.SAMPLE_RATE. Duplicated on purpose: importing recorder
# here would drag sounddevice (and a real audio device) into every transcriber
# test run.
SAMPLE_RATE: int = 16000

# Hallucination guard #3 from the spec. Whisper invents "Thank you." out of
# silence; such segments score high on no_speech_prob AND low on avg_logprob.
# Both must be true -- quiet-but-real speech regularly trips one alone.
NO_SPEECH_THRESHOLD: float = 0.6
AVG_LOGPROB_THRESHOLD: float = -1.0


def keep_segment(seg) -> bool:
    """False only when both no-speech signals agree this is not speech."""
    return not (
        seg.no_speech_prob > NO_SPEECH_THRESHOLD
        and seg.avg_logprob < AVG_LOGPROB_THRESHOLD
    )


def assemble_text(segments: Iterable) -> str:
    """Kept segments, each stripped, joined by single spaces. "" if none."""
    kept = []
    for seg in segments:
        if not keep_segment(seg):
            continue
        text = seg.text.strip()
        if text:
            kept.append(text)
    return " ".join(kept)


# Measured on disk in ~/.cache/huggingface/hub, int8 builds. Only the models
# we have actually weighed appear here: users may name any HuggingFace repo,
# and a guessed number in a "this will download N" warning is worse than no
# number at all.
MODEL_DOWNLOAD_SIZES: dict[str, str] = {
    "tiny": "75 MB",
    "base": "141 MB",
    "small": "464 MB",
    "large-v3-turbo": "1.5 GB",
}


def download_note(model_name: str) -> str:
    """One parenthetical about first-run download cost for ``model_name``."""
    size = MODEL_DOWNLOAD_SIZES.get(model_name)
    if size is None:
        return "(First run only: this downloads the model to your user cache.)"
    return f"(First run only: this downloads {size} to your user cache.)"


# Fallback chain per device setting. cuda/int8_float16 uses ~1.5-2GB of the
# 6GB card and turns a 10-30s utterance around in 1-2s; cpu/int8 is the
# it-still-works path.
_DEVICE_PLANS: dict[str, tuple[tuple[str, str], ...]] = {
    "auto": (("cuda", "int8_float16"), ("cpu", "int8")),
    "cuda": (("cuda", "int8_float16"), ("cpu", "int8")),
    "cpu": (("cpu", "int8"),),
}


def _default_model_factory(model_name: str, device: str, compute_type: str):
    """Build a real WhisperModel.

    The faster_whisper import lives inside this function on purpose: it must
    not happen until cuda_dlls.prepare() has registered the CUDA DLL
    directories, and a module-scope import would fire the moment anything
    imported this file.
    """
    cuda_dlls.prepare()  # idempotent; the import on the next line depends on it
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device=device, compute_type=compute_type)


class Transcriber:
    """Loads Whisper once, keeps it resident, and guards what comes back."""

    #: Test seam. Called as model_factory(model_name, device, compute_type) and
    #: must return an object with .transcribe(audio, **kwargs) -> (segments, info).
    model_factory = staticmethod(_default_model_factory)

    def __init__(
        self,
        model_name: str,
        device: str,
        language: str,
        min_duration_s: float,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.language = language
        self.min_duration_s = min_duration_s
        self.device_in_use: str | None = None
        self.compute_type_in_use: str | None = None
        self._model = None

    # -- model lifecycle ----------------------------------------------------

    def _device_plan(self) -> tuple[tuple[str, str], ...]:
        key = (self.device or "").strip().lower()
        plan = _DEVICE_PLANS.get(key)
        if plan is None:
            print(
                f"WARNING: unknown device {self.device!r} in config.json; using 'auto'.",
                file=sys.stderr,
                flush=True,
            )
            plan = _DEVICE_PLANS["auto"]
        return plan

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        plan = self._device_plan()
        print(
            f"Loading speech model {self.model_name!r} "
            f"{download_note(self.model_name)}",
            flush=True,
        )

        failures: list[str] = []
        for device, compute_type in plan:
            try:
                model = self.model_factory(self.model_name, device, compute_type)
            except Exception as exc:  # any load failure is a fallback, not a crash
                failures.append(f"{device}/{compute_type}: {exc}")
                print(
                    f"WARNING: could not load the model on {device} "
                    f"({compute_type}): {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                continue

            if device == "cpu" and plan[0][0] != "cpu":
                print(
                    "WARNING: running on CPU. Transcription will take several "
                    "seconds per utterance instead of 1-2.",
                    file=sys.stderr,
                    flush=True,
                )

            self._model = model
            self.device_in_use = device
            self.compute_type_in_use = compute_type
            print(f"Model ready on {device} ({compute_type}).", flush=True)
            return model

        raise RuntimeError(
            "Could not load the speech model on any device: " + "; ".join(failures)
        )

    # -- transcription ------------------------------------------------------

    def _transcribe_kwargs(self) -> dict:
        return {
            "language": self.language,
            # beam 1 matched beam 5 on LocalFlow's dictation WER benchmark.
            "beam_size": 1,
            "vad_filter": True,
            # The library default of 2000ms silence adds latency for nothing.
            "vad_parameters": {
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 400,
            },
            # Stops Whisper looping the previous sentence forever.
            "condition_on_previous_text": False,
            "without_timestamps": True,
        }

    def _run(self, audio: np.ndarray) -> str:
        model = self._ensure_model()
        segments, _info = model.transcribe(audio, **self._transcribe_kwargs())
        # The return is a lazy generator -- consume it now or no work happens.
        segments = list(segments)
        return assemble_text(segments)

    def warm_up(self) -> None:
        """Pay the 1-3s CUDA init cost at startup, not mid-dictation."""
        self._run(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcript of ``audio``; "" when it is too short or nothing was heard."""
        if audio is None:
            return ""
        n_samples = int(np.asarray(audio).size)
        if n_samples == 0:
            return ""
        if n_samples / SAMPLE_RATE < self.min_duration_s:
            return ""  # guard #1: short taps never reach the model
        return self._run(audio)
