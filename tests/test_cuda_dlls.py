"""The NVIDIA DLL search-path wiring.

These tests never touch a real CUDA install: they build a pretend
site-packages tree in tmp_path and swap out the module's directory-discovery
seam, so they pass identically on a GPU box and a CPU-only laptop.
"""

import os
from pathlib import Path

import pytest

from vocal_advantage import cuda_dlls

# The pre-existing PATH entry that prepare() must prepend in front of. It has
# to be spelled for the host platform, because it must not itself contain
# os.pathsep: on macOS that separator is ":", so the Windows literal
# "C:\Windows\system32" splits into two entries and every "we kept the old
# PATH" assertion below reads wrong. cuda_dlls itself is already pathsep-neutral.
EXISTING_PATH_ENTRY = r"C:\Windows\system32" if os.name == "nt" else "/usr/bin"


@pytest.fixture(autouse=True)
def fresh_module_state(monkeypatch):
    """prepare() is a once-per-process side effect; give each test a clean slate."""
    monkeypatch.setattr(cuda_dlls, "_prepared", False)
    monkeypatch.setattr(cuda_dlls, "_dll_directory_handles", [])
    monkeypatch.setenv("PATH", EXISTING_PATH_ENTRY)
    monkeypatch.delenv("KMP_DUPLICATE_LIB_OK", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)


@pytest.fixture
def added_dirs(monkeypatch):
    """Record every os.add_dll_directory() call instead of really making one.

    os.add_dll_directory only exists on Windows, and we do not want the test
    suite to depend on the OS to prove the calls happen.
    """
    recorded = []

    def fake_add_dll_directory(path):
        recorded.append(path)
        return object()  # stands in for the real handle

    monkeypatch.setattr(os, "add_dll_directory", fake_add_dll_directory, raising=False)
    return recorded


@pytest.fixture
def fake_site_packages(tmp_path, monkeypatch):
    """A site-packages tree with both NVIDIA wheel bin folders present."""
    root = tmp_path / "site-packages"
    (root / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (root / "nvidia" / "cudnn" / "bin").mkdir(parents=True)
    monkeypatch.setattr(cuda_dlls, "_candidate_roots", lambda: [root])
    return root


def path_entries():
    return [p for p in os.environ["PATH"].split(os.pathsep) if p]


def test_prepare_sets_the_openmp_environment_variables(fake_site_packages, added_dirs):
    # OpenMP gets loaded twice (ctranslate2 and onnxruntime each bring a copy);
    # without KMP_DUPLICATE_LIB_OK the process aborts with OMP error #15.
    cuda_dlls.prepare()

    assert os.environ["KMP_DUPLICATE_LIB_OK"] == "TRUE"
    assert os.environ["OMP_NUM_THREADS"] == "4"


def test_prepare_registers_both_nvidia_bin_dirs_with_the_dll_loader(
    fake_site_packages, added_dirs
):
    cuda_dlls.prepare()

    cublas = str(fake_site_packages / "nvidia" / "cublas" / "bin")
    cudnn = str(fake_site_packages / "nvidia" / "cudnn" / "bin")
    assert sorted(added_dirs) == sorted([cublas, cudnn])


def test_prepare_keeps_the_dll_directory_handles_alive(fake_site_packages, added_dirs):
    # The handle returned by add_dll_directory REMOVES the directory again when
    # it is garbage collected, so dropping it would silently undo the wiring.
    cuda_dlls.prepare()

    assert len(cuda_dlls._dll_directory_handles) == 2


def test_prepare_prepends_both_dirs_to_path_without_losing_the_old_path(
    fake_site_packages, added_dirs
):
    cuda_dlls.prepare()

    entries = path_entries()
    cublas = str(fake_site_packages / "nvidia" / "cublas" / "bin")
    cudnn = str(fake_site_packages / "nvidia" / "cudnn" / "bin")
    assert set(entries[:2]) == {cublas, cudnn}
    assert entries[2] == EXISTING_PATH_ENTRY


def test_prepare_is_a_no_op_the_second_time(fake_site_packages, added_dirs):
    cuda_dlls.prepare()
    path_after_first = os.environ["PATH"]

    cuda_dlls.prepare()

    assert len(added_dirs) == 2
    assert os.environ["PATH"] == path_after_first


def test_prepare_never_duplicates_path_entries_even_if_state_is_reset(
    fake_site_packages, added_dirs, monkeypatch
):
    cuda_dlls.prepare()
    monkeypatch.setattr(cuda_dlls, "_prepared", False)

    cuda_dlls.prepare()

    entries = path_entries()
    assert len(entries) == len(set(entries)) == 3


def test_prepare_does_not_raise_on_a_machine_with_no_nvidia_wheels(
    tmp_path, monkeypatch, added_dirs
):
    # CPU-only machine: the folders simply are not there. We still want the
    # OpenMP vars set, and we absolutely must not crash on startup.
    empty = tmp_path / "site-packages"
    empty.mkdir()
    monkeypatch.setattr(cuda_dlls, "_candidate_roots", lambda: [empty])

    cuda_dlls.prepare()

    assert added_dirs == []
    assert os.environ["KMP_DUPLICATE_LIB_OK"] == "TRUE"
    assert path_entries() == [EXISTING_PATH_ENTRY]


def test_prepare_survives_a_platform_without_add_dll_directory(
    fake_site_packages, monkeypatch
):
    # os.add_dll_directory is Windows-only; the module must still import and
    # run (it is the Mac seam) rather than blowing up with AttributeError.
    monkeypatch.delattr(os, "add_dll_directory", raising=False)

    cuda_dlls.prepare()

    assert set(path_entries()[:2]) == {
        str(fake_site_packages / "nvidia" / "cublas" / "bin"),
        str(fake_site_packages / "nvidia" / "cudnn" / "bin"),
    }


def test_nvidia_dll_dirs_reports_only_folders_that_exist(tmp_path, monkeypatch):
    root = tmp_path / "site-packages"
    (root / "nvidia" / "cublas" / "bin").mkdir(parents=True)  # cudnn missing
    monkeypatch.setattr(cuda_dlls, "_candidate_roots", lambda: [root])

    assert cuda_dlls.nvidia_dll_dirs() == [Path(root / "nvidia" / "cublas" / "bin")]


def test_nvidia_dll_dirs_deduplicates_overlapping_roots(tmp_path, monkeypatch):
    root = tmp_path / "site-packages"
    (root / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (root / "nvidia" / "cudnn" / "bin").mkdir(parents=True)
    # sysconfig and site often report the same directory twice in a venv.
    monkeypatch.setattr(cuda_dlls, "_candidate_roots", lambda: [root, root])

    assert len(cuda_dlls.nvidia_dll_dirs()) == 2
