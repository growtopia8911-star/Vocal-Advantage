# Vocal Advantage

Tap a key, talk, tap it again — what you said appears in whatever app you were
already typing in. Or hold the key down and let go when you finish; the same
key does both.

Everything happens on this PC. Your voice never leaves the machine, there is no
account, and nothing is uploaded. After setup it works with the internet off.

**Default key:** Right Ctrl — the Ctrl key on the right of the space bar.

---

## What you need

- Windows 11
- An NVIDIA graphics card (it does the listening; without one it falls back to
  the CPU, which works but is slower)
- Python 3.12
- About 2 GB of free disk space

You do **not** need administrator rights.

---

## One-time setup

Open PowerShell in this folder and run these four lines, one at a time, waiting
for each to finish:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

Line 2 puts `(.venv)` at the start of your prompt. That is how you know the next
two lines will install into this project instead of your whole computer.

> **If line 2 says "running scripts is disabled on this system":** run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer `Y`, then
> try line 2 again. (Or use `.\.venv\Scripts\activate.bat` instead, which is not
> affected.)

The last line takes a few minutes. It is downloading the NVIDIA support
libraries as well as the program's own bits.

---

## Running it

Every time, in this folder:

```powershell
.\.venv\Scripts\Activate.ps1
python -m vocal_advantage
```

**The first run downloads about 141 MB** — the speech model. It goes into your
user cache folder and only happens once. Progress prints in the window; it is
usually well under a minute, and the window tells you the size before it starts.

When you see `Ready.`, it is listening for your hotkey.

**Leave that window open.** Closing it is how you quit.

### Using it

1. Click into the app you want the text to land in — Notepad, a browser box,
   Slack, anything.
2. Press the hotkey. A small pill appears at the bottom of the screen.
3. Talk.
4. Stop, one of two ways:
   - **Tap** the key again. (This is what happens if your first press was a
     quick tap — under `tap_threshold_s`, 0.3s by default.)
   - **Let go**, if you held the key down past that threshold.

   You do not have to decide in advance. Press and release quickly and it
   toggles; press and hold and it behaves like a walkie-talkie.

The text appears a fraction of a second after you stop, because most of the
transcribing already happened while you were talking — see "How fast it is".

If you forget a toggled recording, it stops itself: after
`silence_timeout_s` of quiet (2.5s), or `max_duration_s` (5 minutes),
whichever comes first. Either way what you said is still transcribed and
pasted, never thrown away.

**The microphone is open the whole time the app is running**, so your OS shows
its "microphone in use" indicator continuously. That is the price of the key
responding instantly — opening the device on the keypress used to clip the
start of the first word. Audio recorded while you are not dictating goes into a
two-second buffer that is thrown away, never transcribed and never written
anywhere.

---

## Changing the hotkey

```powershell
python -m vocal_advantage --set-hotkey
```

It asks you to hold the key (or combination of keys) you want, then release.
It says back in plain English what it heard, saves it, and you restart the app.

If it refuses your key it tells you why and asks again, and your existing
setting is left alone.

You can also open `config.json` in Notepad and edit the `"hotkey"` line by hand.
If you typo it, the app starts anyway, prints a warning, and falls back to Right
Ctrl.

### Which keys work well

| Key | What to expect |
| --- | --- |
| `f8` or `f9` | No side effects at all. The safest choice. |
| `right ctrl` (default) | While you hold it, mouse clicks become Ctrl+clicks. |
| `right alt` | On some non-US keyboard layouts this types accented characters. |
| `scroll lock` or `pause` | Fine — but most laptop keyboards do not have them. |
| `ctrl+win` | Safe. This is what LocalFlow and Wispr Flow default to. |
| `win` on its own | **Refused.** Letting go would open the Start menu every time. |
| `caps lock` | **Refused.** Making it usable needs key-blocking, and key-blocking is where all the crashes live. |

With a plain modifier key like Right Ctrl, pressing any other key while
recording cancels the recording — so **Right Ctrl+C still just copies**. With a
key that does nothing on its own, like F8, that rule is off, so you can type
while you dictate.

---

## Settings (`config.json`)

Created automatically the first time you run it, in this folder. It is not
shared through git — it is your machine's settings.

| Setting | Default | What it means |
| --- | --- | --- |
| `hotkey` | `"right ctrl"` | The key you hold to talk. |
| `language` | `"en"` | English. Fixed, so it never has to guess. |
| `model` | `"small"` | Which speech model to use. See the table below. |
| `device` | `"auto"` | Best available: Metal on Apple Silicon, CUDA on an NVIDIA card, otherwise CPU. Force one with `"metal"`, `"cuda"` or `"cpu"`. The chosen backend is printed at startup. |
| `min_duration_s` | `0.4` | Anything shorter than this is thrown away. |
| `max_duration_s` | `300` | Force-stops a recording you forgot about, after 5 minutes. |
| `tap_threshold_s` | `0.3` | Release the key faster than this and it toggles; hold longer and it stops when you let go. |
| `silence_timeout_s` | `2.5` | Auto-stops after this much silence. `0` turns it off. Silence before you have said anything does not count. |
| `chunk_s` | `2.0` | How much audio is transcribed at a time while you speak. Bigger is more accurate and leaves more work for the moment you stop — see "How fast it is". |
| `overlap_s` | `0.25` | How far each chunk reaches back into the last one, so a word on a boundary is not lost. |
| `timings` | `true` | Print the millisecond breakdown after every dictation. |
| `clean_speech` | `true` | Drops "um", "uh" and stutters before anything is typed. Set `false` for the raw transcript. |
| `ai_cleanup` | `false` | An extra pass through a local Ollama model. Off: see `docs/plans/2026-08-20-speech-cleanup.md`. |

### Choosing a model

Bigger models hear better and cost more time. These are measured on an M4
MacBook Air CPU against a 2.9-second clip, so a graphics card will be several
times quicker:

| `model` | Download | Time per pass | Speed vs. speech |
| --- | --- | --- | --- |
| `tiny` | 75 MB | 0.16s | 17x faster |
| `base` (default) | 141 MB | 0.29s | 10x faster |
| `small` | 464 MB | 1.06s | 2.7x faster |
| `large-v3-turbo` | 1.5 GB | 5.18s | **slower than speaking** |

On Apple Silicon these are the CPU numbers, and the CPU is no longer what runs
the model — see below.

---

## How fast it is

Transcribing does not wait for you to stop talking. While you speak, each
`chunk_s` of audio is trimmed of silence and transcribed as it arrives, and the
results are kept internally (never shown, never typed into your document — a
word that appears and then changes is worse than one that appears a moment
later). When you stop, only the leftover tail still needs transcribing.

Measured on an M4 MacBook Air with `small` on Metal:

| Stage | Typical |
| --- | --- |
| Keypress → first audio | ~0 ms (the stream is already open) |
| One 2s chunk | ~200–260 ms |
| Final chunk on stop | ~200–230 ms |
| Cleanup pass (rules) | <1 ms |
| **Model time vs. audio length** | **0.09–0.17x** |

So a five-second sentence costs roughly 450 ms *after* you stop, most of which
is the final chunk — not the two-plus seconds a single pass over the whole
recording would take.

Every dictation prints this breakdown to the console. Turn it off with
`"timings": false`.

### Apple Silicon runs on the GPU

`faster-whisper` (CTranslate2) has a CUDA backend and **no Metal one** — on an
M-series Mac it reports zero GPU devices. So Metal needs a second engine,
`mlx-whisper`, and the app picks it automatically when it is installed:

```
pip install --no-deps mlx-whisper
pip install mlx numba scipy tiktoken huggingface_hub more-itertools tqdm
```

`--no-deps` is deliberate: `mlx-whisper` declares `torch` (529 MB) as a
dependency but never imports it at runtime — only its checkpoint-conversion
script uses it. `pip install -e ".[metal]"` works too and costs you the 529 MB.

Without it the Mac runs on the CPU and everything else behaves identically. The
startup line tells you which you got:

```
Speech backend: mlx-whisper on Metal (Apple GPU) [metal/float16]
```

Metal makes `small` cost about what `base` used to on the CPU (0.27s vs 0.30s
per pass), so you get the more accurate model for free.

### Does chunking hurt accuracy?

It was expected to — Whisper uses up to 30 seconds of context, so short windows
should hear less well. Measured against the eight clips in
`tests/fixtures/accuracy`, word error rate came out:

| | Mean WER |
| --- | --- |
| One pass over the whole recording | 17.2% |
| 2s chunks (default) | 16.1% |
| 6s chunks | 14.7% |

So it did not, on this sample — chunking was slightly *better*, and longer
windows better still. Eight clips is a small sample and the differences are
within noise, so treat this as "no measurable penalty" rather than "chunking
improves accuracy". If you talk in long sentences, `"chunk_s": 6.0` is worth
trying: it leaves a little more work for the moment you stop.

---

## Limitations — the honest list

**Your clipboard is borrowed, then given back.** This works by putting the text
on the clipboard and pressing Ctrl+V (Cmd+V on a Mac) for you. Whatever you had
copied is saved first and put back a quarter of a second after the paste. Two
caveats: only *text* is restored, so a copied image does not survive a
dictation, and if the clipboard cannot be read at that moment it is left alone
rather than blanked. (Dictated text is kept out of Windows clipboard history,
out of cloud clipboard sync, and marked so macOS clipboard managers skip it.)

**The microphone indicator stays on.** The stream is opened at startup and kept
open so the hotkey responds instantly, which means your OS shows the app as
using the microphone the whole time it runs. Audio recorded outside a dictation
is discarded — but you are taking that on trust rather than reading it off the
screen, which was not true of earlier versions.

**Windows running as administrator cannot receive it.** Windows blocks ordinary
programs from typing into elevated ones — Task Manager, some installers. Nothing
will appear. The text is still on your clipboard though, so click into the
window and press Ctrl+V yourself.

**The first run downloads the speech model.** One time only, and the window
prints the size before it starts.

**It does not start by itself.** You launch it, and it stops when you close the
window. Starting with Windows is a later version.

**Only one copy at a time.** Launch a second one and it exits immediately with a
message — two copies would paste everything twice.

**Games usually will not see it.** Games that read the keyboard directly are out
of scope.

---

## If something goes wrong

| What you see | What it means |
| --- | --- |
| "Vocal Advantage is already running" | There is another window of it open somewhere. Find and close it. |
| Nothing pastes, but Ctrl+V works | The target window is running as administrator. See above. |
| `cublas64_12.dll not found` | The NVIDIA support packages did not install. Re-run the `pip install -e .` line. |
| A warning about your hotkey at startup | There is a typo in `config.json`. It fell back to Right Ctrl and is still working. |
| It says "nothing heard" | The microphone picked up nothing usable. Check Windows' sound settings picked the right mic. |

---

## Quitting

Close the console window. The key stops doing anything and the microphone goes
off.
