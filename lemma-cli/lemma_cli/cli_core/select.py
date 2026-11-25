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
    name = item.get("name") or item.get("slug") or item.get("id") or fallback
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
            state, done = _apply_key(_read_key(fd), state, len(visible))
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


#: How long to wait after Esc for the rest of an arrow-key sequence. A lone
#: Esc press sends one byte; an arrow sends three at once, so the follow-up
#: bytes are already waiting and this is only ever spent on a real Esc.
_ESC_WAIT_SECONDS = 0.05


def _read_key(fd: int) -> str:
    """One key press. Reads the fd directly: sys.stdin buffers, and select()
    cannot see bytes that are already inside that buffer."""
    first = os.read(fd, 1)
    if first == b"\x1b":
        if not select.select([fd], [], [], _ESC_WAIT_SECONDS)[0]:
            return "\x1b"
        return (first + os.read(fd, 2)).decode(errors="ignore")
    return (first + os.read(fd, _utf8_tail_length(first[0]))).decode(errors="ignore")


def _utf8_tail_length(lead_byte: int) -> int:
    """Continuation bytes still to read after a UTF-8 lead byte."""
    if lead_byte >= 0xF0:
        return 3
    if lead_byte >= 0xE0:
        return 2
    if lead_byte >= 0xC0:
        return 1
    return 0


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
    if key in {"\x1b[A", "\x1b[B"} and visible_count:
        step = -1 if key == "\x1b[A" else 1
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
