# macOS port — design

**Status:** agreed 2026-08-20. Supersedes nothing; `SPEC.md` line 62 already
commits to this shape ("the `_win` files are the Mac seam: v1.0 adds `_mac`
twins behind the same function signatures").

**Goal:** hold a key, speak, release, and have the words appear in whatever
macOS app has focus — on the same codebase that runs on Windows, with the
Windows behaviour unchanged.

**Scope of this pass:** the key hook and the paste. **No on-screen pill.** The
indicator becomes a no-op object satisfying the same four methods, so
`controller.py` does not change at all. The pill needs a non-activating
`NSPanel` to avoid stealing focus, and that is the one part most likely to fail
silently and paste into the wrong window; it is deferred rather than rushed.

---

## What already works on macOS

Verified this session, not assumed:

| Module | Status |
| --- | --- |
| `recorder.py` | real microphone capture, 16kHz mono float32 |
| `transcriber.py` | `large-v3-turbo` on `cpu/int8`; CUDA→CPU fallback exercised |
| `config.py`, `hotkey_spec.py` | portable already |
| `controller.py` | pure Python, no OS calls |

So the port is genuinely two files plus wiring.

---

## Hardware findings — measured, not documented

Observed with a listen-only `CGEventTap` on a MacBook Air (M4):

```
flagsChanged   keycode=61   RIGHT option    flags_on=['option']     <- press
flagsChanged   keycode=61   RIGHT option    flags_on=[]             <- release
flagsChanged   keycode=58   LEFT option     flags_on=['option']
keyDown        keycode=0    (the letter a)  flags_on=[]
```

Three things follow, and they **invert** the Windows trap:

1. **Modifiers do not emit key-down/key-up on macOS.** They emit
   `flagsChanged`. Anything expecting `keyDown` for Right Option sees nothing,
   forever, silently.
2. **Left and right modifiers *are* distinguishable** (61 vs 58). On Windows the
   left-hand modifiers arrive unsided as plain `"ctrl"`, which needed the whole
   `_EQUIVALENTS` table. macOS is cleaner here.
3. **Direction comes from the flag bit**, not the event type: press sets the
   modifier's mask bit, release clears it.

**The one ambiguity:** hold both Options, release one, and the `option` flag is
still set — the flag alone cannot say which went up. Resolved with
`CGEventSourceKeyState(kCGEventSourceStateHIDSystemState, keycode)`, the exact
twin of Windows' `GetAsyncKeyState`, and therefore of `read_pressed_keys`.
Confirmed available.

**Accessibility permission** is required or `CGEventTapCreate` returns `None`.
Already granted here. A missing permission must produce a loud, specific error
naming System Settings → Privacy & Security → Accessibility — never a silent
dead key.

---

## Changes

### 1. `hotkey_events.py` (new, portable) — do this first

`normalise_key_name`, `spec_key_for`, `Edge` and `EdgeDetector` are pure logic
with no Windows in them; they are merely stranded in a `_win` file. They move
here and both platforms import them. `VK_CODES` and `read_pressed_keys` stay in
`hotkey_win.py`, because those genuinely are Windows.

`hotkey_win` re-exports the moved names, so every existing import and all 58 of
its tests keep working untouched. Without this the Mac would need a duplicate
copy of the trickiest logic in the project.

### 2. `paste_mac.py`

Mirrors `paste_win`'s contract exactly: `paste_text(text) -> bool` and an
`injection_active` event.

- Clipboard via `NSPasteboard`, including the `org.nspasteboard.ConcealedType`
  marker — the community convention for "clipboard managers, do not record
  this", and the analogue of the three Windows privacy formats.
- `⌘V` injected with `CGEventCreateKeyboardEvent` + `CGEventPost`.
- Waits for physically-held modifiers to release first, exactly as on Windows:
  injecting `⌘V` while Right Option is still down produces `⌥⌘V`.

**This half is simpler than Windows, for a real reason.** On Windows we could
not tell our own injected Ctrl+V from the user's keystrokes, so the hook was
gated for the whole paste and then resynced from `GetAsyncKeyState`. macOS lets
us stamp synthetic events with a magic user-data field
(`kCGEventSourceUserData`), so the tap recognises and skips exactly our own
events. That removes a class of race rather than working around it — and it is
the specific capability a `pynput`-style abstraction would have hidden, which is
why we bind Quartz directly.

### 3. `hotkey_mac.py`

Provides both `HotkeyListener(spec, on_event, *, gate=None)` and
`capture_hotkey(...)` — the latter because `--set-hotkey` must work here too, or
choosing your own key is Windows-only.

Decodes `flagsChanged` per the findings above, maps macOS keycodes onto the
existing shared vocabulary (Right Option → `"right alt"`, since macOS Option
*is* Alt), and reuses `EdgeDetector` unchanged.

Tap is created **listen-only** — never suppressing, the same rule as Windows.

### 4. `main.py`

A platform switch selects `_win` or `_mac`. On macOS there is no tkinter at all;
the main thread runs a **CFRunLoop**, which the event tap requires in order to
deliver events. The controller thread and its queue are unchanged.

### 5. Default hotkey

`right ctrl` does not exist on MacBook keyboards. The macOS default is **Right
Option** (`"right alt"`). One shared key vocabulary means `config.json` stays
portable between the two machines. Changeable with `--set-hotkey`, as on Windows.

---

## Testing

Same seam as the rest of the project: Quartz sits behind a fake, exactly as
`FakeUser32`/`FakeKernel32` do for Windows, so the sequencing is tested with no
permission and no keyboard. Unlike the Windows work, the manual checks can
actually be run here.

## Dependency

`pyobjc-framework-Quartz`, marked `sys_platform == 'darwin'`. 6.9 MB installed.
`pynput` was rejected: it pulls the same pyobjc stack anyway (+92 KB, so size is
not the argument) and hides the event tap we need direct access to.

## Out of scope

The pill, and any change to Windows behaviour.
