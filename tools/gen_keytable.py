"""Generate vocal_advantage/_key_names.py from the installed keyboard library.

Run on Windows, where `keyboard` imports and key_to_scan_codes can consult the
real OS layout. The output is committed so macOS -- where the library refuses
to import without root -- gets the same answers.
"""

import io
import pprint

import keyboard
import keyboard._canonical_names as cn

aliases = {k: v for k, v in cn.canonical_names.items() if k != v}

# Candidates must come from the OS layout too, not just the alias table:
# plenty of real keys ("f8", "right alt") have no alias entry at all, so
# walking canonical_names alone silently omits them.
keyboard._os_keyboard.init()
candidates = set(cn.canonical_names.values()) | set(cn.canonical_names)
candidates |= set(keyboard._os_keyboard.from_name)

valid = []
for name in sorted(candidates):
    try:
        keyboard.key_to_scan_codes(name)
        valid.append(name)
    except Exception:
        pass

# Aliases that point at a name we consider valid are the only useful ones.
valid_set = set(valid)
aliases = {k: v for k, v in sorted(aliases.items()) if v in valid_set}

header = '''"""Canonical key names, vendored from the ``keyboard`` library.

GENERATED FILE -- do not hand-edit. Regenerate with the script in the repo's
task notes if the pinned ``keyboard`` version ever changes.

Why this exists: ``keyboard`` refuses to import on macOS unless the process is
root, and it resolves key names against the live OS layout. Both are fine on
Windows, where the app actually runs -- but the *portable* half of the project
(``hotkey_spec``, ``config``, ``controller``) has to stay importable and
testable on a Mac, which is where the recorder and the state machine get built.

So the name table is captured from the real library on Windows and committed.
``hotkey_spec`` prefers the live library whenever it can be imported and only
falls back to this table when it cannot, and
``test_vendored_key_table_matches_the_library`` fails on Windows if the two
ever drift apart.
"""

'''

with io.open("vocal_advantage/_key_names.py", "w", encoding="utf-8", newline="\n") as f:
    f.write(header)
    f.write("#: Alias -> canonical name (only entries where the two differ).\n")
    f.write("ALIASES: dict[str, str] = ")
    f.write(pprint.pformat(aliases, indent=4, width=88, sort_dicts=True))
    f.write("\n\n")
    f.write("#: Every key name the Windows layout accepts, canonical spelling.\n")
    f.write("VALID_NAMES: frozenset[str] = frozenset(\n")
    f.write(pprint.pformat(set(valid), indent=4, width=88))
    f.write("\n)\n")

print(f"aliases: {len(aliases)}  valid: {len(valid)}")
