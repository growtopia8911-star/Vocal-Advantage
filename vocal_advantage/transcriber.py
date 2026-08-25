"""faster-whisper wrapper plus the guards on what it hands back.

Nothing here imports faster_whisper at module scope -- see
``_default_model_factory`` for why that matters.
"""

import sys
from collections.abc import Iterable

import numpy as np

from vocal_advantage import backends, cuda_dlls

#: Test seam, same idea as recorder.sd: bound once here and always called
#: through this name, so a test can say "pretend this machine has MLX" without
#: an Apple GPU. Never call backends.has_mlx() directly below.
has_mlx = backends.has_mlx

# Must match recorder.SAMPLE_RATE. Duplicated on purpose: importing recorder
# here would drag sounddevice (and a real audio device) into every transcriber
# test run.
SAMPLE_RATE: int = 16000

#: Whisper does better on a healthy signal. Kevin's mic peaks around 0.10-0.19
#: -- roughly 15% of the available range -- and simply scaling his recordings
#: up scored 8.8% against 9.9% on the same clips with the same model. Free
#: accuracy, so it happens on every utterance.
TARGET_PEAK: float = 0.5
#: Never boost more than this. Digital gain lifts the room noise along with
#: the voice, and a near-silent clip multiplied a hundredfold is a
#: hallucination generator, not a transcript.
MAX_GAIN: float = 8.0
#: Below this the clip is silence, not quiet speech. Leave it be.
SILENCE_PEAK: float = 0.005


def normalise_level(audio: np.ndarray) -> np.ndarray:
    """Bring quiet audio up towards TARGET_PEAK. Never attenuates.

    Attenuating a healthy signal risks nothing but gains nothing, and clipping
    a loud one would be a self-inflicted wound -- so this only ever boosts.
    """
    if audio is None or audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < SILENCE_PEAK:
        return audio
    gain = min(TARGET_PEAK / peak, MAX_GAIN)
    if gain <= 1.0:
        return audio
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32, copy=False)


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


def assemble_text(segments: Iterable, on_dropped=None) -> str:
    """Kept segments, each stripped, joined by single spaces. "" if none.

    ``on_dropped`` is called with every segment the guard bins. Silence here
    is dangerous: the guard exists to delete Whisper's invented "Thank you."
    from a quiet room, but it deletes by the same rule a real quiet phrase
    the model was unsure about -- and half a missing sentence then looks
    exactly like the microphone having missed it.
    """
    kept = []
    for seg in segments:
        if not keep_segment(seg):
            if on_dropped is not None:
                on_dropped(seg)
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
    # Apple Silicon. A different engine entirely -- see backends.MlxWhisperModel
    # -- reached through the same ladder so a Metal failure demotes to the CPU
    # exactly the way a CUDA failure does.
    "metal": (("metal", "float16"), ("cpu", "int8")),
    "mlx": (("metal", "float16"), ("cpu", "int8")),
}

#: The plan "auto" resolves to on an Apple Silicon Mac that has mlx-whisper.
_MAC_GPU_PLAN = _DEVICE_PLANS["metal"]


def _default_model_factory(model_name: str, device: str, compute_type: str):
    """Build the model for ``device`` -- MLX on Metal, CTranslate2 otherwise.

    Both objects answer ``transcribe(audio, **kwargs) -> (segments, info)``, so
    everything above this line is engine-agnostic.

    The faster_whisper import lives inside this function on purpose: it must
    not happen until cuda_dlls.prepare() has registered the CUDA DLL
    directories, and a module-scope import would fire the moment anything
    imported this file.
    """
    if device == "metal":
        return backends.MlxWhisperModel(model_name)

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
        hotwords: str = "",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.language = language
        self.min_duration_s = min_duration_s
        #: Names and jargon from the personal dictionary, nudging Whisper
        #: toward hearing them. Empty means "no hotwords", which is what
        #: faster-whisper documents. Applied before transcription, so nothing
        #: is rewritten afterwards and a word you really said is never
        #: corrupted -- unlike the fixes pass, which is the fallback for what
        #: this misses.
        self.hotwords = hotwords or ""
        self.device_in_use: str | None = None
        self.compute_type_in_use: str | None = None
        self._model = None
        #: (device, compute_type) pairs that loaded but then failed doing real
        #: work. Loading proves far less than it looks: CTranslate2 accepts
        #: "cuda" whenever a card is present and only discovers a missing
        #: CUDA library on the first encode. See _run.
        self._rejected: set[tuple[str, str]] = set()

    # -- model lifecycle ----------------------------------------------------

    def _device_plan(self) -> tuple[tuple[str, str], ...]:
        key = (self.device or "").strip().lower()
        if key == "auto" and sys.platform == "darwin":
            # CTranslate2 has no CUDA build for Apple Silicon, so "auto" would
            # attempt it, fail, and warn -- every launch, about something that
            # can never work. An explicit device="cuda" still tries, because an
            # explicit request deserves an explicit failure.
            #
            # Metal is reachable, but only through a different engine, and only
            # if it is installed -- mlx-whisper is optional.
            if has_mlx():
                return _MAC_GPU_PLAN
            return _DEVICE_PLANS["cpu"]
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
            if (device, compute_type) in self._rejected:
                continue
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
                    # No number here on purpose. This used to promise
                    # "several seconds per utterance instead of 1-2", which
                    # was true of large-v3-turbo and nonsense for base --
                    # 0.30s on this Mac's CPU. Speed depends entirely on the
                    # model, and a stale figure alarms people for no reason.
                    f"NOTE: running {self.model_name!r} on the CPU. A "
                    "graphics card would be faster; smaller models are "
                    "faster still. See the model table in the README.",
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
        kwargs = {
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
        if self.hotwords:
            # Only when there is something to say: passing "" is harmless but
            # passing the key at all on an older faster-whisper would not be,
            # and this keeps the kwargs identical to before for anyone with an
            # empty dictionary.
            kwargs["hotwords"] = self.hotwords
        return kwargs

    def _run(self, audio: np.ndarray) -> str:
        """Transcribe, demoting the device if it fails while actually working.

        ``_ensure_model``'s ladder only covers *loading*, and loading proves
        very little: CTranslate2 reports "cuda" as usable whenever a card is
        present, then raises ``Library cublas64_12.dll is not found`` on the
        first encode. Anything that ships without the NVIDIA wheels -- the
        packaged .exe -- hits this on every machine that has a graphics card,
        and so does any machine whose CUDA libraries are present but
        unloadable, a mismatched driver being the usual reason.

        Without this, that is a crash on the user's first sentence rather than
        a slower-but-working app.
        """
        for attempt in (1, 2):
            model = self._ensure_model()
            device, compute_type = self.device_in_use, self.compute_type_in_use
            try:
                segments, _info = model.transcribe(
                    normalise_level(audio), **self._transcribe_kwargs()
                )
                # The return is a lazy generator -- consume it now or no work
                # happens, and the failure would surface at the caller.
                segments = list(segments)
            except Exception as exc:  # noqa: BLE001 - demote, do not crash
                if attempt == 2 or device == "cpu":
                    raise
                print(
                    f"WARNING: {device} ({compute_type}) loaded but failed "
                    f"while transcribing: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                print("Falling back to the CPU and retrying.",
                      file=sys.stderr, flush=True)
                self._rejected.add((device, compute_type))
                self._model = None
                self.device_in_use = None
                self.compute_type_in_use = None
                continue
            return assemble_text(segments, on_dropped=self._report_dropped)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _report_dropped(seg) -> None:
        """Say so on the console when the silence guard bins real-looking text."""
        text = (getattr(seg, "text", "") or "").strip()
        if not text:
            return  # binning an empty segment is not worth a line of output
        print(
            f"  [dropped as silence: {text!r} "
            f"(no_speech {getattr(seg, 'no_speech_prob', float('nan')):.2f}, "
            f"logprob {getattr(seg, 'avg_logprob', float('nan')):.2f})]",
            flush=True,
        )

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
