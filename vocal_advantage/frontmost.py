"""Which application is about to receive the paste.

Used for one thing: leaving the cleanup pass switched off in terminals and code
editors. Filler removal is right for prose and wrong for a shell -- it will
happily turn a command into something that does not run, and you will not spot
it until you press Enter.

**One file rather than the project's usual `_win` / `_mac` pair.** Each half is
about fifteen lines, and splitting them into two files plus a chooser would be
more ceremony than code. The rule the pair exists to protect -- that
`main.platform_modules()` is the only place choosing a platform -- is not at
risk here, because nothing about this module's behaviour differs between them:
same function, same return shape, same meaning.

Never raises. A failure to identify the app returns None, and None means "no
match", so the worst case is that cleanup runs where you would rather it had
not -- never a lost dictation.
"""

from __future__ import annotations

import sys

#: Sensible starting point, written to config.json on first run. Matched as
#: case-insensitive substrings (see `matches`), so one list covers both
#: machines: "terminal" catches macOS Terminal and Windows Terminal, "code"
#: catches VS Code on either, and so on. That portability is the whole reason
#: matching is by substring rather than by bundle id or exe path.
DEFAULT_SKIP_CLEANUP_IN: tuple[str, ...] = (
    "terminal",
    "iterm",
    "warp",
    "alacritty",
    "kitty",
    "powershell",
    "cmd",
    "code",          # VS Code, VSCodium, Xcode
    "cursor",
    "pycharm",
    "sublime",
    "vim",
    "emacs",
    "ghostty",
    "antigravity",   # observed as the frontmost app on this machine
)

if sys.platform == "darwin":  # pragma: no cover - exercised by hand
    try:
        from AppKit import NSWorkspace
    except ImportError:
        NSWorkspace = None
else:
    NSWorkspace = None


def matches(app: str | None, patterns) -> bool:
    """True if `app` looks like one of `patterns`.

    Case-insensitive substring, deliberately, and this is the design decision
    worth keeping: the same `config.json` is meant to work on both machines,
    but the two platforms name applications differently -- macOS reports
    "Terminal" and Windows reports "WindowsTerminal.exe". Matching on
    substrings lets one entry, "terminal", cover both. Bundle ids and exe
    paths would each need their own list.

    `app` of None means "could not tell", which must not match anything: the
    safe failure is running cleanup where it was not wanted, not skipping it
    everywhere.
    """
    if not app:
        return False
    lowered = app.lower()
    return any(
        pattern.strip().lower() in lowered
        for pattern in patterns
        if isinstance(pattern, str) and pattern.strip()
    )


def frontmost_app() -> str | None:
    """A name for the application with focus, or None if it cannot be told.

    The string is only ever fed to `matches`, so its exact shape does not
    matter beyond containing something recognisable -- which is why the two
    platforms are free to answer differently.
    """
    try:
        if sys.platform == "darwin":
            return _frontmost_mac()
        if sys.platform == "win32":
            return _frontmost_win()
    except Exception:  # noqa: BLE001 - never worth a dictation
        return None
    return None


def _frontmost_mac() -> str | None:  # pragma: no cover - needs a real desktop
    if NSWorkspace is None:
        return None
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    # Both, joined: the localised name is what a person would write in the
    # config, and the bundle id catches the cases where it differs ("Code" vs
    # com.microsoft.VSCode).
    parts = [app.localizedName(), app.bundleIdentifier()]
    return " ".join(str(p) for p in parts if p) or None


def _frontmost_win() -> str | None:  # pragma: no cover - needs Windows
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None

    # QueryFullProcessImageNameW rather than GetModuleFileNameEx: it needs only
    # PROCESS_QUERY_LIMITED_INFORMATION, which a non-elevated process is
    # granted for most other processes. The older call needs read access to
    # another process's memory and fails for anything running as admin.
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        return buffer.value or None
    finally:
        kernel32.CloseHandle(handle)
