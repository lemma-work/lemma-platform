"""Turning a resource into the few lines a chat message can actually hold.

A card on Telegram or WhatsApp gets a headline, a line under it, and — if it
earns the room — a fixed-width block. That is the whole budget, and it is what
these helpers are shaped around: a file described by what it *is* rather than
where it lives, and a table shown as its own first rows rather than as a
sentence promising rows elsewhere.

Everything here is pure. The values come from the datastore in
:mod:`display_resource_content`; this module only decides how they read, which
keeps the layout rules unit-testable without a pod behind them.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

# A phone renders a monospace block at roughly 38–40 characters before it wraps,
# and a wrapped fixed-width table is worse than no table: the columns stop
# lining up, which was the only reason to send one. Columns are admitted until
# the budget is spent and the rest are left to the "open it in Lemma" link.
PREVIEW_LINE_BUDGET = 38
PREVIEW_COLUMN_LIMIT = 4
PREVIEW_ROW_LIMIT = 5
PREVIEW_CELL_LIMIT = 14

_COLUMN_GAP = "  "

# Extensions whose bare uppercase form is not what a person calls the thing.
_KIND_BY_EXTENSION: dict[str, str] = {
    "md": "Markdown",
    "txt": "Text",
    "jpg": "Image",
    "jpeg": "Image",
    "png": "Image",
    "gif": "Image",
    "webp": "Image",
    "svg": "Image",
    "mp3": "Audio",
    "ogg": "Audio",
    "wav": "Audio",
    "mp4": "Video",
    "mov": "Video",
    "xlsx": "Spreadsheet",
    "xls": "Spreadsheet",
    "docx": "Document",
    "doc": "Document",
    "pptx": "Slides",
    "ppt": "Slides",
}


def describe_file(
    *,
    name: str | None,
    size_bytes: int | None,
    mime_type: str | None,
) -> str | None:
    """One line saying what a file is: ``PDF · 2.3 MB``.

    The kind comes from the extension first, because that is the word the
    person already has for the file they asked for. A MIME type is the fallback
    for a file whose name carries no extension at all.
    """
    parts = [
        part for part in (_file_kind(name, mime_type), format_bytes(size_bytes)) if part
    ]
    return " · ".join(parts) or None


def format_bytes(size_bytes: int | None) -> str | None:
    """A file size the way a file manager writes it."""
    if size_bytes is None or size_bytes < 0:
        return None
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    kb = size_bytes / 1024
    if kb < 1024:
        return f"{kb:.0f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    return f"{mb / 1024:.1f} GB"


def format_record_count(shown: int, total: int | None) -> str | None:
    """``5 of 128 records`` — how much of the table the block is showing."""
    if shown <= 0:
        return "No records match."
    noun = "record" if total == 1 else "records"
    if total is None or total <= shown:
        return f"{shown} {noun}"
    return f"{shown} of {total} {noun}"


def format_record_table(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
) -> str | None:
    """Lay records out as a fixed-width table, or ``None`` when there is none.

    Columns are taken in the table's own order and admitted left to right until
    the line budget runs out, so the first columns — which are the identifying
    ones in every schema anyone writes — are the ones that survive.
    """
    visible_rows = rows[:PREVIEW_ROW_LIMIT]
    if not visible_rows:
        return None
    ordered = columns or _columns_from_rows(visible_rows)
    cells = {
        column: [_render_cell(row.get(column)) for row in visible_rows]
        for column in ordered
    }
    widths = {
        column: min(
            PREVIEW_CELL_LIMIT,
            max([len(column), *(len(cell) for cell in cells[column])]),
        )
        for column in ordered
    }
    chosen = _columns_within_budget(ordered, widths)
    if not chosen:
        return None
    lines = [_row_line([_fit(column, widths[column]) for column in chosen])]
    lines.append(_row_line(["-" * widths[column] for column in chosen]))
    for index in range(len(visible_rows)):
        lines.append(
            _row_line([_fit(cells[column][index], widths[column]) for column in chosen])
        )
    return "\n".join(lines)


def _columns_within_budget(ordered: list[str], widths: dict[str, int]) -> list[str]:
    chosen: list[str] = []
    used = 0
    for column in ordered:
        cost = widths[column] + (len(_COLUMN_GAP) if chosen else 0)
        if chosen and used + cost > PREVIEW_LINE_BUDGET:
            break
        chosen.append(column)
        used += cost
        if len(chosen) == PREVIEW_COLUMN_LIMIT:
            break
    return chosen


def _columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Column order as the records carry it, first appearance winning."""
    ordered: list[str] = []
    for row in rows:
        for key in row:
            if key not in ordered:
                ordered.append(key)
    return ordered


def _row_line(cells: list[str]) -> str:
    return _COLUMN_GAP.join(cells).rstrip()


def _fit(value: str, width: int) -> str:
    if len(value) <= width:
        return value.ljust(width)
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def _render_cell(value: Any) -> str:
    """One record value as a single short line of plain text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return " ".join(str(value).split())


def _file_kind(name: str | None, mime_type: str | None) -> str | None:
    extension = (
        str(name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    )
    if extension:
        named = _KIND_BY_EXTENSION.get(extension)
        if named:
            return named
        if len(extension) <= 4 and extension.isalnum():
            return extension.upper()
    subtype = str(mime_type or "").split(";")[0].strip().rsplit("/", 1)[-1]
    return subtype.upper() if subtype else None
