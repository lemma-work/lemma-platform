"""Lifecycle coupling for backend processes owned by Lemma locald."""

from __future__ import annotations

import os
import sys
import threading


def _watch_parent_pipe(file_descriptor: int) -> None:
    """Exit when locald closes the inherited stdin pipe.

    Read the file descriptor directly. Holding ``sys.stdin.buffer``'s lock in
    a daemon thread can make CPython abort while an otherwise ordinary backend
    startup failure is shutting the interpreter down.
    """
    try:
        while os.read(file_descriptor, 8192):
            pass
    except OSError:
        return
    os._exit(0)


def install_locald_parent_watchdog() -> None:
    """Exit a managed backend promptly when its owning locald process exits."""
    if os.getenv("LEMMA_LOCALD_PARENT_WATCHDOG") != "1":
        return

    try:
        file_descriptor = sys.stdin.fileno()
    except (AttributeError, OSError):
        return

    threading.Thread(
        target=_watch_parent_pipe,
        args=(file_descriptor,),
        name="lemma-locald-parent-watchdog",
        daemon=True,
    ).start()
