"""Windows console helpers (no-ops elsewhere).

- Closing the console window, logging off or shutting down ends the process as soon
  as the console control handler returns, so Python's own SIGBREAK handler never gets
  to run. on_console_close() registers a native handler that runs the cleanup first
  (Windows allows about 5 s).
- QuickEdit mode: a click in the console window pauses every write to it until a key
  is pressed, which would freeze any thread that logs. disable_quickedit() turns it off.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT = 2, 5, 6
ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x40, 0x80
STD_INPUT_HANDLE = -10
_keep = []          # the ctypes callbacks must outlive the registration


def _kernel32():
    import ctypes
    return ctypes.WinDLL("kernel32", use_last_error=True)


def on_console_close(fn, kernel32=None) -> bool:
    """Call fn() before Windows ends the process on console close / logoff / shutdown.
    Only for a process with a console window (the boot task runs windowless)."""
    if os.name != "nt" and kernel32 is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k = kernel32 or _kernel32()
        if not k.GetConsoleWindow():
            return False

        def handler(event):
            if event in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("cleanup on console close failed")
                return True
            return False           # Ctrl+C / Ctrl+Break: Python's signal handlers

        cb = (ctypes.WINFUNCTYPE if hasattr(ctypes, "WINFUNCTYPE") else ctypes.CFUNCTYPE)(
            wintypes.BOOL, wintypes.DWORD)(handler)
        _keep.append((cb, handler))
        return bool(k.SetConsoleCtrlHandler(cb, True))
    except (OSError, AttributeError) as e:
        log.debug("console close handler not installed: %s", e)
        return False


def disable_quickedit(kernel32=None) -> bool:
    """Turn off QuickEdit on this process's console input, if it has one."""
    if os.name != "nt" and kernel32 is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k = kernel32 or _kernel32()
        h = k.GetStdHandle(STD_INPUT_HANDLE)
        mode = wintypes.DWORD()
        if not h or not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False           # not a console (pythonw, redirected input)
        new = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
        return new == mode.value or bool(k.SetConsoleMode(h, new))
    except (OSError, AttributeError) as e:
        log.debug("QuickEdit not changed: %s", e)
        return False


def fine_sleep(winmm=None) -> bool:
    """Python 3.10 on Windows sleeps in 15.6 ms steps (3.11+ uses a high-resolution
    timer), so 30-60 fps pacing can't be held. Ask for 1 ms timer ticks for this process
    (Windows undoes it when the process exits)."""
    import sys
    if winmm is None and (os.name != "nt" or sys.version_info >= (3, 11)):
        return False
    try:
        import ctypes
        return (winmm or ctypes.WinDLL("winmm")).timeBeginPeriod(1) == 0
    except (OSError, AttributeError):
        return False
