# Vocal Advantage

Hold a key, talk, let go — what you said appears in whatever app you were
already typing in.

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
2. Hold **Right Ctrl** down. A small pill appears at the bottom of the screen
   and Windows shows its "microphone in use" indicator.
3. Talk.
4. Let go. About a second or two later the text appears where your cursor was.

Tapping the key quickly and saying nothing does nothing — that is on purpose.

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
| `device` | `"auto"` | Graphics card if there is one, otherwise CPU. |
| `min_duration_s` | `0.4` | Anything shorter than this is thrown away. |
| `max_duration_s` | `300` | Force-stops a recording you forgot about, after 5 minutes. |
| `live_typing` | `true` | **macOS only.** Type words as you speak them. Turn off to keep a larger model affordable — every live pass re-transcribes the sentence from the start. Forced off when `ai_cleanup` is on. |
| `clean_speech` | `true` | Drops "um", "uh" and stutters before anything is typed. Set `false` for the raw transcript. |
| `ai_cleanup` | `false` | An extra pass through a local Ollama model. Off: see `docs/plans/2026-08-20-speech-cleanup.md`. Pauses the live word-by-word preview when on. |

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

The live word-by-word preview re-transcribes the whole sentence on every pass,
so its cost grows with how long you talk. That is why `large-v3-turbo` is not
the default despite being the most accurate: on a CPU it cannot keep up with
speech at all, and even `small` starts lagging on long sentences. On a machine
with an NVIDIA card, raising this is worth trying.

---

## Limitations — the honest list

**Your clipboard gets replaced.** This works by putting the text on the
clipboard and pressing Ctrl+V for you, so whatever you had copied before is
gone. Putting it back is planned for a later version. (Dictated text is kept out
of Windows clipboard history and out of cloud clipboard sync.)

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
