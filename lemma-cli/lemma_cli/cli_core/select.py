from __future__ import annotations

import os
import select
import sys
from dataclasses import dataclass, replace
from typing import Any

import typer
from rich.markup import escape

from .state import console, fail


def item_label(item: dict[str, Any], fallback: str = "") -> str:
    name = (
        item.get("name")
        or item.get("title")
        or item.get("slug")
        or item.get("id")
        or fallback
    )
    subtitle = item.get("slug") or item.get("id")
    return f"{name} ({subtitle})" if subtitle and subtitle != name else str(name)


def _arrows_available() -> bool:
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401

        return True
    except ImportError:
        return False


def select_from_items(
    items: list[dict[str, Any]],
    *,
    label: str,
    current_id: str | None = None,
) -> dict[str, Any]:
    if not items:
        fail(f"No {label}s found.")
    if len(items) == 1:
        return items[0]
    if sys.stdin.isatty() and sys.stdout.isatty() and _arrows_available():
        return _select_with_arrows(items, label=label, current_id=current_id)
    return _select_by_number(items, label=label)


def _select_by_number(items: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    console.print(f"[bold]Select {label}[/bold]")
    for index, item in enumerate(items, start=1):
        console.print(f"{index:>2}. {escape(item_label(item, str(index)))}")
    while True:
        selected = typer.prompt(f"{label.title()} number", type=int)
        if 1 <= selected <= len(items):
            return items[selected - 1]
        console.print(f"[red]Choose a number from 1 to {len(items)}.[/red]")


def _select_with_arrows(
    items: list[dict[str, Any]],
    *,
    label: str,
    current_id: str | None,
) -> dict[str, Any]:
    import termios
    import tty

    state = _MenuState(selected=_initial_index(items, current_id))
    fd = sys.stdin.fileno()
    keys = _KeyReader(fd)
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            visible = filter_items(items, state.filter_text)
            _render_arrow_selector(
                visible,
                label=label,
                selected=state.selected,
                filter_text=state.filter_text,
            )
            state, done = _apply_key(_read_key(keys), state, len(visible))
            if done == "pick":
                console.print()
                return visible[state.selected]
            if done == "cancel":
                raise typer.Exit(code=1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


@dataclass(frozen=True)
class _MenuState:
    filter_text: str = ""
    selected: int = 0


def filter_items(items: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """Items whose displayed label contains ``text``, ignoring case."""
    needle = text.casefold()
    return [item for item in items if needle in item_label(item).casefold()]


#: How long to wait for the next byte of an escape sequence. A lone Esc press
#: sends one byte; an arrow key sends its whole sequence at once, so this wait
#: is only ever spent on a real Esc.
_ESC_WAIT_SECONDS = 0.05

#: Longest escape sequence read before giving up. Real key sequences are far
#: shorter; the bound stops a stream of parameter bytes from looping forever.
_MAX_ESCAPE_BYTES = 16


class _KeyReader:
    """Bytes from the terminal, with one byte of pushback.

    Reads the fd directly, because sys.stdin buffers and select() cannot see
    bytes that are already inside that buffer.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._pushed = b""

    def read(self) -> bytes:
        """The next byte, waiting for it. Empty at end of input."""
        if self._pushed:
            byte, self._pushed = self._pushed, b""
            return byte
        return os.read(self._fd, 1)

    def read_within_wait(self) -> bytes | None:
        """The next byte if one arrives within the escape wait, else None."""
        if self._pushed:
            return self.read()
        if not select.select([self._fd], [], [], _ESC_WAIT_SECONDS)[0]:
            return None
        return os.read(self._fd, 1) or None

    def push_back(self, byte: bytes) -> None:
        """Hand a byte back, to be read again as the start of the next key."""
        self._pushed = byte


def _read_key(keys: _KeyReader) -> str:
    """One key press, whole: a character or a complete escape sequence.

    A read may return fewer bytes than asked for, so multi-byte input is
    gathered byte by byte.
    """
    first = keys.read()
    if not first:
        return "\x04"  # end of input, treated like Ctrl+D
    if first == b"\x1b":
        return _read_escape(keys)
    return _read_utf8(keys, first).decode(errors="ignore")


def _read_utf8(keys: _KeyReader, data: bytes) -> bytes:
    """``data`` plus the UTF-8 continuation bytes that follow its lead byte.

    Stops early when nothing more arrives, or when the next byte is not a
    continuation byte: a stray lead byte must not swallow the key presses that
    follow it, so that byte is pushed back to start the next key.
    """
    for _ in range(_utf8_tail_length(data[0])):
        byte = keys.read_within_wait()
        if byte is None:
            break
        if not 0x80 <= byte[0] <= 0xBF:
            keys.push_back(byte)
            break
        data += byte
    return data


def _read_escape(keys: _KeyReader) -> str:
    """The rest of an escape sequence, after its first byte.

    A lone Esc returns at once. ``Esc [`` starts a CSI sequence, which runs to
    its final byte (so ``Esc [ 3 ~`` for Delete is consumed whole and its tail
    never lands in the filter). ``Esc O`` is the same for application mode.
    """
    sequence = b"\x1b"
    second = keys.read_within_wait()
    if second is None:
        return "\x1b"
    sequence += second
    if second == b"O":
        third = keys.read_within_wait()
        if third is not None:
            sequence += third
    elif second == b"[":
        while len(sequence) < _MAX_ESCAPE_BYTES:
            byte = keys.read_within_wait()
            if byte is None:
                break
            sequence += byte
            if 0x40 <= byte[0] <= 0x7E:  # final byte ends the sequence
                break
    return sequence.decode(errors="ignore")


def _utf8_tail_length(lead_byte: int) -> int:
    """Continuation bytes still to read after a UTF-8 lead byte."""
    if lead_byte >= 0xF0:
        return 3
    if lead_byte >= 0xE0:
        return 2
    if lead_byte >= 0xC0:
        return 1
    return 0


#: Up and down in normal and application cursor mode.
_ARROW_STEPS = {"\x1b[A": -1, "\x1b[B": 1, "\x1bOA": -1, "\x1bOB": 1}


def _apply_key(
    key: str, state: _MenuState, visible_count: int
) -> tuple[_MenuState, str | None]:
    """The next state and, if the menu is finished, how: pick or cancel.

    ``visible_count`` is the number of items the current filter leaves.
    Letters go into the filter, so only Esc, Ctrl+C and Ctrl+D cancel.
    """
    if key in {"\r", "\n"}:
        return state, "pick" if visible_count else None
    if key in {"\x03", "\x04", "\x1b"}:
        return state, "cancel"
    if key in _ARROW_STEPS and visible_count:
        step = _ARROW_STEPS[key]
        return replace(state, selected=(state.selected + step) % visible_count), None
    if key in {"\x7f", "\x08"}:
        return replace(state, filter_text=state.filter_text[:-1], selected=0), None
    if len(key) == 1 and key.isprintable():
        return replace(state, filter_text=state.filter_text + key, selected=0), None
    return state, None


def _initial_index(items: list[dict[str, Any]], current_id: str | None) -> int:
    if current_id:
        for index, item in enumerate(items):
            if str(item.get("id")) == current_id:
                return index
    return 0


def _render_arrow_selector(
    items: list[dict[str, Any]],
    *,
    label: str,
    selected: int,
    filter_text: str = "",
) -> None:
    output = sys.stdout
    output.write("\033[2J\033[H")
    output.write(f"Select {label} (type to filter, up/down, enter, esc to cancel)\n")
    if filter_text:
        output.write(f"Filter: {filter_text}\n")
    if not items:
        output.write("  No matches\n")
    for index, item in enumerate(items):
        prefix = ">" if index == selected else " "
        line = f"{prefix} {item_label(item, str(index + 1))}"
        if index == selected:
            line = f"\033[7m{line}\033[0m"
        output.write(f"{line}\n")
    output.flush()
