"""An interactive terminal on a machine, over the process's stream socket.

The local terminal goes into raw mode so every keystroke -- Ctrl-C included --
reaches the remote shell rather than this process. One thread reads the
keyboard and is the only thing that sends on the socket: a resize noticed by
the SIGWINCH handler is handed to it rather than sent from the handler, which
runs on the main thread in the middle of a read.
"""

from __future__ import annotations

import codecs
import os
import select
import shutil
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lemma_sdk import Lemma

#: How often the keyboard thread looks up from waiting, to notice a resize or
#: the end of the session.
_POLL_SECONDS = 0.1


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size()
    return size.columns, size.lines


def run_shell(client: Lemma, ref: str, command: str) -> int:
    """Run ``command`` in a terminal on ``ref`` until it exits; its exit code."""
    import termios
    import tty

    stream = client.machines.exec(ref, command, tty=_terminal_size(), via="socket")
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    saved = termios.tcgetattr(stdin_fd)
    done = threading.Event()
    resized = threading.Event()

    def on_resize(_signum: int, _frame: Any) -> None:
        resized.set()

    def pump_keyboard() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while not done.is_set():
            if resized.is_set():
                resized.clear()
                stream.resize(*_terminal_size())
            ready, _, _ = select.select([stdin_fd], [], [], _POLL_SECONDS)
            if not ready:
                continue
            data = os.read(stdin_fd, 4096)
            if not data:
                return
            text = decoder.decode(data)
            if text:
                try:
                    stream.send_input(text)
                except Exception:  # noqa: BLE001 - the socket closed under us
                    return

    previous = signal.signal(signal.SIGWINCH, on_resize)
    keyboard = threading.Thread(target=pump_keyboard, daemon=True)
    try:
        tty.setraw(stdin_fd)
        keyboard.start()
        for chunk in stream:
            os.write(stdout_fd, chunk.data.encode("utf-8", errors="replace"))
    finally:
        done.set()
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)
        signal.signal(signal.SIGWINCH, previous)
        stream.close()
    code = stream.exit_code
    return (
        code if isinstance(code, int) else (0 if stream.state == "succeeded" else 137)
    )
