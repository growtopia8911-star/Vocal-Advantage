# Superwhisper teardown

A design study of Superwhisper's interface, for deciding what Vocal Advantage
should grow next. Written 2026-08-25.

**Screenshots:** `design-research/superwhisper/` (gitignored)
**Their app version at time of writing:** 2.18.1 (macOS), released 2026-08-20
**Their docs:** built on Mintlify, 57 pages, all reachable from `/docs`

---

## What I actually looked at

This matters for reading the rest, so it goes first.

| Source | Count | Result |
| --- | --- | --- |
| Top-level pages (`/`, `/docs`, `/changelog`) | 3 | captured |
| Docs pages from `/docs/sitemap.xml` | 57 | captured, 0 failures |
| Full-page screenshots in `design-research/superwhisper/` | 60 | all succeeded |
| App-UI images embedded in those pages, pulled at native resolution into `assets/` | 107 | all succeeded |
| Embedded images I opened and studied individually | ~35 | see below |

**Playwright MCP was not available in the session this was written in.** The
server had just been added to `~/.claude.json` and MCP servers only connect at
session start. Rather than stall, the captures were taken with the Playwright
CLI (`npx playwright screenshot --full-page`) driving the same Chromium build.
Same browser, same output, different driver.

**The full-page screenshots are close to useless for judging the app's UI, and
the `assets/` folder is where the real evidence is.** A docs page renders 1,700–
3,000px-wide app screenshots inside a 1,440px column and the pages run up to
8,600px tall, so the app interface inside them is illegible at page scale. Every
visual claim below comes from an image in `assets/` opened at native resolution.

**Caveats on what I saw:**

- **The docs images lag the app.** Their in-image version strings read
  `1.45.5-rc4`, `1.46.0`, and `2.2.0` depending on the page, against a current
  release of `2.18.1`. The 2.0 and 2.13 releases both redesigned settings and
  Modes, so some layouts below are one or two redesigns behind what ships today.
- **Everything I saw is dark.** Light and system themes exist (shipped 2.16.0,
  2026-06-09), but no doc image shows them, so I can say nothing about their
  light palette.
- **I never saw the Home tab.** It is the first item in the settings sidebar and
  no docs image shows its contents. From changelog entries ("negative time saved
  showing on homepage stats", "Stat share card feature") it holds usage stats,
  but I did not look at it and am not describing it.
- **I did not see the onboarding screens** beyond the first window. The docs
  describe the steps in prose; only the opening "Get Started" panel is pictured.
  The onboarding section below says which parts are seen and which are text.
- The `/docs` URL redirects to `/docs/get-started/introduction` — those two
  captures are byte-identical, so there are 59 distinct pages, not 60.
- One primary source failed: the Medium review "I Spent a Week in Superwhisper's
  Settings" returned **HTTP 403**. Its findings are not used.

---

## The screens

### 1. Recording window (main)

The centre of the product. A single rounded-rectangle panel, floating, roughly
3:1 landscape, split into two horizontal bands.

**Upper band — the waveform.** Near-black, taller than the lower band, holding a
symmetrical bar-style waveform centred on a horizontal axis. Bars are thin,
white, evenly spaced with a gap roughly equal to bar width, amplitude mirrored
above and below centre. It reads as a hi-fi VU meter rather than a scientific
plot. Two controls sit in this band, both very low contrast until hovered:

- **top-right:** a resize toggle — two arrows pointing inward at each other —
  switching between main and mini view
- **bottom-left:** a context-capture indicator, a small dashed-bracket
  "scan text" glyph, which lights up when clipboard or selected-text context has
  been captured

**Lower band — the control strip.** Slightly lighter than the waveform band,
separated by a hairline. Left to right:

- a small **status dot**, colour-coded (blue in the images I saw)
- the **active mode name** ("Voice") as plain text
- the **mode-switch shortcut** rendered as a key cap (`^⇧Z`) in a rounded
  dark chip
- — a wide gap —
- **Stop** + its key cap (`⌥⌘^0`)
- a thin vertical divider
- **Cancel** + its key cap (`Esc`)

Every action in the strip is a **label + its keyboard shortcut, side by side**.
The shortcut is not hidden in a tooltip or a settings page; it is on the control,
permanently, at nearly the same visual weight as the label. Hover fills a
rounded pill behind the item — this is how you learn the strip is clickable at
all, because at rest there are no button borders anywhere.

The mode name doubles as a button: clicking it opens the mode switcher.

### 2. Recording window (mini)

A small rounded pill. Two states seen:

- **idle/collapsed:** just a tiny waveform stub, a few bars wide, in a dark pill
  maybe 120px across
- **recording:** the pill widens; a red rounded-square button carrying the
  Superwhisper triangle mark sits at its left, with a live waveform to its right

On hover (idle) or after a result arrives, the pill is replaced by a
**three-icon control cluster** in a single dark pill: a sparkle (change mode), the
triangle mark (start recording), and a diagonal expand arrow (expand to the full
window). The docs are explicit that the *same three buttons* appear after a
dictation completes when the result was not auto-pasted — one control vocabulary
serving both "before" and "after", which is why the cluster is worth copying as
a pattern rather than as pixels.

Right-clicking the mini window opens a context menu: Expand Window, Open
Settings, Open History — available during recording too.

### 3. Menu bar icon and menu

The icon is the outlined triangle mark, monochrome. Its **status dot is
colour-coded and is the app's primary always-visible state channel**:

| Colour | Meaning |
| --- | --- |
| Yellow | Model loading |
| Red | Recording |
| Blue | Processing |
| Green | Processing complete |

The menu is short and grouped by separators:

```
Start/Stop Recording        ⌘⌥
Transcribe File...
History...
Settings...                 ⌘,
────────────────────────────────
Input Device                 ›
Select Mode                  ›
────────────────────────────────
Version 1.46.0   (disabled label)
Check for Updates...
Quit                        ⌘Q
```

Nine items, two submenus, one disabled version label. Optionally (advanced
setting) left-click toggles recording and right-click opens the menu instead.

### 4. Settings window

Standard macOS two-pane: fixed sidebar left, scrolling content right. Traffic
lights are **red and yellow only — no green/zoom**, so the window is a fixed,
non-resizable panel. The sidebar:

```
🏠 Home
✨ Modes
📘 Vocabulary
        (gap)
⚙️  Configuration
🔊 Sound
        (gap)
🐚 History              ↗
```

Sidebar icons are full-colour rounded-square app-style glyphs — orange house,
blue sparkle, blue book, grey gear, grey speaker, purple shell — the same visual
language as macOS System Settings. Two blank gaps do the grouping; there are no
section headers. **History carries an outward arrow** because it opens a separate
window, not a pane — a small, honest touch.

Pinned at the sidebar's bottom-left: a **`Superwhisper PRO` badge** in a
bordered pill, greyed, always visible. Licence state is ambient, not a nag.

A **persistent top bar** in the content pane, right-aligned, shows the current
input device (`System default` + a headphone glyph). It appears on every tab
(changelog 2.5.0, 2025-09-29: "Settings top bar now appears everywhere") — mic
selection is treated as global context, not a Sound-tab setting.

### 5. History window

A separate three-pane window, and the densest screen in the app.

- **Left:** a search field ("Search recordings") over a scrolling list of
  recording cards. Each card is a rounded rect: transcript snippet (truncated),
  then a metadata row of date, time, and duration.
- **Centre:** the selected recording's text, large and comfortably leaded, over
  a **playback strip** at the bottom — waveform with `0s`/`13s` end labels, a
  circular play button dead centre, copy and reveal-in-Finder icons bottom-left,
  and a small segmented control bottom-right (waveform / speakers / AI).
- **Right:** an inspector, on/off via a pane-toggle icon. Three stacked
  card groups with small grey captions above each: **Recording** (duration,
  voice model processing time, language model processing time, taken at),
  **Configuration** (mode, voice model, language model, language, and seven
  Yes/No capability rows, plus app version), and **Prompt** (the raw prompt text
  in a scrollable box).

A top-right segmented control switches the centre pane between **Voice /
Segments / AI** — the raw transcript, the speaker-separated segments, and the
AI-processed result. All three are retained per recording.

This window is where "why did I get that output" is answerable. The exact model,
the exact prompt, and the per-stage timings are all recorded per dictation.

### 6. Modes screen

Title, a two-line description, and a blue **Create mode** button top-right.
Below, a vertical list of mode cards. A collapsed card is one row: icon, name, a
green dot if active, a disclosure chevron right. Expanded, it reveals:

- **Preset** (dropdown, with an ⓘ) — Super / Voice to text / Message / Mail /
  Note / Meeting / Custom
- **Language** (dropdown)
- **Custom instructions** (multi-line textarea, placeholder "eg. Never use
  emoji") — only when Preset is Custom
- **model chips** along the bottom: a language-model chip (e.g. OpenAI mark +
  "GPT-4o mini") and a voice-model chip (e.g. "Ultra"), each a small rounded
  button with the provider's logo
- a gear icon at the row's right → the mode's own **Advanced settings**

The Create-mode dropdown lists the same seven presets, with **Super** tagged
`Recommended` in grey italic at the row's right.

### 7. Mode advanced settings (third level)

Opens as a **third column sliding in from the right**, with a `‹` back chevron
and the title "Advanced settings" centred. The parent columns stay visible but
dim — you can see where you came from. Contains:

- **Language Model** and **Voice Model** dropdowns, stacked in one card
- **Activate when using** — a card with a `+ Add App` tile and a
  `e.g. example.com` text field beside a blue **Add website** button. This is the
  auto-activation rule editor, and it lives *inside the mode*, not in a global
  rules screen.
- audio handling toggles: Mute audio while recording, Pause media while
  recording, Record from system audio, Identify speakers

The model dropdown is worth a note of its own: each row carries the provider's
logo, the model name, optional `EN` / `NEW` badges, and a right-side status glyph
— a **cloud** for cloud models, a **green check in a cloud** for the selected
one, a **⊖** for models not downloaded/available. `+ Create custom` is pinned at
the list's bottom. It is a lot of information per row and it stays readable.

### 8. Vocabulary

Title, a one-line explanation ("This helps Superwhisper recognize people's
names, company names, acronyms, slang, or words from other languages"), then a
single card labelled **Input** holding one row: a `New word or sentence` text
field, a `Replace with...` button, and a blue `Add to vocabulary` button.

Two features — hotword nudging and literal text replacement — collapsed into one
input row, where the middle button is what distinguishes them. Same split as
Vocal Advantage's `dictionary.json` (`words` vs `fixes`), presented as one
gesture.

---

## How settings are grouped

There are **three levels of depth**, and which level a setting sits at is the
most transferable thing in this teardown.

### Level 1 — the sidebar tabs

`Configuration` is the app-wide tab. Two groups:

**Keyboard Shortcuts** — five rows, each with a bold label, a grey one-line
description underneath, and on the right a reset-arrow, the key caps as
individual chips, and an `×` to clear:

| Row | Description shown |
| --- | --- |
| Toggle Recording | Starts and stops recordings |
| Cancel Recording | Discards the active recording |
| Change mode | Activates the mode switcher |
| Push to Talk | Hold to record, release when done |
| Mouse shortcut | Tap to toggle, or hold and release when done |

**Application** — Update application (with a `Check for Updates...` button),
Automatically check for updates, Launch on login, Error logging. Each label
carries a `(?)` help affordance.

`Sound` is two groups: **Microphone** (Automatically increase microphone volume,
Silence removal) and **Sound Effects** (Enable sound effects, Volume slider with
tick marks).

That is the entire top level: **five shortcuts, four app toggles, four sound
controls.** Everything else is one level down.

### Level 2 — "Advanced settings"

A full-width row pinned at the bottom of the Configuration pane, styled as a
lighter bar with a `›` chevron. It leads to six groups:

| Group | Contents |
| --- | --- |
| Recording Window | Recording window enabled · Close recording window automatically · Mini Recording window · Always show Mini Recording Window |
| Application | Show in Dock · Start Recording on Menubar Click |
| Voice Processing Options | Voice model active duration (dropdown: "1 minute") · Dynamic normalization |
| Folder Location | the config path + `Change folder...` · Filesync enabled |
| Text Input Controls | Paste result text · Hold shift to auto-send after paste · Restore clipboard after paste · Simulate keypresses |
| Experimental Models | Show experimental models |

Some rows carry a small **atom glyph** to the left of their toggle, marking them
experimental — used on Hold shift to auto-send, Simulate keypresses, Filesync,
Dynamic normalization, and Show experimental models. It is a second, subtler
warning tier *inside* the advanced pane, and it is a nice idea: "advanced" and
"might misbehave" are different claims and get different marks.

### Level 3 — per-mode advanced settings

Model choice, auto-activation rules, and audio handling. Reached only through a
gear on a mode row.

### The line they drew

The organising principle is clear and worth stating plainly:

> **Level 1 is what you change to fit your hands. Level 2 is what you change to
> fit your machine. Level 3 is what you change to fit the task.**

Shortcuts and sound are hands. Window behaviour, memory residency, folder
location, and paste mechanics are machine. Models and prompts are task.

Two consequences of that split:

- **Paste behaviour is buried.** "Paste result text", "Restore clipboard after
  paste", and "Simulate keypresses" are level 2. These are the settings most
  likely to be the difference between the app working and not working in a given
  editor, and they are two clicks and a scroll from the front.
- **Model choice is not global at all.** There is no "which model" setting in
  the app-wide settings; it exists only per mode. Choosing a mode *is* choosing a
  model. That is a strong, opinionated call and it is why the mode system carries
  as much weight as it does.

### Getting into settings

Four documented routes, which is itself a design position: menu bar item, dock
icon + `⌘,` (requires Show in Dock, which is off by default), right-click the
mini recording window, or the `superwhisper://settings` deep link.

---

## Modes

### What a mode is

A named bundle of: a preset (the AI processing instructions), a language, a voice
model, a language model, custom instructions, auto-activation rules, and audio
handling flags. Stored as a **JSON file per mode** in `~/superwhisper/modes/`,
each with a `key` and a `name` field — user-visible, documented, and editable.

Seven presets: **Super** (context-aware, marked Recommended), **Voice to text**
(no AI pass at all), **Message**, **Mail**, **Note**, **Meeting**, **Custom**.

The gradient is deliberate. Voice-to-text is the fast path with no LLM. Message/
Mail/Note are fixed prompts tuned per output shape. Super reads your active app,
your selection, and your clipboard. Custom hands you the prompt box.

Built-in modes cannot be edited directly — the documented workaround is to create
a Custom mode and paste the built-in's instructions in as a starting point. That
is a friction point they wrote a whole docs page about
(`/docs/modes/customizing-modes`) rather than fixing.

### Switching modes — four ways

1. **Keyboard, mid-recording.** Trigger recording, press the Change-mode
   shortcut (`^⇧Z` by default), then **hold the modifier and tap the key
   repeatedly to cycle**, releasing on the one you want. The switcher overlay is
   a large rounded panel listing modes as rows: a `⌘1`…`⌘5` key-cap chip at
   left, the mode name, an optional ★ for favourites, and an empty radio circle
   at the right. The list fades toward the bottom rather than ending at a hard
   edge. So there are two selection gestures on one surface — cycle-by-repeat,
   or jump by number.
2. **Menu bar** → Select Mode submenu.
3. **Auto-activation rules** — per-mode app and website matchers.
4. **Deep links** — `superwhisper://mode?key=YOUR_MODE_KEY` and
   `superwhisper://record`, designed to be chained in Shortcuts/Raycast/Alfred.

Two honest limitations in their own docs, both worth noting because they are the
kind of thing that bites later:

- The active mode is **only visible in the large recording window**. Use the mini
  window and you are flying blind on which mode is armed.
- Once a mode auto-activates for an app, **you cannot override it**, and it does
  not switch back afterwards.

---

## Recording and completion feedback

### While recording

Five simultaneous channels, which is the striking part — no single signal is
load-bearing:

1. **Menu bar dot turns red.** Visible even with every window hidden.
2. **The waveform moves.** The docs lean on this hard: a static waveform is
   documented as the diagnostic for "your mic is not being captured", with a
   troubleshooting list attached. The animation is doing double duty as a health
   check.
3. **The mini pill turns red** and grows a red record button.
4. **A start sound plays** (Enable sound effects, on by default).
5. **The status dot** in the recording window's control strip.

### From start to finish

The status dot is a four-state colour progression shared between the menu bar
and the recording window: **yellow** (model loading) → **red** (recording) →
**blue** (processing) → **green** (done). One vocabulary, two locations.

### When the text is ready

The default is that the text **just appears** in the focused app — paste result
text is on by default and the recording window closes on successful paste
detection. Note the mechanism: it closes on *paste detection*, not on a timer.
The window staying open is itself the failure signal.

When the result is *not* auto-pasted, the mini window shows the result and the
three-button cluster appears above it — change mode / record again / expand. The
docs call these "post-request controls", and they are the same three buttons as
the idle hover state.

Beyond the moment: every result lands in History with its transcript, its
AI output, its audio, its per-stage timings, and the prompt that produced it.
"Where did my text go" always has an answer.

The one safety interlock: cancelling a recording **longer than 30 seconds**
raises a confirmation; under 30 seconds it cancels immediately. A time-based
threshold on a destructive action, rather than confirming always or never.

---

## Onboarding

The first window is pictured and I looked at it: a near-square panel, no sidebar,
the 3D app icon floating in the upper third, then `superwhisper` in medium
weight, then the tagline "Transform your voice into text" in grey, then a
prominent light **`Get Started`** button with **`⌘ Enter`** shown as a key cap
inside the button itself.

That last detail is the whole design philosophy in one control. The first button
a new user ever sees is already teaching them that this app is driven by the
keyboard.

The steps that follow are **described in the docs but not pictured**, so this is
their text, not my observation:

- set up system permissions
- choose transcription language
- select an AI model, with system-optimised suggestions
- configure microphone and audio
- complete a **guided first dictation** using the keyboard shortcut
- test and verify the setup works

Two things stand out even from prose. It ends with a **guided first dictation
rather than a settings summary** — you leave onboarding having successfully used
the product once. And the model recommendation is **hardware-aware**, so the
hardest question a new user faces gets answered for them.

The docs then say explicitly: *"Superwhisper is designed to work excellently
right out of the box, so if you've completed the initial onboarding, you're
already set to start dictating."* Free tier gives 15 minutes of Pro and 3 modes.

Onboarding is not a one-time event they finished with. It kept being worked on:
"onboarding toasts to help new users get started" (2.13.0, 2026-04-24), "Local
Mode is now suggested automatically when your keyboard is set to English"
(2.18.0), and the most recent release in the log is literally "Updated default
modes and models for a smoother onboarding experience" (2.18.1, 2026-08-20).

---

## What the changelog says about what users wanted

The log runs from **1.11.0 (2023-08-03)** to **2.18.1 (2026-08-20)** — three
years, 210 releases.

### First few weeks — the reflexes

| Version | Date | Shipped |
| --- | --- | --- |
| 1.12.0 | 2023-08-12 | Auto language detection, literal punctuation, "New settings layout and UI improvements" |
| 1.12.3 | 2023-08-17 | **Sound effects when recording begins and ends**; accessibility install dialog |
| 1.13.0 | 2023-08-22 | Translate any spoken language to English |
| 1.13.2 | 2023-08-25 | **Onboarding flow** |
| 1.14.0 | 2023-09-08 | **Dictation history view (Beta)** |
| 1.14.3 | 2023-09-14 | Search recordings; hover hints on recording stats |
| 1.16.0 | 2023-10-31 | Option to turn off automatic paste |

Sound effects landed in **week two**. Onboarding in **week four**. History in
**week six**. All three before Modes existed at all.

That ordering is the finding. A dictation app's first real problems are not about
features, they are: *did it hear me* (sound + waveform), *what do I do now*
(onboarding), and *where did my text go* (history). Superwhisper answered all
three before it built anything clever.

### Month four — Modes

**1.19.0, 2023-12-10:** *"New feature: 'Modes', allows you to make many
configurations of models, languages, prompts, and configurations"* — and, in the
same release, *"You can switch between 'Modes' from the menubar"*.

The switching mechanism shipped **in the same release as the concept**. They
never shipped a mode system you had to go into settings to use.

### The long middle — capability

Text replacements (1.19.5, Dec 2023) → clipboard context (1.25.0, Jan 2024) →
speaker separation (1.30.0, Mar 2024) → **Vocabulary** (1.32.0, Apr 2024) →
Ollama support (1.34.0, Apr 2024) → **Super mode, experimental** (1.36.0, May
2024) → **Push to Talk** (1.36.1, Jun 2024) → offline diarization (1.40.0, Oct
2024).

Note that Vocabulary (Apr 2024) came **four months after** text replacements
(Dec 2023) — the two halves of the same problem, shipped half a year apart, and
only later merged into one input row.

### Late — the interface itself

| Version | Date | Shipped |
| --- | --- | --- |
| 1.46.0 | 2025-05-27 | **Mini recording window** — nearly 2 years in |
| 2.0.0 | 2025-07-10 | **Settings redesign + Modes overhaul**; AI models moved into the Modes UI and the separate AI Models tab was *removed* |
| 2.10.0 | 2026-02-23 | Realtime streaming UI with live transcription |
| 2.13.0 | 2026-04-24 | Modes redesigned *again*, dedicated settings view; onboarding toasts; new Vocabulary & Replacements design |
| 2.16.0 | 2026-06-09 | **Light / dark / system theme control** — nearly 3 years in |
| 2.16.3 | 2026-07-10 | Deep links to start/stop recording |
| 2.18.0 | 2026-08-19 | S1-mini, on-device LLM cleanup with no network request |

### What that ordering says users wanted

1. **Feedback beats features.** Sounds, onboarding, and history all shipped
   inside six weeks. Modes took four months.
2. **The settings surface was the recurring problem, not a one-time job.**
   Redesigned at 1.12.0, again at 2.0.0, again at 2.13.0. Version 2.0's headline
   was a settings redesign — the marquee release of the product's second era was
   about *organising what already existed*. And the direction was always
   consolidation: 2.0.0 deleted an entire tab by folding model choice into modes.
3. **Cosmetics genuinely were last.** Light mode took three years. They shipped
   a mini window, a realtime UI, two settings redesigns and an enterprise SSO
   stack before letting anyone pick a theme. Nobody was blocked on light mode;
   people were blocked on not knowing what the app was doing.
4. **Automation demand arrived late but arrived.** Deep links for mode switching
   predate deep links for recording by years, and third-party Raycast/Alfred
   integrations exist. Power users routed around the UI, and the app eventually
   met them.
5. **The last mile is going back on-device.** S1-mini (Aug 2026) does the
   cleanup pass locally with no network request — the same architecture Vocal
   Advantage started from.

---

## Visual notes

**Density: sparse at the front, dense at the back.** The recording window has
maybe six elements. The Configuration tab has thirteen controls across two
groups with generous padding. The History inspector, by contrast, stacks
seventeen label/value rows in three groups. Density scales with how deliberately
you went there — a screen you glance at is sparse, a screen you went looking for
is packed. That is a rule worth stealing outright.

**Palette.** Near-black (`#000`–`#0d0d0d`) for the waveform field and recording
window; a soft charcoal (~`#3a3a3c`) for settings cards; a slightly lighter
charcoal for card rows. Backgrounds are not flat — the app windows sit on a
subtly mottled dark ground with a faint vignette, which is what keeps a mostly-
black UI from looking like a void.

Colour is rationed hard:

- **Blue** is the only accent — active toggles, primary buttons ("Create mode",
  "Add website", "Add to vocabulary"), the selected sidebar row, the volume
  slider fill, and the selected history card's border.
- **Status colours** (yellow/red/blue/green) appear only as small dots and only
  to mean state.
- **Provider logos** — OpenAI green, Deepgram red, NVIDIA green — are the only
  other colour, and they are borrowed, not chosen.
- Sidebar icons are the loudest colour in the app, and they are confined to a
  strip 200px wide.

Everything else is greyscale. In the whole settings window there is exactly one
blue button visible at a time.

**Type.** SF Pro throughout, three sizes doing all the work: a ~22px semibold
screen title, a ~15px regular control label, a ~13px grey description/caption.
Group headers ("Microphone", "Sound Effects", "Recording", "Configuration") are
small grey text *outside and above* the card, never inside it — so the card stays
pure content. Numerals in the history inspector are right-aligned against
left-aligned labels, the standard spec-sheet arrangement, and it reads instantly.

**Shape.** Rounded rectangles at three consistent radii: ~12px for cards and
panels, ~6px for key caps and chips, full-round for pills, toggles and the play
button. No square corners anywhere in the app.

**Borders are almost absent.** Grouping is done with fill, not stroke. Cards are
a lighter fill on a darker ground; rows inside a card are divided by hairlines
that stop short of the card's edges (inset dividers, macOS-style). The recording
window's two bands are separated by one hairline. Buttons have no borders at
rest and gain a filled pill on hover.

**Key caps are a first-class UI element.** `⌥⌘^0`, `Esc`, `⌘1`, `^⇧Z` all render
as small rounded chips with a slightly lighter fill. They appear in the recording
window, the mode switcher, the shortcuts settings, and inside the onboarding
button. Rendering shortcuts as objects rather than text is probably the single
most characteristic thing about this interface.

**Spacing.** Roughly an 8px grid. Cards are padded ~16px; rows inside them are
~44px tall; groups are separated by ~24px. The recording window's control strip
has a large deliberate void between the mode name (left) and Stop/Cancel
(right) — destructive and non-destructive controls kept far apart, with the
divider between Stop and Cancel as a third separation.

**Overall feel: quiet, and confident about it.** Nothing is competing for
attention. Almost everything is greyscale, borderless, and low-contrast until you
interact with it. The app is designed to be *looked past* — it sits over your
work all day and does not want to be noticed until it has something to say. The
one deliberate exception is colour-as-state: a red dot in the menu bar is the
only thing in the entire product that is trying to catch your eye.

One tiny blemish worth recording, since it argues that polish is not free even
here: the Modes screen labels its prompt box **"Custom intructions"** — missing
the `s`. Visible in `assets/modes-modes-custom-001.png`.

---

## What Vocal Advantage is missing, ranked by impact

Vocal Advantage's current UI surface, for reference: a Flow Bar (waveform pill
with idle / recording / transcribing / message states, draggable, three preset
positions), a tray icon whose menu is a disabled status line + Move bar +
Change hotkey… + Quit, generated start/done/error tones, console output
including a per-stage timing block, and `config.json` + `dictionary.json` edited
by hand.

Ranked by impact on daily use, not by effort.

### 1. There is no settings UI at all — **large**

Twenty settings live in `config.json` and every one of them requires quitting to
a text editor. Superwhisper's answer is not "expose everything" — it is a
three-tier split, and the tiering is the part worth copying. Mapped onto the
existing keys:

| Tier | Vocal Advantage keys |
| --- | --- |
| **Hands** (front) | `hotkey`, `tap_threshold_s`, `sounds`, `sound_on_start`, `flow_bar`, `flow_bar_position` |
| **Machine** (advanced) | `model`, `device`, `chunk_s`, `overlap_s`, `min_duration_s`, `max_duration_s`, `silence_timeout_s`, `history`, `timings` |
| **Task** (per-profile) | `clean_speech`, `ai_cleanup`, `language`, `skip_cleanup_in` |

Large because it means a real cross-platform UI layer — AppKit on macOS,
something else on Windows — where today there is only a menu-bar item and a
tkinter-free overlay. But note the tier table is free: it can be applied to the
README and `config.json` comment ordering *today*, and it makes the eventual
window mostly a transcription job.

### 2. No modes, and no way to vary behaviour per task — **large**

This is Superwhisper's core differentiator and Vocal Advantage has a
one-bit version of it already: `skip_cleanup_in` is a list of apps where the
cleanup pass is disabled. That is an auto-activation rule with exactly two
outcomes.

The generalisation is a named profile carrying `clean_speech`, `ai_cleanup`,
`language`, model, and an app-match list — with `skip_cleanup_in` becoming a
built-in "Raw" profile rather than a special case. Their strongest lesson here is
1.19.0: **ship the switcher in the same release as the concept.** A profile
system you have to open settings to use is not one people will use.

Their two documented mistakes are free to avoid: make the active profile visible
in the *small* UI too, and let a manual switch override an auto-activated one.

### 3. History is written but cannot be read — **medium**

`logs/history.jsonl` already accumulates every dictation with text, timestamp,
app, and duration, capped at 2,000 lines. There is no way to look at it without
opening a JSONL file. Superwhisper shipped a history *view* in week six, and it
is the answer to "the paste went into the wrong window" — which is precisely the
failure `history.py`'s docstring says it exists for. The data is being collected
for a recovery use case the UI cannot serve.

Medium, and the best value-per-hour item on this list: the hard part (capture) is
done. A searchable list with copy-to-clipboard is most of the value.

Worth flagging: history stores **text only, no audio**. Their reprocess-from-
history feature is therefore not available at any size without first deciding to
retain audio — which is a privacy and disk decision, not a UI one.

### 4. The tray icon never shows state — **small**

Today, state lives in a *disabled menu item you have to open the menu to read*.
The Flow Bar shows state, but it can be turned off (`flow_bar: false`), and then
the app has no persistent state channel at all. Superwhisper's colour-coded dot
is the single highest-leverage feedback element in their product.

Small, with one real gotcha already sitting in the code: `tray_mac.py` calls
`setTemplate_(True)`, which hands the icon to macOS to recolour for the menu-bar
appearance. **A template image cannot carry a colour-coded dot** — macOS will
flatten it. The dot has to be composited into a non-template variant for the
non-idle states, or expressed as a shape change instead of a colour change.

### 5. The Flow Bar never says what will happen — **small**

Their control strip is a permanent, live legend: mode name, mode-switch
shortcut, `Stop ⌥⌘^0`, `Cancel Esc`. Vocal Advantage's bar is a waveform and
nothing else — the hotkey is knowable only from the README or the console line
at startup.

Adding the active hotkey as a small label at the bar's left, in the same
key-cap-chip style, is a small change that makes the overlay self-documenting.
`flowbar.py` already has a text-rendering path (`MESSAGE`, `TEXT_ALPHA`,
`MESSAGE_CHAR_WIDTH`), so the drawing machinery exists.

### 6. Cancel is invisible and conditional — **small**

Cancel-on-other-key works only when the hotkey is or contains a bare modifier;
with `f8` the rule is off by design. So on some configurations there is **no way
to abandon a dictation in progress**, and on none of them is cancelling
advertised. Superwhisper gives cancel a dedicated always-present shortcut
(`Esc`), a visible control, and a 30-second confirmation threshold.

An unconditional Esc-to-cancel plus a `Cancel Esc` label in the bar closes this.

### 7. Nothing catches the text when pasting fails — **medium**

`PASTE_FAILED_MESSAGE` flashes on the bar for 90 frames (1.5s) and then the bar
goes quiet. The transcript is on the clipboard and in history, but the UI has
moved on. Superwhisper's answer is the post-request control cluster: the result
stays on screen with copy / retry / expand until dealt with.

Medium. The minimum version is small: on paste failure, hold the message state
instead of timing out, and make clicking the bar re-attempt the paste.

### 8. There is no onboarding — **medium**

First run is: read the README, run four PowerShell lines, run `--set-hotkey`,
watch the console for `Ready.`. Their onboarding ends with a **guided first
dictation** — the user succeeds once before being left alone — and it recommends
a model based on the hardware it finds.

Both ideas port to a console-first app without a GUI. A first-run flow that
detects no `config.json`, asks for the hotkey, picks a model from whether CUDA
is present, and then says "hold your key and say something" while waiting to
confirm the round trip would be a large improvement for zero UI framework.
Medium mostly because it needs a state machine and a "have I run before" marker.

### 9. The dictionary is a hand-edited JSON file — **medium**

`dictionary.json` ships with an empty `words` list and an empty `fixes` map, and
a `_help` string explaining the format — which is a tell that the format needs
explaining. It is the file most likely to be edited repeatedly during real use
(every misheard name is an entry) and it has the highest edit friction in the
project.

Superwhisper collapsed the same two concepts into one input row with three
controls. Medium, and it becomes small once item 1 exists.

### 10. Per-dictation timings are computed and then thrown away — **small**

`timings.py` produces a per-stage breakdown after every dictation and prints it
to a console the user is told to leave open but not to look at. Superwhisper
keeps the same numbers *attached to the recording* in the History inspector,
where they can be compared later. Once item 3 exists, writing the timing block
into the history line is nearly free — and "it felt slow" becomes answerable
after the fact rather than only in the moment.

### 11. No file transcription — **medium**

`Transcribe File...` is the second item in their menu bar. Vocal Advantage
already has a loaded Whisper model, a transcriber wrapper, and a chunker; what is
missing is a file picker and a decode path for non-WAV input. Medium mostly
because of the decode dependency — the SPEC deliberately excludes ffmpeg/PyAV
("no ffmpeg, no PyAV decode"), so this either accepts WAV only or reopens a
decision the project made on purpose.

### 12. The Flow Bar ignores the system appearance — **small**

`PILL_FILL_RGB = (0.97, 0.965, 0.945)` — a warm near-white pill with black bars,
in all conditions. It is a deliberate, defensible choice and it is well
documented in the code. But Superwhisper took three years to add theme control
and shipped it anyway, which suggests it eventually matters. Lowest priority on
this list, and listed last on purpose.

---

## Sources

Primary (captured directly, in `design-research/superwhisper/`):
[superwhisper.com](https://superwhisper.com) ·
[/docs](https://superwhisper.com/docs) ·
[/changelog](https://superwhisper.com/changelog) · 57 docs pages via
[sitemap.xml](https://superwhisper.com/docs/sitemap.xml)

Secondary (reviews and community discussion):
[Product Hunt reviews](https://www.producthunt.com/products/superwhisper/reviews) ·
[Voibe: Wispr Flow vs Superwhisper](https://www.getvoibe.com/resources/wispr-flow-vs-superwhisper/) ·
[Voibe: Superwhisper review](https://www.getvoibe.com/resources/superwhisper-review/) ·
[Spokenly: Superwhisper review](https://spokenly.app/blog/superwhisper-review) ·
[MetaWhisp review](https://metawhisp.com/blog/superwhisper-review/) ·
[Blazing Fast Transcription review](https://blazingfasttranscription.com/blog/superwhisper-review) ·
[Macrowhisper (community automation helper)](https://afadingthought.substack.com/p/macrowhisper-automation-helper-for-superwhisper)

**On the secondary sources, a caveat.** No Reddit thread was retrieved directly —
site-scoped searches returned no Reddit results, and the round-up articles that
*cite* Reddit ("users describe the settings surface as overwhelming"; one quoted
user: *"I love Superwhisper but I don't think it makes sense to all users. Its
value, for me, is on the unlimited LLM access and its flexibility of use"*) are
paraphrasing threads I could not open to verify. Treat the recurring
"overwhelming settings / steep learning curve / 15–30 minutes to a useful setup"
claim as consistent across several independent review sites, but not as
first-hand evidence. Product Hunt, which I did retrieve, skews positive and
praises the "sleek" UI and custom modes; its one recorded friction point is that
app-based auto mode switching is "not 100% reliable" — which matches the
limitation Superwhisper's own docs admit.
