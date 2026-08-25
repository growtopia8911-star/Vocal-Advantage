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
from vocal_advantage.flowbar import IDLE
from vocal_advantage.tray_icon import make_icon

try:  # pragma: no cover - absence is only reachable off macOS
    import objc
    from AppKit import (
        NSImage,
        NSMenu,
        NSMenuItem,
        NSRunLoop,
        NSRunLoopCommonModes,
        NSStatusBar,
        NSTimer,
        NSVariableStatusItemLength,
    )
    from Foundation import NSData, NSObject
except ImportError:  # pragma: no cover - not macOS
    objc = None
    NSImage = NSMenu = NSMenuItem = NSStatusBar = NSTimer = None
    NSRunLoop = NSRunLoopCommonModes = None
    NSVariableStatusItemLength = -1
    NSData = None
    NSObject = object

#: Points. The menu bar is 22pt tall on every Mac; 18 leaves the padding Apple's
#: own items use, and the icon is drawn at 4x and downscaled so it stays crisp.
ICON_POINTS = 18

#: Seconds between status-dot checks. State changes are human-paced -- a key
#: going down, a model finishing -- so eight times a second is already finer
#: than anyone can perceive, and each tick is one string comparison that
#: usually does nothing at all.
TICK_S = 0.12


def _ns_image(size: int = ICON_POINTS, state: str = IDLE) -> NSImage:
    """The generated icon as an NSImage, template only when it can be one.

    PNG in memory rather than a file on disk: the icon is generated precisely so
    that no binaries live in the repository, and writing one to a temp file to
    read it straight back would give that up for nothing.

    **`setTemplate_` is conditional, and that is the whole trick.** A template
    image is black-plus-alpha and macOS recolours all of it for the current
    menu-bar appearance -- which is why the idle icon is correct on a light bar
    and a dark one with nothing to detect, and equally why a status colour put
    into one would be flattened away. So the working states ship as ordinary
    images carrying their own contrast, exactly as the Windows icon always has.
    """
    buffer = io.BytesIO()
    # Drawn at 2x for Retina; setSize_ below tells AppKit the point size, and it
    # picks the pixels up as a @2x representation.
    make_icon(size * 2, template=True, state=state).save(buffer, format="PNG")
    image = NSImage.alloc().initWithData_(
        NSData.dataWithBytes_length_(buffer.getvalue(), len(buffer.getvalue()))
    )
    image.setSize_((size, size))
    image.setTemplate_(state == IDLE)
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
        #: Parallel lists, indexed by the menu item's tag. Cocoa gives every
        #: item one selector, so the tag is how one target serves many items --
        #: which beats inventing a selector per feature.
        self._actions = []
        self._titles = []
        self._items = []
        self._button = None
        self._drawn_state = IDLE
        return self

    def setButton_(self, button) -> None:
        self._button = button

    def tick_(self, _timer) -> None:
        """Repaint the icon when, and only when, the state has actually moved.

        A pull on a timer rather than a push from the Indicator, because the
        Indicator is written to be callable from any thread and AppKit is not:
        a push would have to hop threads to get here. Comparing one string
        eight times a second is cheaper than that machinery.

        The docstring on `menuNeedsUpdate_` argues against exactly this for the
        status *line*, and it is still right about that -- nobody is looking at
        a string inside a closed menu. The dot is the opposite case: it is on
        screen the whole time, and the state it fails to show is "your
        microphone is open".
        """
        if self._button is None:
            return
        try:
            state = self._indicator.state_name()
        except Exception:  # noqa: BLE001 - the icon must not take the app down
            return
        if state == self._drawn_state:
            return
        self._drawn_state = state
        try:
            self._button.setImage_(_ns_image(state=state))
        except Exception:  # noqa: BLE001 - as above
            warn("Could not update the menu bar icon.")

    def setStatusMenuItem_(self, item) -> None:
        self._status_item = item

    def addAction_title_item_(self, action, title, item) -> None:
        self._actions.append(action)
        self._titles.append(title)
        self._items.append(item)

    def menuNeedsUpdate_(self, _menu) -> None:
        """Refresh the status line as the menu opens.

        Pulled here rather than pushed on a timer: the text is only ever read
        while the menu is on screen, so a timer would be work done once a second
        forever to keep a string nobody is looking at up to date.
        """
        if self._status_item is not None:
            try:
                self._status_item.setTitle_(self._indicator.status_text())
            except Exception:  # noqa: BLE001 - a menu must still open
                self._status_item.setTitle_("Unknown")

        # Titles that are callables are re-read here, so an item can say what
        # clicking it will DO rather than what the state currently is.
        for title, item in zip(self._titles, self._items):
            if callable(title):
                try:
                    item.setTitle_(title())
                except Exception:  # noqa: BLE001 - a menu must still open
                    pass

    def invoke_(self, sender) -> None:
        index = int(sender.tag())
        if 0 <= index < len(self._actions):
            self._actions[index]()

    def quit_(self, _sender) -> None:
        self._on_quit()


class TrayIcon:
    """The menu-bar presence. Main thread only, like everything in AppKit."""

    def __init__(self, indicator, on_quit, items=()) -> None:
        self._indicator = indicator
        self._on_quit = on_quit
        #: (title, action) pairs between the status line and Quit. `title` may
        #: be a callable, re-read each time the menu opens. A feature whose
        #: action is None is left out entirely rather than shown doing nothing.
        self._extra = [(t, a) for t, a in items if a is not None]
        self._item = None
        self._target = None
        self._timer = None

    def start(self) -> None:
        self._target = _TrayTarget.alloc().initWithIndicator_onQuit_(
            self._indicator, self._on_quit
        )

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._item.button().setImage_(_ns_image())
        self._item.button().setToolTip_("Vocal Advantage")
        self._target.setButton_(self._item.button())

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

        for index, (title, action) in enumerate(self._extra):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title() if callable(title) else title, "invoke:", ""
            )
            item.setTarget_(self._target)
            # The tag is the index into the target's action list: one selector
            # serving every item, rather than a selector invented per feature.
            item.setTag_(index)
            menu.addItem_(item)
            self._target.addAction_title_item_(action, title, item)

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

        # NSRunLoopCommonModes, not the default mode: an open menu puts the run
        # loop into tracking mode, and a default-mode timer stops firing there.
        # The dot would then freeze for exactly as long as the menu is open,
        # which is precisely when someone is looking at it.
        self._timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            TICK_S, self._target, "tick:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, NSRunLoopCommonModes
        )

    def stop(self) -> None:
        """Take the icon out of the menu bar. Safe to call twice."""
        if self._item is None:
            return
        # Invalidated first. A repeating NSTimer holds a strong reference to its
        # target and goes on firing after the status item is gone, which would
        # leave it painting a button that is no longer in the menu bar.
        if self._timer is not None:
            try:
                self._timer.invalidate()
            except Exception:  # noqa: BLE001 - shutting down regardless
                pass
            self._timer = None
        try:
            NSStatusBar.systemStatusBar().removeStatusItem_(self._item)
        except Exception:  # noqa: BLE001 - shutting down regardless
            warn("Could not remove the menu bar icon cleanly.")
        self._item = None
        self._target = None
