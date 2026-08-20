# Tray icon + Flow Bar — design

**Status:** agreed 2026-08-20. Completes the overlay deliberately deferred by
[`2026-08-20-macos-port-design.md`](2026-08-20-macos-port-design.md) ("**No
on-screen pill.** ... it is deferred rather than rushed"), and replaces the
Windows-only pill in `indicator_win.py`.

**Goal:** the app has no terminal window. Its presence is a menu-bar / tray
icon, plus an always-visible rounded pill showing a live waveform of my voice.
Hotkey and dictation behave exactly as they do now, on both machines.

**The one rule everything below serves:** dictation is the product, the UI is
decoration. A tray icon that fails to create, or a bar that fails to create,
must cost nothing but a warning.

---

## The constraint that decided the toolkits

Click-through, exactly as predicted. It splits the platforms, and the split is
forced rather than chosen.

**macOS has no Tk route.** `NSWindow.setIgnoresMouseEvents_(True)` is a one-line
documented API. Tk on Aqua exposes `-transparent`, `-alpha` and `-topmost` but
has no click-through attribute at all, and reaching the backing `NSWindow` from
a Tk widget is undocumented and version-fragile. So the macOS overlay is
PyObjC. `pyobjc-framework-Cocoa` is already installed as a `Quartz` dependency,
so this costs no new package.

**That decides the tray as well.** pystray's own docs: on macOS `run()` "must be
called from the main thread", and `run_detached()` "requires providing an
NSApplication instance". Since the overlay already needs an NSApplication on the
main thread, both want the same thread. A native `NSStatusItem` is ~30 lines and
removes the contention rather than negotiating with it.

**And it gets the dark/light requirement right for free.** `setTemplate_(True)`
makes macOS recolour the icon per menu-bar appearance — genuinely correct in
both, not a compromise that reads acceptably in each. pystray does not expose
template images, so under it a light menu bar would need a hand-tuned
outline-plus-fill fudge.

`rumps` was rejected: it wraps only the `NSStatusItem` half, and raw PyObjC is
required for the window regardless. It would be a dependency that saves nothing.

**Windows keeps pystray + Pillow**, as specified.

### Windows draws with `UpdateLayeredWindow`, not Tk

Tk's `-transparentcolor` is a colour key (`LWA_COLORKEY`). Colour keys are a
per-pixel yes/no test, so the pill's rounded ends come out aliased — visibly
jagged stair-steps, against a design whose whole point is that it looks
finished. Tk cannot do per-pixel alpha on Windows at all.

`UpdateLayeredWindow` can. Pillow renders the frame to RGBA, it is converted to
a premultiplied BGRA DIB section and pushed to the window. Smooth antialiased
corners and true translucency, which is what the design actually asks for.

Two things fall out of it, both good:

1. **Tk leaves the project entirely.** `tkinter`, `set_dpi_awareness`'s
   before-`tk.Tk()` ordering hazard, and the withdrawn-root trick all go.
2. **The tray icon owns the main thread on both platforms**, which is the model
   the task asked for and which Tk previously made impossible on Windows.

Cost, stated plainly: ~200 lines of new `ctypes` that cannot be run from the
Mac this is being built on. Windows is a hand-check, listed at the end.

---

## Threading

No conflict, because macOS puts both UI objects in one run loop and Windows no
longer has a main-thread-hungry toolkit.

| Thread | macOS | Windows |
| --- | --- | --- |
| main | `NSApplication`: status item + pill + 60fps `NSTimer` | pystray message loop |
| flow bar | (same run loop) | owns its window, pumps messages, renders 60fps |
| controller | background daemon — **unchanged** | background daemon — **unchanged** |
| hotkey | event tap thread — **unchanged** | `keyboard` hook thread — **unchanged** |
| audio | PortAudio thread — **unchanged** | PortAudio thread — **unchanged** |

If the tray fails to start, the main thread falls back to the
`while worker.is_alive(): sleep(0.2)` wait `_run_app_mac` already uses. Dictation
does not depend on either UI object existing.

**The waveform must never touch anything the audio thread needs.** It doesn't:
the only shared state is one `float`, written by the PortAudio callback and read
by the renderer, with no lock (see below).

---

## Audio levels, from the stream that already exists

`Recorder._callback` already copies each 1024-frame block. RMS is computed there
**outside the existing lock** and stored in a plain attribute:

```python
block = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
self._level = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
with self._lock:
    self._chunks.append(block)
```

`np.sqrt(np.mean(np.square(...)))` on 1024 float32 is a few microseconds.
Attribute assignment and read are atomic under the GIL, so the renderer polls
`recorder.level` lock-free and can never stall the callback, no matter how far
behind the UI falls. `stop()` zeroes it, so idle really is silent — the mic
stream stays closed between dictations exactly as it does today, and the
"microphone in use" light behaviour is unchanged.

No second stream. Nothing added to the controller's path.

---

## Modules

Following the existing `_win` / `_mac` convention, and the rule in
`platform_modules()` that it is the only place choosing a platform.

| File | | Purpose |
| --- | --- | --- |
| `console.py` | new | `say()` / `warn()` — see below |
| `waveform.py` | new | **all the maths, pure, fully tested** |
| `tray_icon.py` | new | Pillow icon generation, no platform code |
| `flowbar.py` | new | portable `Indicator`: state machine + thread-safe queue |
| `tray_win.py` | new | pystray |
| `tray_mac.py` | new | `NSStatusItem` |
| `flowbar_win.py` | new | `UpdateLayeredWindow` |
| `flowbar_mac.py` | new | non-activating `NSPanel` |
| `recorder.py` | edit | the `level` property above |
| `config.py` | edit | two new keys + validation |
| `main.py` | edit | `say()`, wiring, failure isolation |
| `indicator_win.py` | **delete** | superseded; its Win32 plumbing moves to `flowbar_win.py` |
| `tests/test_indicator_win.py` | **delete** | with it |
| `tools/indicator_demo.py` | **delete** | with it |

### `say()` — a correction

**`main.py` has no `say()` helper.** It has ~30 bare `print()` calls, and
`config.py` prints its hotkey warning to stderr. Under `pythonw` both
`sys.stdout` and `sys.stderr` are `None` and `print()` raises `AttributeError`,
so today the app would die on launch the moment it had no console — which is the
entire premise of this task.

It lives in `console.py` rather than `main.py` so `config.py` can use it without
an import cycle; `main.py` re-exports it, so `main.say(...)` reads as intended.

```python
def say(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is None:          # pythonw, or a .app bundle
        return
    try:
        stream.write(f"{message}\n")
        stream.flush()
    except Exception:           # a closed or broken pipe is not fatal either
        pass
```

`traceback.print_exc()` in `main._safe_call` and `controller.tick` has the same
hazard and becomes `say(traceback.format_exc(), error=True)`.

`python -m vocal_advantage` prints to the console exactly as it does now.

### `waveform.py` — the tested part

Every function pure, deterministic, no numpy-in-the-signature where avoidable:

| Function | Does |
| --- | --- |
| `block_rms(block)` | float32 block → RMS |
| `level_from_rms(rms)` | dB-mapped to 0..1 over −60..−15 dBFS, clamped |
| `Envelope.update(level)` | fast attack (α 0.5), slow release (α 0.08) |
| `centre_weights(n)` | symmetric hump, `EDGE` at the ends, 1.0 at centre |
| `bar_targets(level, n, tick)` | weights × level, plus deterministic per-bar wobble |
| `ease_bars(current, targets, α)` | one easing step toward the targets |
| `idle_heights(n)` | flat row of short dashes |
| `transcribing_heights(n, tick)` | a travelling bump — deterministic in `tick` |
| `bar_layout(width, n, bar_w, gap)` | x-centres |

The wobble is what makes it read as a soundwave rather than an arch: a fixed
per-bar phase driven by `tick`, amplitude scaled by `level`, so it is still at
silence. Deterministic in `(i, tick)` and therefore testable.

**Motion:** at 60fps, easing α 0.25 gives a ~66ms time constant — responsive but
gliding. Nothing is ever assigned to a bar height directly; every visible change
goes through `ease_bars`.

### `flowbar.py` — the state machine

Keeps the four-method protocol `controller.py` already calls, so **`controller.py`
does not change**:

- `show_recording()` → RECORDING
- `show_processing()` → TRANSCRIBING
- `hide()` → IDLE (the bar stays visible; "hidden" now means "quiet")
- `flash(msg)` → MESSAGE for 1.5s, then IDLE

All four only enqueue, as today — any thread may call them. `next_frame()` runs
on the render thread: drains the queue, polls `recorder.level`, advances the
envelope and easing, returns a `Frame`. The renderer draws the `Frame` and knows
nothing else.

`status_text()` gives the tray its non-clickable status line from the same
state, so the two UI objects can never disagree.

**`flash` widens the pill and shows the message**, then eases back. This is what
keeps `"nothing heard"` and `"could not paste - press Ctrl+V"` visible; the
three-state spec has nowhere else to put them, and losing the Ctrl+V instruction
would mean losing a recoverable dictation silently.

### Look

150×48 pill, radius 24 (half the height — fully rounded ends). 13 bars (odd, so
there is a true centre), 4px wide, 5px gaps → 112px of content, 19px margins.
Bars are `create_line`-style round-capped strokes mirrored about the centre
line: each is drawn from `cy − h` to `cy + h`, never from a baseline.

| | Pill fill | Bars |
| --- | --- | --- |
| Idle | `#0E0E10` @ 88% | white @ 55%, half-height 2px, static |
| Recording | `#0E0E10` @ 95% | white @ 100%, half-height up to 16px |
| Transcribing | `#0E0E10` @ 92% | white @ 85%, travelling bump |

---

## `config.json`

Two keys added to `DEFAULTS` in `config.py`. No new file.

```json
"flow_bar": true,
"flow_bar_position": "bottom-centre"
```

`flow_bar: false` turns the bar off entirely and keeps the tray icon.
`flow_bar_position` accepts `bottom-centre`, `bottom-left`, `bottom-right` (and
`bottom-center`, because the muscle memory is real). Anything else warns on
stderr and falls back to the default **for that run only**, leaving the file
untouched — the contract `load_config` already keeps for a bad hotkey.

---

## Launchers

| Platform | File | Notes |
| --- | --- | --- |
| Windows | `VocalAdvantage.pyw` | double-click runs it under `pythonw.exe`, no console |
| macOS | `VocalAdvantage.command` | as asked; a Terminal window does open and stay |
| macOS | `tools/make_mac_app.py` | generates `VocalAdvantage.app` — genuinely no Terminal, works in Login Items |

**The `.app` gotcha, up front:** a bundle is a new TCC identity. Accessibility
and Microphone permission must be granted to `VocalAdvantage.app` once, even
though Terminal already has them. Until that is done the hotkey is dead — which
is exactly the silent failure `hotkey_mac` already raises a loud error for.

`python -m vocal_advantage` is untouched.

---

## Failure behaviour

Each UI object is built inside its own `try` / `except Exception`. A failure
warns via `say(..., error=True)` and returns `None`; the app runs on. Losing
both leaves a working hotkey and a main thread that waits, which is precisely
today's macOS behaviour.

**Quit** stops the hotkey listener, unhooks, stops the controller thread, closes
the recorder stream (mic light out), closes the bar, flushes the cleanup log,
stops the tray loop, and returns from the run loop. No orphan process.

---

## Testing

Per `Spec + Test Driven`: test first, watched to fail, then the code.

**Tested:** everything in `waveform.py`; the `flowbar.Indicator` state machine
headless, the way `test_indicator_win.py` drove the old one; `console.say()` with
`sys.stdout` set to `None` and to a raising object; the two new config keys
including the bad-value fallback; `recorder.level` through the existing fake
`sd` seam; `tray_icon` image size, mode and transparency.

**Not tested, by instruction:** the `NSStatusItem`, the `NSPanel`, the pystray
icon, the layered window. Those are the hand-check list.

## Dependencies

`pillow` on both platforms (the icon is generated, not shipped). `pystray`
marked `sys_platform == 'win32'`. Nothing new on macOS.

## Out of scope

Any change to dictation behaviour, the hotkey, the transcriber, or the paste.
Menu items beyond the status line and Quit.
