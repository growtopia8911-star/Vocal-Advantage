# Acceptance walk-through — macOS

**Run `uv run python tools/acceptance_mac.py` first.** It covers 13 checks that
need no human. This file is only the part that needs eyes, a voice, and other
applications.

SPEC.md's original list was written for Windows and has drifted — it still
expects the dictated text to be left on the clipboard, which was deliberately
removed. This replaces it for the Mac.

Start it, then work down the list:

```
cd ~/Vocal-Advantage && uv run python -m vocal_advantage
```

---

## It types where you are

Same sentence each time — *"testing one two three"* — so a wrong result is
obviously wrong.

- [ ] **TextEdit** — text appears, correctly spelled, no duplicates
- [ ] **A browser text box** (this chat, a search bar) — same
- [ ] **VS Code** — same
- [ ] **Terminal** — same. Watch for dropped or reordered characters
      specifically; terminals are the fussiest target

## It stays out of the way

- [ ] **Quick tap** of the hotkey, under half a second — **nothing appears**
- [ ] **Hold it silently for five seconds**, say nothing, release — **nothing
      appears.** Not "Thank you.", not "you", nothing
- [ ] **Your clipboard survives.** Copy something first, dictate, then paste —
      you should get **what you copied**, not what you said
- [ ] **The orange microphone dot** in the menu bar comes on while you hold and
      **goes off** when you release

## The Left Cmd problem

Your hotkey is Left Cmd, which is also half of every shortcut on this machine.
The app cancels a recording the moment another key joins, so these should all
behave completely normally:

- [ ] **Cmd+C** copies as usual, nothing is dictated
- [ ] **Cmd+V** pastes as usual
- [ ] **Cmd+Tab** switches apps as usual
- [ ] But watch the orange dot while you do them — **it flickers on every
      time.** Harmless, but if it bothers you, a key you never use in
      combination is a better hotkey. `--set-hotkey` and press Right Option

## It survives real use

- [ ] **Dictate a long one** — thirty seconds or so, keep talking. Text appears
      as you go and the whole thing lands
- [ ] **Dictate twice quickly** — release, then immediately hold and speak
      again. No crash, no doubled text, no missing words
- [ ] **Close the terminal window** — the app stops, and holding the hotkey now
      does nothing at all

---

## If something fails

`logs/cleanup.jsonl` records every dictation when `ai_cleanup` is on. The
console prints `[heard Xs of audio in Ys]` after each one, and
`[dropped as silence: ...]` if the guard binned part of what you said.

For accuracy specifically, do not judge by eye — run the scorer:

```
uv run python tools/accuracy_session.py   # two minutes, eight sentences
uv run python tools/score_accuracy.py
```
