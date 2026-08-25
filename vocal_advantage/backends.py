"""Which inference engine runs the model, and the one line that says so.

Two engines, because no single one covers both machines:

* **faster-whisper** (CTranslate2) -- CUDA on Windows, CPU everywhere. This is
  what the project has always used.
* **mlx-whisper** (Apple MLX) -- Metal on Apple Silicon. Optional; the app
  starts without it and simply runs on the CPU instead.

**Why two.** CTranslate2 has a CUDA backend and no Metal one -- on this arm64
Mac it reports compute types ``{int8, float32, int8_float32}`` and zero GPU
devices, so "use the GPU on a Mac" is not a setting that exists, it is a
different engine. MLX is Apple's own array framework and runs Whisper on the
GPU properly: measured here, `small` turns an utterance around in 0.27s warm,
against ~0.30s for the *smaller* `base` model on the same machine's CPU.

DirectML on Windows was considered and rejected: CTranslate2 has no DirectML
backend either, so it would mean a third engine (ONNX Runtime) for the benefit
of non-NVIDIA Windows GPUs only.

**The selection is a pure function.** ``choose_backend`` takes every fact about
the machine as an argument rather than looking any of them up, so the Mac's
logic is testable on Windows and the CUDA logic is testable on a Mac. The
probes that discover those facts live at the bottom and promise only not to
raise.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import numpy as np

#: (backend, device, compute_type) -- what to try, in order.
Rung = tuple[str, str, str]

CPU_RUNG: Rung = ("faster-whisper", "cpu", "int8")
CUDA_RUNG: Rung = ("faster-whisper", "cuda", "int8_float16")
METAL_RUNG: Rung = ("mlx-whisper", "metal", "float16")


@dataclass
class Segment:
    """One transcribed span, in the one shape both engines are folded into.

    Only the three fields anything downstream reads. ``keep_segment``'s
    hallucination guard needs the two probabilities, and both engines happen to
    report them under the same names -- which is the only reason this adapter
    is three lines rather than a translation table.
    """

    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0


@dataclass
class BackendChoice:
    """What to load, what to fall back to, and what to tell the user."""

    backend: str
    device: str
    compute_type: str
    fallbacks: list[Rung] = field(default_factory=list)
    warning: str | None = None

    @property
    def rung(self) -> Rung:
        return (self.backend, self.device, self.compute_type)


# --- model naming -----------------------------------------------------------

def mlx_repo_for(model_name: str) -> str:
    """The HuggingFace repo holding the MLX build of ``model_name``.

    A name containing a slash is already a repo id and is passed straight
    through -- users may name any repo, and "correcting" one would break the
    only escape hatch they have.
    """
    name = (model_name or "small").strip()
    if "/" in name:
        return name
    return f"mlx-community/whisper-{name}-mlx"


# --- the choice -------------------------------------------------------------

def _auto_rung(platform: str, machine: str, cuda: bool, mlx: bool) -> Rung:
    if platform == "darwin":
        # MLX is Apple Silicon only. On an Intel Mac it either will not import
        # or has no GPU to use, and CTranslate2 has no CUDA build for macOS
        # either -- so the honest answer there is the CPU.
        if mlx and machine.lower() in ("arm64", "aarch64"):
            return METAL_RUNG
        return CPU_RUNG
    if cuda:
        return CUDA_RUNG
    return CPU_RUNG


def choose_backend(
    *,
    device_setting: str = "auto",
    platform: str = sys.platform,
    machine: str = "",
    cuda: bool = False,
    mlx: bool = False,
) -> BackendChoice:
    """Decide the engine and device. Pure -- every input is passed in.

    ``device_setting`` comes from config.json and always wins where it can be
    honoured, because an explicit request deserves an explicit failure rather
    than a silent demotion the user never sees.
    """
    setting = (device_setting or "auto").strip().lower()
    warning: str | None = None

    if setting in ("metal", "mlx"):
        if mlx and platform == "darwin":
            chosen = METAL_RUNG
        else:
            chosen = CPU_RUNG
            warning = (
                "device is set to 'metal' but mlx-whisper is not available "
                "here, so the CPU will be used. Install it with "
                "`pip install mlx-whisper` on an Apple Silicon Mac."
            )
    elif setting == "cuda":
        chosen = CUDA_RUNG  # obeyed even with no card detected; see the docstring
    elif setting == "cpu":
        chosen = CPU_RUNG
    elif setting == "auto":
        chosen = _auto_rung(platform, machine, cuda, mlx)
    else:
        chosen = _auto_rung(platform, machine, cuda, mlx)
        warning = (
            f"unknown device {device_setting!r} in config.json; "
            f"using 'auto'."
        )

    # Everything can demote to the CPU, and the CPU rung is never listed as its
    # own fallback -- a chain that retried the thing that just failed would
    # spin rather than recover.
    fallbacks = [rung for rung in (CUDA_RUNG, CPU_RUNG) if rung != chosen]
    if chosen == CPU_RUNG:
        fallbacks = [CPU_RUNG]
    elif fallbacks[-1] != CPU_RUNG:  # pragma: no cover - defensive
        fallbacks.append(CPU_RUNG)
    if chosen == METAL_RUNG:
        fallbacks = [CPU_RUNG]

    backend, device, compute_type = chosen
    return BackendChoice(
        backend=backend,
        device=device,
        compute_type=compute_type,
        fallbacks=fallbacks,
        warning=warning,
    )


_HUMAN = {
    "metal": "Metal (Apple GPU)",
    "cuda": "CUDA (NVIDIA GPU)",
    "cpu": "CPU",
}


def describe_choice(choice: BackendChoice) -> str:
    """The single startup line naming backend, device and compute type.

    One line, deliberately (spec 3d). This prints on every launch, and a block
    of diagnostics there is noise that trains people to skip the console -- the
    place the timing report has to be read.
    """
    human = _HUMAN.get(choice.device, choice.device)
    return (
        f"Speech backend: {choice.backend} on {human} "
        f"[{choice.device}/{choice.compute_type}]"
    )


# --- segment normalisation --------------------------------------------------

def normalise_segments(segments) -> list[Segment]:
    """Fold either engine's segments into ``Segment``.

    faster-whisper yields objects with attributes; MLX yields dicts. A missing
    guard field defaults to the *keep* value, never the drop value: absent
    metadata means "no reason to bin this", and defaulting the other way would
    silently delete real speech from any engine that stopped reporting it.
    """
    out: list[Segment] = []
    for seg in segments:
        if isinstance(seg, Segment):
            out.append(seg)
        elif isinstance(seg, dict):
            out.append(
                Segment(
                    text=seg.get("text", "") or "",
                    no_speech_prob=float(seg.get("no_speech_prob", 0.0) or 0.0),
                    avg_logprob=float(seg.get("avg_logprob", 0.0) or 0.0),
                )
            )
        else:
            out.append(
                Segment(
                    text=getattr(seg, "text", "") or "",
                    no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
                    avg_logprob=float(getattr(seg, "avg_logprob", 0.0) or 0.0),
                )
            )
    return out


# --- the MLX engine ---------------------------------------------------------

#: Same VAD settings faster-whisper is given in transcriber._transcribe_kwargs.
#: Kept identical on purpose: the whole point is that both engines see the same
#: audio, so a difference in what they return is a difference in the *decoder*
#: and not in what was fed to it.
VAD_MIN_SILENCE_MS: int = 500
VAD_SPEECH_PAD_MS: int = 400

_VAD_UNAVAILABLE = False


def apply_vad(audio: np.ndarray) -> np.ndarray:
    """Keep only the speech, using faster-whisper's bundled Silero VAD.

    **Why this exists.** faster-whisper runs Silero over every utterance
    (``vad_filter=True``) before the encoder ever sees it. mlx-whisper has no
    such thing, so switching engines silently removed a preprocessing step that
    was doing real work -- measured at ~6 points of word error rate on the eight
    clips in tests/fixtures/accuracy, which is far more than the Metal speed-up
    is worth.

    Silero is a small ONNX model on the CPU, so this costs a few milliseconds
    and does not compete with the GPU the decoder is using.

    Never raises. If the VAD is unavailable or finds nothing, the original
    audio is returned: handing the decoder everything is much better than
    handing it silence, and a missing optional dependency must not be the
    reason a dictation comes back empty.
    """
    global _VAD_UNAVAILABLE
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if _VAD_UNAVAILABLE or audio.size == 0:
        return audio
    try:
        from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

        options = VadOptions(
            min_silence_duration_ms=VAD_MIN_SILENCE_MS,
            speech_pad_ms=VAD_SPEECH_PAD_MS,
        )
        spans = get_speech_timestamps(audio, options)
        if not spans:
            return audio  # all silence by Silero's reckoning; let the guards decide
        chunks, _meta = collect_chunks(audio, spans)
        if not chunks:
            return audio
        kept = np.concatenate(chunks).astype(np.float32, copy=False)
        return kept if kept.size else audio
    except Exception:  # noqa: BLE001 - the VAD is an improvement, not a dependency
        _VAD_UNAVAILABLE = True  # do not pay the import cost again every chunk
        return audio


class MlxWhisperModel:
    """Apple MLX, wearing faster-whisper's interface.

    The whole point of this class is the shape of ``transcribe``: it takes the
    same keyword arguments and returns the same ``(segments, info)`` pair that
    ``faster_whisper.WhisperModel`` does, so ``Transcriber`` can hold either
    one without knowing which. Everything downstream -- the hallucination
    guard, ``assemble_text``, the device-demotion ladder -- then works
    unchanged for both engines.

    Arguments MLX has no equivalent for are dropped rather than forwarded.
    ``beam_size`` and ``vad_filter`` are the notable ones: MLX's decoder has no
    beam-search parameter exposed here, and it has no built-in VAD. Losing the
    VAD matters less than it sounds, because ``vad.trim_silence`` has already
    cut the silence off both ends before the audio reaches this point.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.repo = mlx_repo_for(model_name)
        import mlx_whisper  # deferred: importing MLX costs real time

        self._transcribe = mlx_whisper.transcribe

    def transcribe(self, audio: np.ndarray, **kwargs):
        """Same signature and same return shape as WhisperModel.transcribe."""
        call = {
            "path_or_hf_repo": self.repo,
            "language": kwargs.get("language"),
            "condition_on_previous_text": kwargs.get(
                "condition_on_previous_text", False
            ),
            # Whisper's own no-speech threshold, matching the guard applied
            # downstream so the two do not disagree about the same segment.
            "no_speech_threshold": 0.6,
        }
        # mlx-whisper has no `hotwords`; `initial_prompt` biases the model the
        # same way. The dictionary's fixes pass still runs afterwards either
        # way, so a name this misses is corrected rather than lost.
        hotwords = kwargs.get("hotwords")
        if hotwords:
            call["initial_prompt"] = hotwords
        # The VAD faster-whisper applies for free, applied here too, so the two
        # engines are compared on the same audio rather than one being handed a
        # cleaner signal than the other. See apply_vad.
        audio = np.asarray(audio, dtype=np.float32)
        if kwargs.get("vad_filter", True):
            audio = apply_vad(audio)
        result = self._transcribe(audio, **call)
        return normalise_segments(result.get("segments", [])), None


# --- the probes -------------------------------------------------------------
#
# Each promises exactly one thing: it returns a bool and never raises. A
# missing library, a driver that will not load, an import that segfaults on
# some other machine -- all of them mean "no", and none of them may stop the
# app starting.

def has_cuda() -> bool:
    """True if CTranslate2 can see at least one CUDA device."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # noqa: BLE001
        return False


def has_mlx() -> bool:
    """True if mlx-whisper is installed and Metal is actually available."""
    try:
        import mlx.core as mx
        import mlx_whisper  # noqa: F401

        return bool(mx.metal.is_available())
    except Exception:  # noqa: BLE001
        return False
