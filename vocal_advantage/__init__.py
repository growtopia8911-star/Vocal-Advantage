"""Vocal Advantage — local hold-to-talk dictation for Windows.

This file deliberately imports nothing. The NVIDIA DLL wiring in
``cuda_dlls.prepare()`` has to run *before* ``faster_whisper`` is first
imported (SPEC, "CUDA DLL wiring"), so import order stays under the explicit
control of ``main.py`` rather than being forced by importing the package.
"""
