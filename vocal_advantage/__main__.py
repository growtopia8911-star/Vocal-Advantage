"""Makes `python -m vocal_advantage` work.

Deliberately a shim. Python runs this file as a module named "__main__", so
anything defined here would exist twice the moment another module imports
vocal_advantage.__main__. Keeping the wiring in main.py avoids that and lets the
tests import it without launching the app.
"""

import sys

from vocal_advantage.main import main

if __name__ == "__main__":
    sys.exit(main())
