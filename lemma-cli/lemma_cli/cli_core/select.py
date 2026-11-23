from __future__ import annotations

import sys
from typing import Any, TextIO

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


def filter_items(items: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    needle = text.lower()
    if not needle:
        return list(items)
    return [
        item
        for item in items
        if needle in item_label(item).lower()
        or needle in str(item.get("name") or "").lower()
        or needle in str(item.get("slug") or "").lower()
        or needle in str(item.get("id") or "").lower()
        or needle in str(item.get("title") or "").lower()
    ]


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

    filter_text = ""
    selected = _initial_index(items, current_id)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            filtered = filter_items(items, filter_text)
            if filtered:
                selected = max(0, min(selected, len(filtered) - 1))
            else:
                selected = 0
            _render_arrow_selector(
                filtered,
                label=label,
                selected=selected,
                filter_text=filter_text,
            )
            key = sys.stdin.read(1)
            if key == "\x1b":
                next_ch = sys.stdin.read(1)
                key += next_ch
                if next_ch in {"[", "O"}:
                    while True:
                        ch = sys.stdin.read(1)
                        key += ch
                        if ch == "~" or ch.isalpha():
                            break
            if key in {"\r", "\n"}:
                if filtered:
                    console.print()
                    return filtered[selected]
                continue
            if key in {"\x03", "\x04"}:
                raise typer.Exit(code=1)
            if key == "\x1b[A":
                if filtered:
                    selected = (selected - 1) % len(filtered)
            elif key == "\x1b[B":
                if filtered:
                    selected = (selected + 1) % len(filtered)
            elif key in {"\x7f", "\x08", "\b"}:
                if filter_text:
                    filter_text = filter_text[:-1]
                    selected = 0
            elif key.isprintable() and len(key) == 1:
                filter_text += key
                selected = 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


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
    output: TextIO | None = None,
) -> None:
    out = output if output is not None else sys.stdout
    out.write("\033[2J\033[H")
    out.write(f"Select {label} (type to filter, up/down, enter)\n")
    if filter_text:
        out.write(f"Filter: {filter_text}\n")
    if not items:
        out.write("  (no matches)\n")
    else:
        for index, item in enumerate(items):
            prefix = ">" if index == selected else " "
            line = f"{prefix} {item_label(item, str(index + 1))}"
            if index == selected:
                line = f"\033[7m{line}\033[0m"
            out.write(f"{line}\n")
    out.flush()
