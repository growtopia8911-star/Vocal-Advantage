# Interface design — spec

Written 2026-08-25, after `docs/superwhisper-teardown.md` and before any UI code.
The visual target is an interactive prototype, not a description:

**Prototype:** https://claude.ai/code/artifact/e0e17c38-77d1-4de3-b98a-4b9e11aedb82

Per `Spec + Test Driven`, visual work gets a fixture to eyeball rather than a
test. This is that fixture. The gates below are the part that can be checked.

---

## The decision that comes first

**This project has no widget layer, and that blocks most of what follows.**

The Flow Bar is a hand-drawn `NSPanel` (`flowbar_mac.py`) and a hand-drawn
layered window (`flowbar_win.py`). The menus are `NSStatusItem` and pystray. Tk
was removed deliberately — it owned the main thread on Windows, which is why the
tray could not have it — and two tests in `test_main.py` assert it never returns.

So every window below is built **twice, by hand, in two native APIs**, or a
toolkit comes back and reverses that. Nothing in the Settings, History or
Onboarding sections can start before this is answered.

| Option | Cost | What it gives up |
| --- | --- | --- |
| Hand-roll twice | Two implementations of every pane, forever | Nothing; matches what is already there |
| Tk returns | One implementation | The main thread on Windows, and the reason the Flow Bar was rewritten |
| Qt / wxPython | One implementation | A large dependency in a venv documented as torch-free and lean |
| Mac only, console on Windows | Half the work | Parity; Windows becomes a second-class platform |

Not decided here. It is the next thing to decide.

---

## Scope

Superwhisper's interface patterns, populated with this app's own material. Ten
screens across five plates. Roughly **40% of the researched interface is dropped**
rather than drawn and left dark — cloud model pickers, provider logos, licence
badges, everything enterprise, meetings, speaker separation, realtime streaming.
None of that machinery exists here or is planned.

Every screen carries one of three marks, and the prototype shows them:

- **BUILT** — wired to real code today
- **DATA EXISTS** — the data is already written; no UI reads it
- **NEW** — the feature does not exist

---

## The organising rule

The single transferable finding from the teardown. Depth is decided by *why* a
person is changing the setting, not by how advanced it is.

> **Tier one is what you change to fit your hands. Tier two is what you change to
> fit your machine. Tier three is what you change to fit the task.**

Applied to the twenty real keys in `DEFAULTS`. Nothing dropped, nothing invented:

| Tier | Where | Keys |
| --- | --- | --- |
| **Hands** | Configuration pane, no clicks | `hotkey`, `tap_threshold_s`, `flow_bar`, `flow_bar_position`, `sounds`, `sound_on_start` |
| **Machine** | Advanced settings, one click | `model`, `device`, `chunk_s`, `overlap_s`, `min_duration_s`, `max_duration_s`, `silence_timeout_s`, `history`, `timings` |
| **Task** | Inside a profile | `clean_speech`, `ai_cleanup`, `language`, `skip_cleanup_in` |

`flow_bar_point` gets no control. It is written by dragging, which is already
right.

The consequence worth stating: **model choice is not global.** Choosing a profile
is choosing a model. That is the strongest call in the researched design and it
is what stops the Profiles screen being decoration.

---

## Gates

Numbered so a test or a hand-check can name one.

### 1. Flow Bar control strip — BUILT + small addition

- [ ] 1a. The strip shows the active profile name.
      *Blocked on gate 6: there are no profiles. The slot shows the hotkey
      instead of inventing a profile name that would mean nothing.*
- [x] 1b. It shows the current hotkey rendered as a key cap, not prose.
- [x] 1c. It shows Stop and Cancel, each with its own key cap.
- [x] 1d. The strip is legible in all four existing states.
- [x] 1e. The pill keeps its warm paper ground and black bars. The identity does
      not change; only the legend is added.

### 2. Menu bar state dot — BUILT + small addition

- [x] 2a. Four states are distinguishable at menu-bar size: loading, recording,
      transcribing, done.
      *Shipped as idle / recording / transcribing / message, which is the
      vocabulary this app actually has -- there is no tray-visible "loading" or
      "done" state to colour. Artwork verified at 18pt on a light and a dark
      bar; the live NSStatusItem swap is not verified, see below.*
- [x] 2b. State is visible **without opening the menu**.
- [x] 2c. It survives a light and a dark menu bar.

**Not verified:** the live NSStatusItem swap. The icon would not register from
a bare-interpreter harness, and with a full menu bar and a notch that cannot be
told apart from a real fault. Worth one look while the app is running for real.

**Known trap:** `tray_mac.py` calls `setTemplate_(True)`, and macOS flattens a
template image to one colour. Either composite a non-template variant for the
non-idle states, or make state a *shape* change. Deciding which is part of 2a.

### 3. Cancel — NEW

- [x] 3a. Esc cancels a recording in progress, unconditionally.
- [x] 3b. It works with a dead-key hotkey such as `f8`, where cancel-on-other-key
      does not apply — today there is no way to abandon a dictation at all.
- [x] 3c. The recording is discarded, not transcribed.

### 4. Settings window — the three tiers

- [ ] 4a. Every key in the table above appears in exactly one tier.
- [ ] 4b. Tier one is reachable with no clicks from opening settings.
- [ ] 4c. Tier two is exactly one disclosure away, not a separate window.
- [ ] 4d. Editing a control writes `config.json` in the documented format.
- [ ] 4e. A file hand-edited while settings are open is not silently reverted —
      `_save_flow_bar_point` already establishes re-reading before writing.
- [ ] 4f. Unknown keys in an existing `config.json` are preserved untouched.

### 5. History window — DATA EXISTS

- [ ] 5a. Reads `logs/history.jsonl` without modifying it.
- [ ] 5b. Search filters the list.
- [ ] 5c. Selecting an entry shows its full text and its target app.
- [ ] 5d. Copying an entry to the clipboard works — this is the recovery path the
      file exists for.
- [ ] 5e. Per-stage timings are shown per entry.
- [ ] 5f. A corrupt line is skipped, not fatal.

**Prerequisite for 5e:** `timings.py` currently prints and discards. The stage
block must be written into the history line. That is the small change that turns
"it felt slow" into something answerable after the fact.

**Explicitly out of scope:** playback and reprocess-from-history. `history.jsonl`
stores text and no audio. Retaining audio is a privacy and disk decision and must
be taken on its own, not smuggled in as a UI feature.

### 6. Profiles — NEW

- [ ] 6a. A profile carries `clean_speech`, `ai_cleanup`, `language`, model, and
      an app-match list.
- [ ] 6b. `skip_cleanup_in` becomes a built-in profile rather than a special case.
- [ ] 6c. An existing `config.json` with a `skip_cleanup_in` list still behaves
      identically after upgrade.
- [ ] 6d. The active profile is switchable **without opening settings**.
- [ ] 6e. The active profile is visible in the Flow Bar (gate 1a).
- [ ] 6f. A manually chosen profile is not overridden by an auto-activation rule
      until the app changes.

Gates 6d–6f exist because the researched app documents all three as limitations
of its own: their mode is invisible in the mini window, and an auto-activated
mode cannot be overridden and never switches back. Free to avoid.

### 7. Words — DATA EXISTS

- [ ] 7a. Reads and writes `dictionary.json` in its existing shape.
- [ ] 7b. The `words` / `fixes` distinction is visible, not collapsed.
- [ ] 7c. A malformed file warns and falls back to doing nothing, as it does now.

### 8. Onboarding — NEW

- [ ] 8a. Runs only when no `config.json` exists.
- [ ] 8b. Captures the hotkey rather than asking the user to type a key name.
- [ ] 8c. Recommends a model from the backend actually detected, not a fixed one.
- [ ] 8d. Ends with a dictation the user performs, and confirms it round-tripped.
- [ ] 8e. Works with no GUI toolkit — console is an acceptable host for this one.

---

## Rejected

- **Replicating their settings faithfully.** The reviews were consistent that
  those settings are the one thing people complain about: "overwhelming", "like
  configuring a server", 15–30 minutes to a useful setup. The rules are worth
  copying; the surface area is the known defect.
- **A Modes system as rich as theirs.** Prompts, presets, context capture and
  per-mode cloud models. This app has one local pipeline and no prompt library.
  Profiles carry the four keys that genuinely vary and stop there.
- **Drawing the dropped 40% greyed out.** A prototype showing cloud providers
  and licence tiers would be a picture of a different product.
- **Building anything before the toolkit decision.** Every window here costs
  double or reverses the Tk removal. Guessing wrong is expensive in both
  directions.

---

## Order

The two additions that need no toolkit land first and are independently useful:

1. Gate 2 — menu bar state dot
2. Gate 1 — Flow Bar control strip
3. Gate 3 — Esc to cancel
4. **The toolkit decision**
5. Gate 5 — History window (data already exists; best value per hour)
6. Gate 4 — Settings window
7. Gate 6 — Profiles (needs 4)
8. Gate 7 — Words (needs 4)
9. Gate 8 — Onboarding (can run earlier; console-hosted)
