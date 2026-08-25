"""The settings page: one HTML document, generated in Python.

Generated rather than shipped as a file, for the same reason `tray_icon` draws
the icon and `sounds` synthesises its tones -- no binaries and no assets in the
repository. It also means the page cannot drift from `settings_api.TIERS`: the
three panes are built by walking that table, so a setting added to the app
appears here without anyone remembering to add it.

The design is the prototype at
https://claude.ai/code/artifact/e0e17c38-77d1-4de3-b98a-4b9e11aedb82 --
sidebar, cards on a dark ground, one blue accent, shortcuts drawn as key caps.
What is missing against that picture is everything that needs a feature this
app does not have yet: Profiles, Words, History and the Home stats. Those panes
are absent rather than shown empty, because a disabled tab is a promise.

There is no HTTP anywhere in this file. The page talks to Python through
WKWebView's message bridge, so there is no port, no token and nothing listening.
"""

from __future__ import annotations

import json

from .settings_api import TIERS

#: How each setting is drawn, and what it says about itself. Keyed the same as
#: DEFAULTS so `_pane` can walk a tier and render it without a special case.
#:
#: The help text is the point of this table. A settings window whose rows are
#: bare key names is `config.json` with rounded corners -- the reason a control
#: exists is what the file cannot tell you.
CONTROLS: dict[str, dict] = {
    "hotkey": {
        "label": "Hold to talk",
        "help": "Hold and release, or tap once to start and again to stop.",
        "kind": "keycap",
    },
    "tap_threshold_s": {
        "label": "Tap threshold",
        "help": "Release faster than this and it toggles instead of stopping.",
        "kind": "seconds",
    },
    "flow_bar": {"label": "Show the Flow Bar", "kind": "toggle",
                 "help": "The waveform pill. Turn it off for the tray icon alone."},
    "flow_bar_position": {
        "label": "Position", "kind": "choice",
        "choices": ["bottom-centre", "bottom-left", "bottom-right"],
        "help": "Where it rests. Drag it with “Move bar” to override this.",
    },
    "sounds": {"label": "Sound when finished", "kind": "toggle",
               "help": "A soft note when the text lands, and a lower one on failure."},
    "sound_on_start": {
        "label": "Sound when recording starts", "kind": "toggle",
        "help": "Off by default: it plays while the microphone is open, so on "
                "speakers it goes back into the recording. Safe on headphones.",
    },
    "model": {
        "label": "Speech model", "kind": "choice",
        "choices": ["tiny", "base", "small", "large-v3-turbo"],
        "help": "Bigger hears better and costs more time.",
    },
    "device": {
        "label": "Run the model on", "kind": "choice",
        "choices": ["auto", "metal", "cuda", "cpu"],
        "help": "Automatic picks Metal on Apple Silicon, CUDA on an NVIDIA "
                "card, otherwise the CPU. The choice is printed at startup.",
    },
    "chunk_s": {
        "label": "Chunk length", "kind": "seconds",
        "help": "How much audio is transcribed at a time while you speak. "
                "Bigger is more accurate and leaves more to do when you stop.",
    },
    "overlap_s": {
        "label": "Overlap", "kind": "seconds",
        "help": "How far each chunk reaches back into the last, so a word on a "
                "boundary is not lost.",
    },
    "min_duration_s": {"label": "Shortest kept", "kind": "seconds",
                       "help": "Anything shorter than this is thrown away."},
    "max_duration_s": {"label": "Longest recording", "kind": "seconds",
                       "help": "Force-stops a recording you forgot about."},
    "silence_timeout_s": {
        "label": "Stop after silence", "kind": "seconds",
        "help": "0 turns it off. Silence before you have said anything does "
                "not count — pressing the key and thinking is normal.",
    },
    "history": {
        "label": "Keep a history", "kind": "toggle",
        "help": "Every dictation, so one that pasted into the wrong window is "
                "still recoverable. Never leaves this machine.",
    },
    "timings": {"label": "Print stage timings", "kind": "toggle",
                "help": "Five lines after every dictation, in the console."},
    "clean_speech": {"label": "Remove filler words", "kind": "toggle",
                     "help": "Drops “um”, “uh” and stutters before anything is typed."},
    "ai_cleanup": {
        "label": "AI cleanup pass", "kind": "toggle",
        "help": "An extra pass through a local model. Costs up to six seconds "
                "after you stop, and needs Ollama running.",
    },
    "language": {"label": "Language", "kind": "text",
                 "help": "Fixed, so the model never has to guess."},
    "skip_cleanup_in": {
        "label": "Apps that get the raw transcript", "kind": "list",
        "help": "Filler removal is right for prose and wrong for a shell — it "
                "will happily turn a command into something that does not run.",
    },
}

PANES = [
    ("hands", "Configuration", "Everything you change to fit your hands.",
     "⚙︎"),
    ("machine", "Advanced", "Everything you change to fit this machine.",
     "◧"),
    ("task", "Writing", "How what you say becomes what gets typed.",
     "✦"),
]


def page() -> str:
    """The whole settings document as one string."""
    nav = "\n".join(
        f'<button class="nav" data-pane="{key}" aria-selected="'
        f'{"true" if i == 0 else "false"}"><span class="gi">{icon}</span>{title}</button>'
        for i, (key, title, _sub, icon) in enumerate(PANES)
    )
    panes = "\n".join(
        _pane(key, title, sub, i == 0) for i, (key, title, sub, _ic) in enumerate(PANES)
    )
    return _DOC.replace("{{NAV}}", nav).replace("{{PANES}}", panes).replace(
        "{{CONTROLS}}", json.dumps(CONTROLS)
    )


def _pane(tier: str, title: str, subtitle: str, first: bool) -> str:
    rows = "\n".join(_row(key) for key in TIERS[tier] if key in CONTROLS)
    hidden = "" if first else " hidden"
    note = ""
    if tier == "task":
        note = (
            '<p class="note">These are global today. When profiles arrive they '
            "move inside one, and choosing a profile becomes how you choose "
            "them.</p>"
        )
    return (
        f'<section class="pane" id="pane-{tier}"{hidden}>'
        f"<h1>{title}</h1><p class='lede'>{subtitle}</p>{note}"
        f'<div class="card">{rows}</div></section>'
    )


def _row(key: str) -> str:
    spec = CONTROLS[key]
    help_text = spec.get("help", "")
    return (
        f'<div class="row" data-key="{key}">'
        f'<div class="lab"><div class="t">{spec["label"]}</div>'
        f'<div class="d">{help_text}</div></div>'
        f'<div class="ctl" data-kind="{spec["kind"]}"></div>'
        f"</div>"
    )


_DOC = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Vocal Advantage</title><style>
:root{
  --bg:#0f0f10; --panel:#3a3a3c; --card:#48484a; --hair:rgba(255,255,255,.085);
  --ink:#f2f2f7; --ink2:#a1a1a6; --ink3:#7c7c80; --blue:#0a84ff;
  --side:#2b2b2d; --bad:#ff6b6b; --good:#5fbf7f;
  --sys:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;font-family:var(--sys);background:var(--panel);color:var(--ink);
  font-size:14px;display:grid;grid-template-columns:196px minmax(0,1fr);
  -webkit-user-select:none;user-select:none;overflow:hidden}
aside{background:var(--side);padding:34px 10px 12px;display:flex;flex-direction:column;gap:2px}
.nav{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:7px;
  color:var(--ink);font:inherit;background:none;border:0;cursor:pointer;text-align:left}
.nav:hover{background:rgba(255,255,255,.055)}
.nav[aria-selected="true"]{background:rgba(255,255,255,.15)}
.nav .gi{width:21px;height:21px;border-radius:5px;background:#5a5a5e;display:grid;
  place-items:center;font-size:11px;flex:none}
.foot{margin-top:auto;color:var(--ink3);font-size:11.5px;line-height:1.5;padding:0 9px}
main{overflow-y:auto;padding:30px 30px 40px}
h1{font-size:22px;font-weight:600;margin:0 0 4px;letter-spacing:-.01em}
.lede{color:var(--ink2);margin:0 0 20px;font-size:13px;max-width:62ch;line-height:1.5}
.note{background:rgba(10,132,255,.12);border:1px solid rgba(10,132,255,.3);
  border-radius:8px;padding:9px 12px;color:#cfe4ff;font-size:12.5px;margin:0 0 16px;
  max-width:62ch;line-height:1.5}
.card{background:var(--card);border-radius:10px;overflow:hidden}
.row{display:flex;align-items:flex-start;gap:18px;padding:13px 15px;position:relative}
.row+.row::before{content:"";position:absolute;top:0;left:15px;right:15px;height:1px;
  background:var(--hair)}
.lab{flex:1;min-width:0}
.lab .t{font-size:14px}
.lab .d{font-size:12px;color:var(--ink2);margin-top:2px;line-height:1.45;max-width:52ch}
.ctl{flex:none;display:flex;align-items:center;gap:8px;padding-top:1px}
.tog{width:38px;height:23px;border-radius:999px;background:#6c6c70;position:relative;
  cursor:pointer;border:0;flex:none;transition:background .15s}
.tog i{position:absolute;top:2.5px;left:2.5px;width:18px;height:18px;border-radius:50%;
  background:#fff;transition:left .15s}
.tog[aria-checked="true"]{background:var(--blue)}
.tog[aria-checked="true"] i{left:17px}
select,input[type=text]{background:#5f5f63;color:var(--ink);border:1px solid rgba(255,255,255,.1);
  border-radius:6px;padding:4px 8px;font:inherit;font-size:13px;-webkit-user-select:text;
  user-select:text}
select:focus,input:focus{outline:2px solid var(--blue);outline-offset:1px}
input.num{width:78px;text-align:right;font-variant-numeric:tabular-nums}
.kc{background:#5a5a5e;border-radius:6px;padding:3px 9px;font-size:13px;cursor:pointer;
  border:0;color:var(--ink);font:inherit}
.kc:hover{background:#6a6a6e}
.tags{display:flex;flex-wrap:wrap;gap:5px;justify-content:flex-end;max-width:330px}
.tag{background:#5f5f63;border-radius:6px;padding:3px 8px;font-size:12px;display:flex;
  align-items:center;gap:6px}
.tag button{background:none;border:0;color:var(--ink3);cursor:pointer;font-size:13px;
  padding:0;line-height:1}
.tag button:hover{color:var(--bad)}
.addtag{background:none;border:1px dashed #75757a;color:var(--ink2);border-radius:6px;
  padding:3px 8px;font-size:12px;cursor:pointer;font-family:inherit}
#toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%) translateY(30px);
  background:#1c1c1e;border:1px solid #3a3a3c;border-radius:8px;padding:8px 14px;
  font-size:12.5px;opacity:0;transition:opacity .18s,transform .18s;pointer-events:none;
  max-width:70vw;text-align:center}
#toast.show{opacity:1;transform:translateX(-50%)}
#toast.bad{border-color:var(--bad);color:#ffd4d4}
#toast.good{border-color:rgba(95,191,127,.5);color:#d6f5e2}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<aside>{{NAV}}<div class="foot">Everything stays on this Mac.<br>Nothing is uploaded.</div></aside>
<main>{{PANES}}</main>
<div id="toast" role="status" aria-live="polite"></div>
<script>
"use strict";
const CONTROLS = {{CONTROLS}};
let VALUES = {};
let waiting = null;

function send(msg){
  return new Promise((resolve) => {
    waiting = resolve;
    // JSON text, not an object: see settings_api.handle for why.
    window.webkit.messageHandlers.va.postMessage(JSON.stringify(msg));
  });
}
// Python calls this back with the reply.
window.vaReply = function(reply){
  const done = waiting; waiting = null;
  if (done) done(reply);
};

function toast(text, kind){
  const el = document.getElementById("toast");
  el.textContent = text;
  el.className = "show " + (kind || "");
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = ""; }, kind === "bad" ? 4200 : 1600);
}

async function save(key, value){
  const reply = await send({action:"save", updates:{[key]: value}});
  if (!reply.ok){
    toast(reply.error, "bad");
    render();                       // snap the control back to the truth
    return false;
  }
  Object.assign(VALUES, reply.data.changed);
  if (Object.keys(reply.data.changed).length) toast("Saved", "good");
  return true;
}

function control(key){
  const spec = CONTROLS[key], value = VALUES[key];
  if (spec.kind === "toggle"){
    const b = document.createElement("button");
    b.className = "tog"; b.type = "button"; b.role = "switch";
    b.setAttribute("aria-checked", value ? "true" : "false");
    b.setAttribute("aria-label", spec.label);
    b.onclick = async () => { await save(key, !VALUES[key]); render(); };
    b.appendChild(document.createElement("i"));
    return [b];
  }
  if (spec.kind === "choice"){
    const s = document.createElement("select");
    s.setAttribute("aria-label", spec.label);
    for (const opt of spec.choices){
      const o = document.createElement("option");
      o.value = opt; o.textContent = opt;
      if (opt === value) o.selected = true;
      s.appendChild(o);
    }
    if (!spec.choices.includes(value)){
      const o = document.createElement("option");
      o.value = value; o.textContent = value + "  (from your file)";
      o.selected = true; s.appendChild(o);
    }
    s.onchange = async () => { await save(key, s.value); render(); };
    return [s];
  }
  if (spec.kind === "seconds"){
    const i = document.createElement("input");
    i.type = "text"; i.className = "num"; i.value = String(value);
    i.setAttribute("aria-label", spec.label);
    i.onchange = async () => {
      const n = Number(i.value);
      if (!i.value.trim() || Number.isNaN(n)){
        toast(spec.label + " must be a number.", "bad"); render(); return;
      }
      await save(key, n); render();
    };
    const unit = document.createElement("span");
    unit.textContent = "s"; unit.style.color = "var(--ink3)";
    unit.style.fontSize = "12px";
    return [i, unit];
  }
  if (spec.kind === "keycap"){
    const b = document.createElement("button");
    b.className = "kc"; b.type = "button"; b.textContent = value;
    b.title = "Change it with: python -m vocal_advantage --set-hotkey";
    b.onclick = () => toast(
      "Change the hotkey from the tray menu, or run --set-hotkey. " +
      "It has to capture the key you press.", "");
    return [b];
  }
  if (spec.kind === "list"){
    const wrap = document.createElement("div");
    wrap.className = "tags";
    (value || []).forEach((name, idx) => {
      const t = document.createElement("span");
      t.className = "tag"; t.textContent = name;
      const x = document.createElement("button");
      x.type = "button"; x.textContent = "×";
      x.setAttribute("aria-label", "Remove " + name);
      x.onclick = async () => {
        const next = (VALUES[key] || []).slice();
        next.splice(idx, 1);
        await save(key, next); render();
      };
      t.appendChild(x); wrap.appendChild(t);
    });
    const add = document.createElement("button");
    add.className = "addtag"; add.type = "button"; add.textContent = "+ Add app";
    add.onclick = async () => {
      const name = window.prompt("Application name (part of it is enough):");
      if (!name || !name.trim()) return;
      const next = (VALUES[key] || []).slice();
      next.push(name.trim().toLowerCase());
      await save(key, next); render();
    };
    wrap.appendChild(add);
    return [wrap];
  }
  const i = document.createElement("input");
  i.type = "text"; i.value = String(value);
  i.setAttribute("aria-label", spec.label);
  i.onchange = async () => { await save(key, i.value); render(); };
  return [i];
}

function render(){
  document.querySelectorAll(".row").forEach(row => {
    const key = row.dataset.key, slot = row.querySelector(".ctl");
    slot.textContent = "";
    for (const el of control(key)) slot.appendChild(el);
  });
}

document.querySelectorAll(".nav").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".nav").forEach(b =>
      b.setAttribute("aria-selected", b === btn ? "true" : "false"));
    document.querySelectorAll(".pane").forEach(p =>
      p.hidden = (p.id !== "pane-" + btn.dataset.pane));
  };
});

(async function start(){
  const reply = await send({action:"read"});
  if (!reply.ok){ toast(reply.error, "bad"); return; }
  VALUES = reply.data.values;
  render();
})();
</script></body></html>
"""
