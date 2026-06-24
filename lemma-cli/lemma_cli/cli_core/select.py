from __future__ import annotations

import sys
from typing import Any

import typer

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
        console.print(f"{index:>2}. {item_label(item, str(index))}")
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

    selected = _initial_index(items, current_id)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            _render_arrow_selector(items, label=label, selected=selected)
            key = sys.stdin.read(1)
            if key == "\x1b":
                key += sys.stdin.read(2)
            if key in {"\r", "\n"}:
                console.print()
                return items[selected]
            if key in {"\x03", "\x04"}:
                raise typer.Exit(code=1)
            if key == "\x1b[A":
                selected = (selected - 1) % len(items)
            elif key == "\x1b[B":
                selected = (selected + 1) % len(items)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _initial_index(items: list[dict[str, Any]], current_id: str | None) -> int:
    if current_id:
        for index, item in enumerate(items):
            if str(item.get("id")) == current_id:
                return index
    return 0


def _render_arrow_selector(
    items: list[dict[str, Any]], *, label: str, selected: int
) -> None:
    output = sys.stdout
    output.write("\033[2J\033[H")
    output.write(f"Select {label} (up/down, enter)\n")
    for index, item in enumerate(items):
        prefix = ">" if index == selected else " "
        line = f"{prefix} {item_label(item, str(index + 1))}"
        if index == selected:
            line = f"\033[7m{line}\033[0m"
        output.write(f"{line}\n")
    output.flush()
