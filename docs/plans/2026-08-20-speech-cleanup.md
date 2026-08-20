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

---

## Outcome — the AI pass (2026-08-20)

Built to spec, off by default, `ai_cleanup` in `config.json`.

**qwen3:4b does not work for this and cannot be made to.** It is a reasoning
model; `think: false` removes the `<think>` tags but not the reasoning, which
then arrives as prose in the content field.

| Attempt | Result |
| --- | --- |
| `think: false`, 87 tokens (spec formula) | preamble only, cut off |
| `think: false`, 2000 tokens | 62s, still deliberating, no answer |
| `think: true`, 1200 tokens | 35–85s, ~850 words of thinking, no answer |
| temperature 0 vs 0.7 / top_p 0.1 vs 0.8 | no difference |
| few-shot only, hard no-preamble line, assistant prefill | no difference |

**`qwen2.5:3b-instruct` works.** 12 ordinary dictations, guard applied: 10 kept,
~0.5s each. Both rejections were the guard catching a real meaning change.
`llama3.2:3b` was worse — it answered the question transcript ("I don't know
the capital of France") and swapped "a sec" for "a moment".

**The lesson worth keeping: use a non-reasoning instruct model.** A reasoning
model will spend more words deliberating about removing an "um" than the
sentence contains.

Warm-up added: a cold 2 GB model takes ~10s to load, which would blow the 6s
budget on the very first dictation — the one that forms the opinion.

### Guard results in the wild

Both candidate models tried to answer the transcript rather than clean it:

    "um whats the capital of france"
      llama3.2:3b -> "I don't know the capital of France."   binned
      qwen2.5:3b  -> "The capital of France is Paris."       binned

Which is precisely the failure Kevin predicted when specifying the check, and
the reason no length-based rule alone would be enough.
