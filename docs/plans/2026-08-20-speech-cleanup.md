# Speech cleanup — spec

**Written:** 2026-08-20
**Why:** Kevin said "um so I I think we should uh ship it on friday" and got
`Um, so I think we should ship it on Friday.` Whisper punctuates and
capitalises well and resolved the `I I` stutter itself. The only thing left
to remove is the filler.

**Decided against:** a local 0.5B model (276 MB, ~0.2s). Measured, and it added
only punctuation Whisper already provides. It also cannot work with
word-by-word typing: it can only clean a finished sentence, by which time the
filler is on screen and removing it means backspacing over text the user is
looking at. Rules never type it in the first place. See the project note for
the measurements.

---

## Checklist

Each line is a yes/no I can check by looking.

1. `Um, so I think we should ship it on Friday.` → `So I think we should ship it on Friday.`
2. A filler in the middle goes too: `I think, um, we should` → `I think, we should`
3. Fillers only count as whole words. `umbrella`, `uhuru`, `Erm...` — the first
   two survive untouched, the third goes.
4. Case does not matter: `Um`, `um`, `UM` all go.
5. A doubled word collapses: `the the file` → `the file`, `I I think` → `I think`.
6. When the doubled word carries punctuation, the tidier one survives:
   `I, I think` → `I think`, not `I, think`.
7. Removing the first word re-capitalises the new first word.
8. A sentence with nothing to clean comes back **character-for-character identical**.
9. Cleaning an already-clean sentence twice changes nothing further.
10. An all-filler utterance (`um uh er`) cleans to empty, and empty types nothing.
11. Live typing sees cleaned text, so the word `Um,` never reaches the document
    at all — not typed then removed.
12. `config.json` gains `clean_speech` (default `true`); setting it `false`
    gives the raw transcript back.

## Constraints

- **Cleaning happens before `StreamingTranscript` sees the text.** Cleaning
  after it would put the document and `_committed` out of step again — the
  exact bug fixed in 8dda0cd.
- Pure and portable: no OS, no model, no audio. Same module serves Mac and PC.
- Deterministic, so repeated passes over a growing utterance stay consistent
  and LocalAgreement-2 keeps working.
- Never invent a word. Removal and capitalisation only.
