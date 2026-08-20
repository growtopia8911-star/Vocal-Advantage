"""The Windows system-tray icon: pystray, with two menu items and nothing else.

pystray here and a native NSStatusItem on macOS, because the two platforms have
genuinely different problems (see `tray_mac.py` for why that one is native).
Windows has none of them: `run()` is happy on whichever thread calls it, and
there is no template-image mechanism to miss out on -- the icon carries its own
contrast instead, which `tray_icon.make_icon(template=False)` handles.

**This owns the main thread on Windows.** That is the point of rendering the
Flow Bar with `UpdateLayeredWindow` rather than tkinter: with Tk gone, nothing
else wants the main thread, so the tray can have it and the layout matches
macOS.

Untested by unit tests, by instruction -- there is no useful assertion to make
about a tray icon. It is on the Windows hand-check list.
"""

from __future__ import annotations

from vocal_advantage.console import warn
from vocal_advantage.tray_icon import ICON_SIZE, make_icon

try:  # pragma: no cover - pystray is a win32-only dependency
    import pystray
except ImportError:  # pragma: no cover - not Windows, or not installed
    pystray = None


class TrayIcon:
    """The tray presence. `run()` blocks, so it is what the main thread does."""

    def __init__(self, indicator, on_quit, on_toggle_move=None, is_movable=None) -> None:
        self._indicator = indicator
        self._on_quit = on_quit
        #: Optional: without both, the "Move bar" item is left out entirely
        #: rather than shown doing nothing.
        self._on_toggle_move = on_toggle_move
        self._is_movable = is_movable
        self._icon = None

    def _status(self, _item) -> str:
        """Text for the status line, re-evaluated each time the menu opens.

        A callable rather than a fixed string: pystray rebuilds the menu on
        open, so this is read exactly when it is about to be looked at.
        """
        try:
            return self._indicator.status_text()
        except Exception:  # noqa: BLE001 - the menu must still open
            return "Unknown"

    def _move_title(self, _item) -> str:
        # Says what clicking will DO, not what the state is, so it reads as a
        # verb -- and re-evaluated on open, like the status line.
        return "Lock bar in place" if self._is_movable() else "Move bar"

    def _toggle_move(self, _icon=None, _item=None) -> None:
        self._on_toggle_move()

    def _build(self):
        items = [
            # enabled=False makes it a label. A status line that highlights
            # under the pointer looks like something you failed to click.
            pystray.MenuItem(self._status, None, enabled=False),
        ]
        if self._on_toggle_move is not None and self._is_movable is not None:
            items.append(pystray.MenuItem(self._move_title, self._toggle_move))
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Vocal Advantage", self._quit),
        ]
        menu = pystray.Menu(*items)
        return pystray.Icon(
            "vocal-advantage",
            icon=make_icon(ICON_SIZE, template=False),
            title="Vocal Advantage",
            menu=menu,
        )

    def _quit(self, _icon=None, _item=None) -> None:
        self._on_quit()

    def run(self) -> None:
        """Enter the tray's message loop. Blocks until `stop()`."""
        if pystray is None:
            raise RuntimeError(
                "pystray is not installed, so there is no tray icon. "
                'Re-run the install step from the README: pip install -e ".[dev]"'
            )
        self._icon = self._build()
        self._icon.run()

    def stop(self) -> None:
        """Leave the message loop and remove the icon. Safe to call twice."""
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001 - shutting down regardless
            warn("Could not remove the tray icon cleanly.")
        self._icon = None
