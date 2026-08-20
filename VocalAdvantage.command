#!/bin/bash
# Double-click launcher for macOS.
#
# HONEST CAVEAT: this opens a Terminal window and leaves it there. macOS has no
# way for a .command file not to. If you want genuinely no terminal, run
#     python tools/make_mac_app.py
# once and launch VocalAdvantage.app instead -- that also works in Login Items.
#
# Output is left on the console on purpose: if you are double-clicking this
# rather than the .app, seeing what it says is the point.

cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

exec "$PYTHON" -m vocal_advantage
