"""Point the Windows DLL loader at the CUDA libraries pip installed for us.

Must run BEFORE ``faster_whisper`` is imported anywhere in the process.
ctranslate2 (the engine under faster-whisper) resolves ``cublas64_12.dll`` and
``cudnn_ops64_9.dll`` while it is being imported, and Python 3.8+ ignores
``PATH`` for DLL resolution -- only directories registered with
``os.add_dll_directory()`` are searched. Skip this and the import fails with
"Could not locate cublas64_12.dll".

Two things guarantee the ordering:

1. ``transcriber.py`` never imports faster_whisper at module scope; the only
   import sits inside ``_default_model_factory``, on the line after a
   ``cuda_dlls.prepare()`` call.
2. ``main.py`` (Task 10) calls ``prepare()`` as its first executable statement::

       from vocal_advantage import cuda_dlls

       cuda_dlls.prepare()  # MUST come before any other project import

       from vocal_advantage.config import load_config       # noqa: E402
       from vocal_advantage.transcriber import Transcriber  # noqa: E402

   and ``__main__.py`` contains only
   ``from vocal_advantage.main import main`` + ``main()``, so ``python -m
   vocal_advantage`` cannot reach the model code by any other route.

``prepare()`` is idempotent and never raises: on a CPU-only machine the NVIDIA
folders simply are not there, and the app must still start (it falls back to
CPU transcription).
"""

import os
import site
import sysconfig
from pathlib import Path

# Where the nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels put their binaries.
_NVIDIA_BIN_SUBDIRS = ("cublas/bin", "cudnn/bin")

# add_dll_directory() returns a handle that REMOVES the directory again when it
# is closed or garbage collected, so the handles must live as long as the
# process does.
_dll_directory_handles: list[object] = []

_prepared = False


def _candidate_roots() -> list[Path]:
    """Directories that might contain an installed ``nvidia`` package.

    Seam: the tests replace this so they can point at a tmp_path tree.
    """
    roots: list[Path] = []

    purelib = sysconfig.get_paths().get("purelib")
    if purelib:
        roots.append(Path(purelib))

    try:
        roots.extend(Path(p) for p in site.getsitepackages())
    except AttributeError:  # pragma: no cover - absent in some embedded builds
        pass

    try:
        user_site = site.getusersitepackages()
    except AttributeError:  # pragma: no cover - same
        user_site = None
    if user_site:
        roots.append(Path(user_site))

    return _dedupe(roots)


def _dedupe(paths: list[Path]) -> list[Path]:
    """Drop repeats (case-insensitively, this is Windows) but keep the order."""
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def nvidia_dll_dirs() -> list[Path]:
    """The NVIDIA DLL folders that actually exist on this machine."""
    candidates = [
        root / "nvidia" / sub
        for root in _candidate_roots()
        for sub in _NVIDIA_BIN_SUBDIRS
    ]
    return [path for path in _dedupe(candidates) if path.is_dir()]


def _prepend_to_path(directory: str) -> None:
    entries = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if any(entry.lower() == directory.lower() for entry in entries):
        return
    os.environ["PATH"] = os.pathsep.join([directory, *entries])


def prepare() -> None:
    """Wire up the CUDA DLLs. Safe to call repeatedly; never raises."""
    global _prepared
    if _prepared:
        return

    # Two copies of the OpenMP runtime end up loaded (ctranslate2 and
    # onnxruntime each ship one). Without this the process aborts with
    # "OMP: Error #15". The thread cap keeps CPU fallback from pegging the box.
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    os.environ["OMP_NUM_THREADS"] = "4"

    add_dll_directory = getattr(os, "add_dll_directory", None)  # Windows only

    for dll_dir in nvidia_dll_dirs():
        if add_dll_directory is not None:
            _dll_directory_handles.append(add_dll_directory(str(dll_dir)))
        _prepend_to_path(str(dll_dir))

    _prepared = True
