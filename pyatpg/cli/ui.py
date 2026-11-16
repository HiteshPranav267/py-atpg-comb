"""Terminal helpers for the ``pyatpg`` command: colour, tables, a progress line.

Colour is used only on a terminal (never when output is piped or redirected),
and ``NO_COLOR`` turns it off everywhere. Only ASCII is printed, so reports
also work in a Windows console with a legacy code page.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Sequence


def _enable_windows_ansi() -> bool:
    """Turn on ANSI escape handling in a Windows console; False if it cannot be."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - no colour is a fine outcome
        return False


def _colour_wanted(stream) -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name == "nt":
        return _enable_windows_ansi()
    return True


COLOUR = _colour_wanted(sys.stdout)

_CODES = {
    "bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36",
}


def style(text: str, *names: str) -> str:
    """``text`` in the given styles (``bold``, ``green``...) when colour is on."""
    if not COLOUR or not names:
        return str(text)
    codes = ";".join(_CODES[n] for n in names if n in _CODES)
    return f"\033[{codes}m{text}\033[0m"


def ok(text: str) -> str:
    return f"{style('[ok]', 'green', 'bold')} {text}"


def warn(text: str) -> str:
    return f"{style('[!!]', 'yellow', 'bold')} {text}"


def bad(text: str) -> str:
    return f"{style('[xx]', 'red', 'bold')} {text}"


def skip(text: str) -> str:
    return f"{style('[--]', 'dim')} {text}"


def heading(text: str) -> None:
    print()
    print(style(text, "bold", "cyan"))
    print(style("-" * len(text), "cyan"))


def table(headers: Sequence[str], rows: Sequence[Sequence[object]], *,
          align: str | None = None, indent: int = 2,
          highlight: Sequence[bool] | None = None) -> None:
    """Print a plain-text table. ``align`` has one ``l``/``r`` per column."""
    cells = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(c))
    align = align or "l" * len(headers)
    pad = " " * indent

    def fmt(row: Sequence[str]) -> str:
        parts = [c.rjust(w) if a == "r" else c.ljust(w) for c, w, a in zip(row, widths, align)]
        return pad + "  ".join(parts).rstrip()

    print(style(fmt(headers), "bold"))
    print(pad + "  ".join("-" * w for w in widths))
    for i, row in enumerate(cells):
        line = fmt(row)
        print(style(line, "green") if highlight and highlight[i] else line)


@contextmanager
def progress(enabled: bool = True) -> Iterator[None]:
    """Show the analysis's current stage on one updating line of stderr.

    Uses :mod:`pyatpg.core.progress`, the same stage reports the web app
    polls. Shown only when stderr is a terminal; otherwise a no-op.
    """
    if not enabled or not sys.stderr.isatty():
        yield
        return
    from pyatpg.core import progress as tracker

    pid = f"cli-{uuid.uuid4().hex[:12]}"
    stop = threading.Event()
    spinner = "|/-\\"

    def draw() -> None:
        tick = 0
        width = 0
        while not stop.wait(0.2):
            snap = tracker.snapshot(pid)
            if not snap:
                continue
            line = f" {spinner[tick % 4]} [{snap['elapsed_s']:>5.0f} s] {snap['stage']}"
            if snap.get("thought"):
                line += f"  ({snap['thought'][:60]})"
            line = line[:110]
            sys.stderr.write("\r" + line.ljust(width))
            sys.stderr.flush()
            width = len(line)
            tick += 1
        sys.stderr.write("\r" + " " * (width + 2) + "\r")
        sys.stderr.flush()

    thread = threading.Thread(target=draw, daemon=True)
    started = time.monotonic()
    with tracker.track(pid):
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1)
    elapsed = time.monotonic() - started
    if elapsed > 2:
        sys.stderr.write(style(f"  done in {elapsed:.0f} s\n", "dim"))
