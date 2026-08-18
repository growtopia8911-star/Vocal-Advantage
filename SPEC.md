# Vocal Advantage — SPEC v0.1

Hold **Right Ctrl** → speak → release → the words appear in whatever app has
focus. 100% local: audio never leaves the machine, no account, no admin rights.

This file is the source of truth for v0.1 scope and design. The build follows
it; the acceptance checklist at the bottom decides when v0.1 is done.

---

## Scope

**In:** global hold-to-talk hotkey (**user-configurable**, with a
press-your-key setup command), mic capture, local transcription
(faster-whisper on the NVIDIA GPU), paste into the focused app, a tiny
recording indicator, a config file.

**Out (deliberately — later versions):** cleanup/LLM pass (v0.2), personal
dictionary (v0.3), app-aware tone (v0.4), clipboard save-and-restore, sounds,
tray icon, auto-start, settings UI (v0.5), Mac (v1.0), games, admin-elevated
apps.

---

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Language/runtime | Python 3.12 (already installed), venv, **no torch ever in this venv** | torch is unneeded (VAD runs on onnxruntime) and causes the OMP #15 DLL crash |
| Hotkey | **user-configurable**; single key or combo; hold-to-talk; default **Right Ctrl**; never suppressed | Kevin's pick as default. Suppression is where all the keyboard-lib bugs live (#442/#666), so we never suppress — which also constrains which keys are allowed (below) |
| Choosing the hotkey | `python -m vocal_advantage --set-hotkey` — prompts, captures the next chord, writes `config.json` | Typing key-name strings by hand is the error-prone part; capture removes it. The file stays hand-editable |
| Hotkey library | `keyboard==0.13.5`, via raw `keyboard.hook()` | Proven in LocalFlow; pure ctypes, no admin. A raw all-events hook is needed anyway (combo tracking + cancel-on-other-key), so `on_press_key` is not used |
| STT | `faster-whisper==1.2.1`, model **large-v3-turbo**, `device=cuda`, `compute_type=int8_float16` | ~1.5–2GB of the 6GB VRAM, leaves headroom; 1–2s for a 10–30s utterance. Fallback chain: cuda/int8_float16 → cpu/int8 (warn on console) |
| Language | pinned `language="en"` | Kevin's pick — skips per-utterance language detection |
| Audio | `sounddevice` InputStream, 16kHz mono float32, blocksize 1024, `WasapiSettings(auto_convert=True)` | Feeds Whisper directly as numpy — **no ffmpeg, no PyAV decode**. Stream opened only while recording so the Windows mic indicator tells the truth |
| Text injection | clipboard + synthetic **Ctrl+V** via SendInput | Typing char-by-char breaks in Windows Terminal (#12977) and on non-US layouts. Transcript **stays on the clipboard** afterwards (restore is v0.5) |
| Indicator | one tkinter pill, bottom-center, no-focus window styles | Must never steal focus or the paste lands in the wrong window |
| Config | `config.json` at repo root, gitignored, created with defaults on first run | hotkey/language/model are per-machine choices |

Pinned deps: `faster-whisper==1.2.1`, `ctranslate2==4.8.1`, `nvidia-cublas-cu12`,
`nvidia-cudnn-cu12==9.*` (both ship win_amd64 wheels now — the faster-whisper
README calling the pip route "Linux only" is outdated), `sounddevice==0.5.6`,
`keyboard==0.13.5`, `pywin32`, `numpy`.

---

## Architecture

```
vocal_advantage/
  main.py            entry point: wiring, state machine, shutdown
  config.py          load/create config.json, defaults        (portable)
  hotkey_spec.py     parse/validate/normalise a hotkey string (portable)
  recorder.py        mic capture -> 16kHz float32 numpy       (portable)
  transcriber.py     faster-whisper wrapper + output guards   (portable)
  hotkey_win.py      the key hook: combo tracking, capture    (Windows-only)
  paste_win.py       clipboard + SendInput Ctrl+V             (Windows-only)
  indicator_win.py   the pill overlay                         (Windows-only)
  cuda_dlls.py       NVIDIA DLL path wiring                   (Windows-only)
```

The `_win` files are the Mac seam: v1.0 adds `_mac` twins behind the same
function signatures; everything else ports unchanged.

**State machine** (in `main.py`):

```
IDLE --keydown--> RECORDING --keyup--> PROCESSING --paste done--> IDLE
```

- "keydown" means **the last key of the configured combo went down while the
  rest are already held**; "keyup" means **any key of the combo went up**.
- Key-downs within 30ms of the last, or while already RECORDING (OS
  autorepeat), are ignored.
- **Cancel-on-other-key applies only when the hotkey is (or contains) a bare
  modifier.** With Right Ctrl, pressing any non-combo key during RECORDING
  cancels it — the user was typing Right Ctrl+C, not dictating; the shortcut
  still reaches the app because we never suppress. With a dead key (F8) the
  rule is off, so typing while dictating is allowed.
- Recording shorter than `min_duration_s` (0.4s) is discarded silently.
- A 300s watchdog force-stops a forgotten recording and processes it.
- Key events during PROCESSING are ignored (no double-start, no double-paste).

**Threading:** hotkey hook callbacks only enqueue events to a single queue; one
controller thread consumes it (serialization prevents the key-up-beats-key-down
race LocalFlow hit). Audio callback appends chunks under a lock. Transcription
runs on the controller thread. tkinter mainloop owns the main thread; the
controller talks to it via a UI queue polled with `root.after` (tkinter is not
thread-safe).

---

## Critical mechanics (expensive to rediscover — do not improvise here)

**CUDA DLL wiring** (`cuda_dlls.py`, runs before `faster_whisper` is imported):
set `KMP_DUPLICATE_LIB_OK=TRUE` and `OMP_NUM_THREADS=4`; call
`os.add_dll_directory()` on `site-packages/nvidia/cublas/bin` and
`.../cudnn/bin` and prepend both to `PATH`. Python 3.8+ ignores PATH for DLL
resolution — skipping this yields "cublas64_12.dll not found" /
"cudnn_ops64_9.dll not found".

**Model lifecycle:** load once at startup, keep resident (~2GB VRAM is the
price of 1–2s latency). First `transcribe()` pays ~1–3s CUDA init — warm up
with 0.5s of zeros at startup. First run ever downloads ~1.6GB to
`~/.cache/huggingface/hub` — print progress so it doesn't look hung.

**Transcribe call:** `language="en"`, `beam_size=1` (LocalFlow's WER benchmark:
beam 1 matched beam 5 for dictation), `vad_filter=True`,
`vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400)` (the
library default of 2000ms adds latency), `condition_on_previous_text=False`
(kills repetition loops), `without_timestamps=True`. The return is a **lazy
generator** — consume it immediately or nothing runs.

**Hallucination guards** (Whisper invents "Thank you." on silence), in order:
1. `min_duration_s` 0.4 — short taps never reach the model.
2. VAD filter strips silence before the model sees it.
3. Drop segments where `no_speech_prob > 0.6` **and** `avg_logprob < -1.0`.
4. Empty result → no paste, pill flashes "nothing heard".

**Paste sequence** (`paste_win.py`):
1. Set `injection_active` flag — the hotkey hook ignores events while it's set.
2. Wait (max 2s) until `GetAsyncKeyState` shows Ctrl/Shift/Alt/Win all
   released — physically held modifiers combine with injected keystrokes.
3. Set clipboard `CF_UNICODETEXT` with a retry loop (5x50ms — `OpenClipboard`
   races with Win+V clipboard history, WinError 5). Also register
   `ExcludeClipboardContentFromMonitorProcessing`,
   `CanIncludeInClipboardHistory=0`, `CanUploadToCloudClipboard=0` so
   dictations stay out of clipboard history and cloud sync (privacy is the
   product).
4. Sleep ~100ms (apps fetch the clipboard lazily).
5. SendInput: **Left** Ctrl down, V down, V up, Ctrl up, ~20ms apart.
6. Sleep ~60ms, clear `injection_active`, and **resync the held-key set** from
   `GetAsyncKeyState` before accepting events again.

Step 1 is the load-bearing guard, not step 5: since the hotkey is
user-configurable, the user may well choose Left Ctrl, so "we press a
different Ctrl than you do" cannot be relied on. `injection_active` must gate
the hook for the whole sequence, and the resync in step 6 stops our own
injected Ctrl from being left in the held-key set (which would otherwise make
the next dictation start instantly or never).

**Indicator** (`indicator_win.py`): one Toplevel created at startup —
`overrideredirect(True)`, `-topmost`; after first map get the real HWND via
`GetParent(root.winfo_id())` (winfo_id is the inner child) and add
`WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` to `GWL_EXSTYLE`. Show/hide only via
`SetWindowPos` with `SWP_NOACTIVATE` — `deiconify` activates and would steal
focus from the paste target. Re-assert topmost on each show.
`SetProcessDpiAwareness(2)` at startup. States: red dot = recording, animated
dots = processing, brief flash = nothing heard / error.

**Misc:** single-instance mutex (`CreateMutexW`, name
`Local\VocalAdvantageSingleInstance`) — two instances double-paste. On audio
stream error, re-initialize PortAudio and reopen on next recording. Quit = close
the console window; shutdown handler unhooks the keyboard hook and closes the
stream.

**Known Windows facts** (documented behavior, not bugs to fix): a non-elevated
process cannot paste into an admin-elevated window (UIPI) — the text stays on
the clipboard for manual Ctrl+V. Games polling raw input are out of scope.
Mouse clicks while holding Right Ctrl are Ctrl+clicks — inherent to a bare
modifier hotkey, accepted.

---

## The hotkey setting

**Format:** a `keyboard`-library key name, or several joined by `+` for a
combo — `"right ctrl"`, `"f8"`, `"ctrl+win"`, `"ctrl+alt+space"`. Case and
spaces around `+` are normalised.

**Setting it:** `python -m vocal_advantage --set-hotkey` prints "hold the key
or combo you want, then release", captures the largest set of keys held
simultaneously, echoes it back in plain English, validates it, and writes
`config.json`. Hand-editing the same field is equally supported.

**Validation** (`hotkey_spec.py`, shared by startup and the capture command):

| Rule | Behavior |
| --- | --- |
| Unknown key name | Refuse. On startup: warn loudly, fall back to `right ctrl`, keep running. During capture: refuse to save, re-prompt |
| Bare `win` (alone) | Refuse — releasing it opens the Start menu every time. `ctrl+win` etc. are fine |
| `caps lock` | Refuse — only usable if suppressed, and suppression is the bug-ridden path we've excluded |
| Anything else | Accept |

**README table** of practical choices, from the research: `f8`/`f9` — no side
effects at all; `right ctrl` (default) — mouse clicks become Ctrl+clicks while
held; `right alt` — types accented characters on some non-US layouts;
`scroll lock`/`pause` — absent from most laptop keyboards; `ctrl+win` — what
LocalFlow and Wispr Flow default to, safe unsuppressed.

---

## Config defaults (`config.json`)

```json
{
  "hotkey": "right ctrl",
  "language": "en",
  "model": "large-v3-turbo",
  "device": "auto",
  "min_duration_s": 0.4,
  "max_duration_s": 300
}
```

Written with defaults on first run. Unknown keys in the file are preserved;
missing keys are filled from defaults.

---

## Test plan

**Automated (pytest, written first, watched to fail first):**
- Output guards: segment combos of `no_speech_prob`/`avg_logprob` → correctly
  kept/dropped (pure logic, mocked segments).
- Min-duration rule: 0.3s of audio → skipped; 0.6s → transcribed (mocked model).
- State machine: fake event sequences — down/up; down/autorepeat-down/up;
  other-key-during-recording (cancels with a modifier hotkey, does *not*
  cancel with `f8`); up-during-processing (ignored); events arriving while
  `injection_active` (ignored) — produce exactly the right actions.
- Hotkey parsing/validation: `"Right Ctrl"`/`"right ctrl"`/`"RIGHT CTRL"` all
  normalise the same; `"ctrl + win"` → combo of two; `"nonsense"` → rejected;
  `"win"` → rejected; `"caps lock"` → rejected; combo fires only when *all*
  parts are held, and releasing *any* part ends it.
- Config: missing file → defaults created; partial file → defaults filled in;
  invalid hotkey in file → falls back to `right ctrl` and warns.
- Slow/manual-run integration test: fixture WAV "testing one two three" →
  transcript contains "testing" (needs model + GPU; not in the default run).

**Manual (the parts that physically press keys have no honest automated test):**
the acceptance checklist below.

---

## Acceptance checklist — v0.1 is done when every box ticks

- [ ] `python -m vocal_advantage` starts without admin; no pill visible; the
      Windows mic-in-use indicator is OFF while idle
- [ ] Hold Right Ctrl → pill appears (~0.2s), mic indicator ON
- [ ] Say "testing one two three", release → the text appears in focused
      Notepad within ~2.5s; pill gone; mic indicator OFF
- [ ] Same dictation lands correctly in: a browser text box, VS Code, and
      Windows Terminal (PowerShell)
- [ ] Quick tap of Right Ctrl (<0.4s) → nothing appears anywhere
- [ ] Hold Right Ctrl silently for 5s → nothing appears (no "Thank you.")
- [ ] Right Ctrl+C still copies (recording cancels on the C press, no paste)
- [ ] `--set-hotkey`, hold F8, release → it echoes "F8" and saves; restart →
      F8 now dictates and Right Ctrl does nothing
- [ ] With F8 set: hold F8 and type on the keyboard while talking → recording
      is *not* cancelled (the modifier-only rule)
- [ ] `--set-hotkey`, hold Ctrl+Win → saved as a combo; both keys must be held
      to record, releasing either one ends it
- [ ] `--set-hotkey`, press CapsLock → refused with a plain-English reason,
      config unchanged
- [ ] Hand-edit `config.json` to `"hotkey": "nonsense"` → app starts, warns
      clearly, and works on Right Ctrl
- [ ] Set the hotkey to **Left Ctrl** (the self-collision case), dictate →
      text pastes once, and the next dictation still starts and stops normally
- [ ] Pressing Right Ctrl while a previous dictation is still processing:
      no crash, no doubled text
- [ ] After dictating, the clipboard contains the dictated text (documented
      v0.1 behavior)
- [ ] Second launch while running: exits immediately with a clear message
- [ ] Close the console window → holding Right Ctrl does nothing, mic stays off
- [ ] Idle: ~0% CPU (GPU memory stays allocated — accepted cost of fast starts)

---

## References

- github.com/nexos-1/localflow — Python reference; hotkey/paste/overlay/guard
  mechanics largely adopted from here
- github.com/cjpais/Handy + handy-keys — state machine, debounce, VAD design
- github.com/SYSTRAN/faster-whisper — STT engine
- espanso docs — paste timing values; microsoft/terminal#12977 — why not
  synthetic typing
