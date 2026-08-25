"""The settings window on macOS: an NSWindow with a WKWebView inside it.

A real window -- own title bar, ⌘W closes it, no browser, no address bar, no
tab. The page inside happens to be HTML, which is why one design serves both
platforms instead of being hand-built twice in two native widget toolkits.

**There is no server.** The page reaches Python through WKWebView's script
message bridge: JavaScript posts to ``window.webkit.messageHandlers.va``, this
file answers by evaluating ``window.vaReply(...)`` back into the page. Nothing
listens on a port, so there is nothing for another process on the machine to
talk to, and the app keeps working with the network off.

Main thread only, like everything AppKit. The window is opened from the tray
menu, which already runs there.

Loaded lazily by `main`, never at import: pulling WebKit in costs time the
startup path should not pay for a window most launches never open.
"""

from __future__ import annotations

import json

from vocal_advantage.console import warn
from vocal_advantage.settings_api import handle
from vocal_advantage.settings_page import page

try:  # pragma: no cover - absence is only reachable off macOS
    import objc
    from AppKit import (
        NSApplication,
        NSBackingStoreBuffered,
        NSColor,
        NSTitledWindowMask,
        NSWindow,
    )
    from Foundation import NSMakeRect, NSObject
    from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration
except ImportError:  # pragma: no cover - not macOS, or WebKit not installed
    objc = None
    NSApplication = NSBackingStoreBuffered = NSColor = NSWindow = None
    NSTitledWindowMask = 0
    NSMakeRect = None
    NSObject = object
    WKUserContentController = WKWebView = WKWebViewConfiguration = None

# Style mask spelled out: closable + titled + resizable, no minimise. A settings
# window that can be sent to the Dock from an app with no Dock icon is a window
# you cannot get back.
NSWindowStyleMaskTitled = 1 << 0
NSWindowStyleMaskClosable = 1 << 1
NSWindowStyleMaskResizable = 1 << 3

WIDTH = 760.0
HEIGHT = 560.0

#: Module-level so the window and its bridge outlive the function that built
#: them. Without this the whole thing is collected the moment `open_settings`
#: returns and the window vanishes as it appears.
_window = None
_bridge = None


class _Bridge(NSObject):
    """Receives what the page posts, and answers it.

    Every reply goes back as JSON through one call, so the page has a single
    entry point and no way to be handed a half-built object.
    """

    def initWithPath_(self, config_path):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self._path = config_path
        self._view = None
        return self

    def setView_(self, view) -> None:
        self._view = view

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        try:
            reply = handle(message.body(), self._path)
        except Exception as exc:  # noqa: BLE001 - the window must keep working
            reply = {"ok": False, "error": f"Could not read the settings: {exc}"}
        if self._view is None:
            return
        # json.dumps twice: once to make the reply, once to make that string a
        # JavaScript string literal. Doing it by hand is how quotes in a
        # filename or an error message break the page.
        script = "window.vaReply(JSON.parse(%s))" % json.dumps(json.dumps(reply))
        try:
            self._view.evaluateJavaScript_completionHandler_(script, None)
        except Exception:  # noqa: BLE001
            warn("The settings window stopped responding.")


def open_settings(config_path) -> None:
    """Show the settings window, building it the first time. Main thread only."""
    global _window, _bridge

    if WKWebView is None:
        warn("The settings window needs pyobjc-framework-WebKit:")
        warn('    pip install "pyobjc-framework-WebKit"')
        return

    if _window is not None:
        # Already built: just bring it back rather than stacking a second one.
        _window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        return

    try:
        _bridge = _Bridge.alloc().initWithPath_(config_path)

        controller = WKUserContentController.alloc().init()
        controller.addScriptMessageHandler_name_(_bridge, "va")
        config = WKWebViewConfiguration.alloc().init()
        config.setUserContentController_(controller)

        frame = NSMakeRect(0, 0, WIDTH, HEIGHT)
        view = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        _bridge.setView_(view)
        view.loadHTMLString_baseURL_(page(), None)

        _window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        _window.setTitle_("Vocal Advantage")
        _window.setContentView_(view)
        _window.setReleasedWhenClosed_(False)   # reopened from the menu later
        _window.center()
        _window.makeKeyAndOrderFront_(None)

        # An accessory app has no Dock icon, so nothing else will bring this
        # window forward. Stealing focus is correct here and only here: the
        # user asked for it from the menu.
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:  # noqa: BLE001 - dictation matters more than settings
        _window = None
        _bridge = None
        warn("The settings window could not be opened.")
        import traceback

        warn(traceback.format_exc())
