"""Double-click launcher for Windows. No console window.

The .pyw extension is what does it: Windows associates it with pythonw.exe,
which has no console attached. That is also why every message in this project
goes through vocal_advantage.console.say() -- under pythonw, sys.stdout and
sys.stderr are None, and a bare print() raises AttributeError on the first
status line, before the hotkey is ever hooked.

`python -m vocal_advantage` still works exactly as before, with output on the
console, and is the right thing to run when something needs debugging.
"""

import sys
from pathlib import Path

# So a double-click from anywhere resolves `import vocal_advantage` without the
# package having to be installed first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vocal_advantage.main import main

if __name__ == "__main__":
    sys.exit(main())
