# Fast dictation pipeline — spec

Written 2026-08-25, before any code. Every line below is a yes/no gate.
Numbering matches the eleven requirements as given.

**Decisions taken before writing this** (see "Rejected" at the end for what
they cost):

| Question | Answer |
| --- | --- |
| Metal on Mac | Add `mlx-whisper` as a real Metal backend. faster-whisper cannot do it. |
| Chunk accuracy | Chunk + stitch exactly as specified, accepting the boundary cost. *(Measured afterwards: there was no cost — see Outcome.)* |
| Live typing | Removed. Partials stay internal. |

---

## Startup

1. **Model resident.** `Transcriber.warm_up()` runs once during launch and the
   model object is never released. A second dictation loads no model.
   - [x] 1a. `warm_up()` is called before the app reports "Ready".
   - [x] 1b. Two `transcribe()` calls build the model exactly once.

2. **Microphone open at startup.** The stream opens during launch and stays
   open until shutdown. While not recording, incoming blocks go into a rolling
   buffer that is dropped, not accumulated.
   - [x] 2a. `Recorder.open()` starts a stream; `is_open` is true before any key.
   - [x] 2b. Audio arriving while not capturing is discarded — after 10s idle,
         `stop_capture()` on a 1s capture returns ~1s, not ~11s.
   - [x] 2c. The rolling buffer never grows past its cap while idle.
   - [x] 2d. `start_capture()` does not open a stream (it is already open).
   - [x] 2e. `close()` at shutdown stops the stream.

3. **Accelerated backend, logged.** Pick the fastest available and say which.
   - [x] 3a. On darwin+arm64 with `mlx_whisper` importable, the MLX/Metal
         backend is chosen.
   - [x] 3b. With CUDA present, faster-whisper `cuda/int8_float16` is chosen.
   - [x] 3c. With neither, CPU is chosen and nothing raises.
   - [x] 3d. Exactly one line naming backend, device and compute type is
         printed at startup.
   - [x] 3e. `device` in config still forces a specific backend when set.

## Hotkey

4. **One key, two modes**, split by a configurable threshold.
   - [x] 4a. Press, release at 100ms → still recording (toggle armed).
   - [x] 4b. Press again briefly → stops and processes.
   - [x] 4c. Press, hold 500ms, release → stops on release.
   - [x] 4d. `tap_threshold_s` in config changes the split; 0.3 is the default.
   - [x] 4e. The key-up that follows a toggle-stop starts nothing.

5. **Instant capture and feedback on press.**
   - [x] 5a. Capture begins on the key-down edge, before any transcription.
   - [x] 5b. A start indication fires on that same edge.

6. **Two watchdogs, both configurable.**
   - [x] 6a. Recording stops and processes at `max_duration_s`.
   - [x] 6b. Recording stops and processes after `silence_timeout_s` of
         trailing silence.
   - [x] 6c. `silence_timeout_s: 0` disables the silence watchdog.
   - [x] 6d. Silence auto-stop processes the audio; it does not discard it.

## During recording

7. **Rolling chunks with overlap, never displayed.**
   - [x] 7a. A 5s recording at 2s chunks yields chunks at 2s and 4s.
   - [x] 7b. Chunk N+1 starts `overlap_s` before chunk N ended.
   - [x] 7c. Overlapping words appear once in the stitched text, not twice.
   - [x] 7d. Nothing is pasted or typed before the recording stops.
   - [x] 7e. `chunk_s` and `overlap_s` are config keys.

8. **Silence trimmed before the model sees it.**
   - [x] 8a. A chunk of leading/trailing silence comes back shorter.
   - [x] 8b. An all-silent chunk is skipped entirely — the model is not called.
   - [x] 8c. Speech in the middle is never cut.

## On stop

9. **Remainder only, then stitch, then clean.**
   - [x] 9a. On stop only the un-chunked tail is transcribed.
   - [x] 9b. Final text is every chunk stitched in order plus the tail.
   - [x] 9c. The cleanup pass runs once, on the stitched whole.

10. **Clipboard paste, clipboard restored.**
    - [x] 10a. Insertion writes the clipboard and sends the paste chord.
    - [x] 10b. No per-character synthetic typing on either platform.
    - [x] 10c. The clipboard's previous contents are restored afterwards.
    - [x] 10d. An empty prior clipboard restores to empty, not to the transcript.
    - [x] 10e. Restore still happens when the paste chord fails.

## Logging

11. **Per-stage milliseconds, every dictation.**
    - [x] 11a. Keypress → first audio is reported in ms.
    - [x] 11b. Each chunk's transcription is reported separately.
    - [x] 11c. The final chunk is reported separately from the rolling ones.
    - [x] 11d. The cleanup pass is reported.
    - [x] 11e. Text insertion is reported.
    - [x] 11f. The block prints after every dictation, including one that
          produced no text.

---

## Module plan

| Module | State | Role |
| --- | --- | --- |
| `timings.py` | new | Stage stopwatch and the report block. Pure. |
| `vad.py` | new | Silence trim / trailing-silence measure. Pure numpy. |
| `chunker.py` | new | Cursor over a growing buffer; emits overlapped chunks. Pure. |
| `stitch.py` | new | Joins overlapping transcripts, dropping the duplicated seam. Pure. |
| `backends.py` | new | Backend choice + the one startup log line. |
| `recorder.py` | changed | Always-open stream, rolling idle buffer, capture flag. |
| `transcriber.py` | changed | Delegates to a backend instead of owning faster-whisper. |
| `controller.py` | changed | Tap/hold modes, silence watchdog, chunk pump. |
| `paste_core.py` | changed | Clipboard save/restore around the chord. |
| `config.py` | changed | Five new keys. |
| `streaming.py` | deleted | Live typing is gone; nothing imports it. |

## New config keys

```jsonc
"tap_threshold_s": 0.3,     // 4: below this a release means "toggle"
"silence_timeout_s": 2.5,   // 6: trailing silence that auto-stops; 0 = off
"chunk_s": 2.0,             // 7: rolling window
"overlap_s": 0.25,          // 7: re-transcribed seam
"timings": true             // 11: print the block
```

## Rejected

- **Metal via faster-whisper.** Not possible: CTranslate2 4.8.1 reports only
  `{int8, float32, int8_float32}` on CPU and no GPU device on Apple Silicon. It
  has a CUDA backend and no Metal one. Verified on this machine before asking.
- **DirectML on Windows.** CTranslate2 has no DirectML backend either. Would
  mean an ONNX Runtime pipeline — a second inference engine for the benefit of
  non-NVIDIA Windows GPUs only. CUDA already covers the machine this runs on.
- **Keeping live typing behind a flag.** Two insertion paths would both need
  the clipboard rework and the timing instrumentation, for a feature
  requirement 7 explicitly removes.
- **Whole-buffer final pass.** Rejected for latency: it puts the whole
  transcription cost after the key release, which is what this rework exists to
  remove. It was *assumed* to be more accurate; measuring afterwards showed it
  is not, at least on these eight clips, so the trade turned out to be free.


---

## Outcome, 2026-08-25

Every box above is ticked and covered by a test. Suite: **1145 passed, 6
skipped**, three consecutive clean runs.

### Measured, on an M4 MacBook Air with `small`

| | Before | After |
| --- | --- | --- |
| Engine on Mac | faster-whisper, CPU | mlx-whisper, **Metal** |
| Time per pass | 1.06s (`small`, CPU) | **0.27s** |
| Keypress → first audio | PortAudio device open | ~0 ms (stream already open) |
| Work done while speaking | none (Windows) / whole-sentence re-passes (Mac) | rolling 2s chunks |
| After you stop | one pass over everything | one pass over the tail, ~200–230 ms |
| Model time vs. audio | — | 0.09–0.17x |

### The accuracy question, answered by measurement

Chunking was expected to cost accuracy, because Whisper uses up to 30s of
context. Against the eight clips in `tests/fixtures/accuracy`:

| | Mean WER |
| --- | --- |
| One pass over the whole recording | 17.2% |
| 2s chunks (the default) | 16.1% |
| 6s chunks | 14.7% |

No penalty on this sample; longer windows were slightly better still. Eight
clips is a small sample and these gaps are within noise, so the honest reading
is "no measurable penalty", not "chunking helps". `chunk_s` is a setting, and
6.0 is worth trying for long sentences.

Reproduce with `tools/pipeline_bench.py`.

### Two things this cost, both deliberate

1. **The microphone indicator now stays on** for the life of the process. The
   old design closed the stream between dictations specifically so it would go
   out, which let a user *see* the app was not listening. That is now a promise
   taken on trust. Requirement 2 asked for it; the reasoning is recorded in
   `recorder.py`'s module docstring so nobody re-derives it as a bug.
2. **`torch` is not installed**, despite `mlx-whisper` declaring it. It is
   529 MB and is only used by a checkpoint-conversion script, never at runtime.
   Install with `--no-deps` — see `pyproject.toml`'s `[metal]` extra.

### A trap this uncovered

Opening the microphone at startup means **any test that reaches a launcher
opens a real PortAudio stream**, which then outlives the test because `stop()`
deliberately no longer closes it. PortAudio's callback thread later segfaults
the whole pytest session while garbage collecting — several tests later and
nowhere near the cause, and only when the suite is run as a whole.

`tests/test_main.py` now carries an autouse fixture stubbing `_open_microphone`
for exactly this reason. Delete it and the suite starts dying with
`Fatal Python error: Segmentation fault` in a thread with no Python frame.
