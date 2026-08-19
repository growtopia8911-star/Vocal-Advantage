"""Manual check for the recording pill. Not a pytest test.

Run from the repo root with the venv active:

    python tools\\indicator_demo.py

(`import vocal_advantage` resolves because Task 1 installed the project into
the venv with `pip install -e ".[dev]"`.)

It walks the pill through every state on a timer, leaving three seconds at the
start to click into Notepad first. See the checklists in Steps 8, 9 and 10.

Windows only: _TkPill reaches straight for user32, which is None off Windows.
"""
import tkinter as tk

from vocal_advantage.indicator_win import (
    PUMP_INTERVAL_MS,
    Indicator,
    set_dpi_awareness,
)


def main() -> None:
    # FIRST statement, before any window exists. main.py must do the same;
    # nothing inside indicator_win calls this for us, on purpose.
    set_dpi_awareness()

    root = tk.Tk()
    root.withdraw()              # the pill is a Toplevel; the root stays unseen

    indicator = Indicator(root)
    root.after(PUMP_INTERVAL_MS, indicator.pump)

    script = [
        (3000, indicator.show_recording),
        (6000, indicator.show_processing),
        (9000, lambda: indicator.flash("nothing heard")),
        (12000, indicator.hide),
        (14000, root.destroy),
    ]
    for delay_ms, action in script:
        root.after(delay_ms, action)

    print("Click into Notepad now. The pill appears in 3 seconds.")
    root.mainloop()


if __name__ == "__main__":
    main()
