"""The macOS menu-bar icon: an NSStatusItem with two items and nothing else.

Native rather than pystray, for two specific reasons rather than taste:

* pystray's own docs say ``run()`` "must be called from the main thread" on
  macOS, and ``run_detached()`` "requires providing an NSApplication instance".
  The Flow Bar already needs an ``NSApplication`` on the main thread, so both
  want the same thread and one of them has to give way. Thirty lines here
  removes the contention instead of negotiating with it.
* **Template images.** ``setTemplate_(True)`` hands the icon to macOS, which
  recolours it for whichever menu-bar appearance is in force -- so it is right
  on a dark bar and a light one by construction, with nothing to detect. pystray
  does not expose that, and the fallback is a hand-tuned image that is a
  compromise in both.

`rumps` was rejected for a duller reason: it wraps only this half, and raw
PyObjC is required for the panel regardless, so it would be a dependency that
saves nothing.

The menu is deliberately two items -- a status line that cannot be clicked, and
Quit. Everything else is edited in config.json.
"""

from __future__ import annotations

import io

from vocal_advantage.console import warn
from vocal_advantage.tray_icon import make_icon

try:  # pragma: no cover - absence is only reachable off macOS
    import objc
    from AppKit import (
        NSImage,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSData, NSObject
except ImportError:  # pragma: no cover - not macOS
    objc = None
    NSImage = NSMenu = NSMenuItem = NSStatusBar = None
    NSVariableStatusItemLength = -1
    NSData = None
    NSObject = object

#: Points. The menu bar is 22pt tall on every Mac; 18 leaves the padding Apple's
#: own items use, and the icon is drawn at 4x and downscaled so it stays crisp.
ICON_POINTS = 18


def _ns_image(size: int = ICON_POINTS) -> NSImage:
    """The generated icon as a template NSImage.

    PNG in memory rather than a file on disk: the icon is generated precisely so
    that no binaries live in the repository, and writing one to a temp file to
    read it straight back would give that up for nothing.
    """
    buffer = io.BytesIO()
    # Drawn at 2x for Retina; setSize_ below tells AppKit the point size, and it
    # picks the pixels up as a @2x representation.
    make_icon(size * 2, template=True).save(buffer, format="PNG")
    image = NSImage.alloc().initWithData_(
        NSData.dataWithBytes_length_(buffer.getvalue(), len(buffer.getvalue()))
    )
    image.setSize_((size, size))
    # The whole reason this file is not pystray: macOS recolours a template
    # image for the current menu-bar appearance, so dark and light are both
    # correct without detecting either.
    image.setTemplate_(True)
    return image


class _TrayTarget(NSObject):
    """Action target and menu delegate. Cocoa needs a real object for both."""

    def initWithIndicator_onQuit_(self, indicator, on_quit):
        self = objc.super(_TrayTarget, self).init()
        if self is None:
            return None
        self._indicator = indicator
        self._on_quit = on_quit
        self._status_item = None
        return self

    def setStatusMenuItem_(self, item) -> None:
        self._status_item = item

    def menuNeedsUpdate_(self, _menu) -> None:
        """Refresh the status line as the menu opens.

        Pulled here rather than pushed on a timer: the text is only ever read
        while the menu is on screen, so a timer would be work done once a second
        forever to keep a string nobody is looking at up to date.
        """
        if self._status_item is None:
            return
        try:
            self._status_item.setTitle_(self._indicator.status_text())
        except Exception:  # noqa: BLE001 - a menu must still open
            self._status_item.setTitle_("Unknown")

    def quit_(self, _sender) -> None:
        self._on_quit()


class TrayIcon:
    """The menu-bar presence. Main thread only, like everything in AppKit."""

    def __init__(self, indicator, on_quit) -> None:
        self._indicator = indicator
        self._on_quit = on_quit
        self._item = None
        self._target = None

    def start(self) -> None:
        self._target = _TrayTarget.alloc().initWithIndicator_onQuit_(
            self._indicator, self._on_quit
        )

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._item.button().setImage_(_ns_image())
        self._item.button().setToolTip_("Vocal Advantage")

        menu = NSMenu.alloc().init()
        menu.setDelegate_(self._target)

        status = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            self._indicator.status_text(), None, ""
        )
        # No action and explicitly disabled: it is a label, and a label that
        # highlights under the pointer looks like something you failed to click.
        status.setEnabled_(False)
        menu.addItem_(status)
        self._target.setStatusMenuItem_(status)

        menu.addItem_(NSMenuItem.separatorItem())

        # PyObjC bridges the plain string to a SEL for a selector-typed
        # argument. objc.selector() is for wrapping a *method*, not naming one,
        # and passing it here fails with "argument 'method' must be callable".
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Vocal Advantage", "quit:", "q"
        )
        quit_item.setTarget_(self._target)
        menu.addItem_(quit_item)

        self._item.setMenu_(menu)

    def stop(self) -> None:
        """Take the icon out of the menu bar. Safe to call twice."""
        if self._item is None:
            return
        try:
            NSStatusBar.systemStatusBar().removeStatusItem_(self._item)
        except Exception:  # noqa: BLE001 - shutting down regardless
            warn("Could not remove the menu bar icon cleanly.")
        self._item = None
        self._target = None
