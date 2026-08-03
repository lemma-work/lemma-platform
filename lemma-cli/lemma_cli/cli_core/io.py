from __future__ import annotations

import json
from typing import Any

from rich import box
from rich.panel import Panel
from rich.table import Table

from .state import CliState, console


IMPORTANT_KEYS = (
    "name",
    "display_name",
    "title",
    "status",
    "state",
    "type",
    "kind",
    "role",
    "roles",
    "email",
    "path",
    "description",
    "created_at",
    "updated_at",
)

LIST_KEYS = (
    "active",
    "name",
    "base_url",
    "auth_url",
    "display_name",
    "title",
    "columns",
    "primary_key_column",
    "enable_rls",
    "status",
    "state",
    "type",
    "kind",
    "role",
    "roles",
    "email",
    "path",
    "id",
)

# Secrets: shown as [redacted], never printed.
REDACTED_KEYS = {
    "credentials",
    "credential_config",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "secret",
    "password",
    "api_key",
    "private_key",
}

# Pure noise: never useful in human output.
NOISE_KEYS = {
    "additional_properties",
    "embedding",
    "description_embedding",
}

# Values that are JSON Schemas — rendered as a field:type tree rather than hidden.
SCHEMA_KEYS = {
    "schema",
    "input_schema",
    "output_schema",
    "config_schema",
    "payload_schema",
    "parameters",
}

# Keys that should never be chosen as list-table columns (too large/structured).
SKIP_LIST_COLUMN_KEYS = (
    NOISE_KEYS
    | REDACTED_KEYS
    | SCHEMA_KEYS
    | {
        "metadata",
        "config",
        "permissions",
        "columns",
        "data",
        "source",
        "output_data",
        "tool_args",
        "tool_result",
        "payload_example",
    }
)

SYSTEM_COLUMN_NAMES = {
    "_owner_user_id",
    "created_by_user_id",
    "updated_by_user_id",
    "deleted_at",
}

_INDENT = "  "


class _Ctx:
    """Carries the --full setting and tracks whether anything was folded."""

    __slots__ = ("full", "folded")

    def __init__(self, full: bool) -> None:
        self.full = full
        self.folded = False

    def note_fold(self) -> None:
        if not self.full:
            self.folded = True


def emit(state: CliState, data: Any) -> None:
    data = to_plain(data)
    if state.output == "json":
        console.print_json(json.dumps(data, default=str))
        return
    ctx = _Ctx(getattr(state, "full", False))
    if isinstance(data, list):
        _emit_list("Results", data, ctx)
    elif isinstance(data, dict):
        _emit_dict(data, ctx)
    else:
        console.print(data)
    _emit_fold_hint(ctx)


def list_items(payload: Any) -> list[dict[str, Any]]:
    payload = to_plain(payload)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []


def to_plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return to_plain(value.to_dict())
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def table(
    title: str, rows: list[dict[str, Any]], columns: list[tuple[str, str]]
) -> None:
    view = Table(title=title, box=box.SIMPLE_HEAVY)
    for heading, _key in columns:
        view.add_column(heading)
    for row in rows:
        view.add_row(*(str(row.get(key, "")) for _heading, key in columns))
    console.print(view)


def _emit_fold_hint(ctx: _Ctx) -> None:
    if ctx.folded:
        console.print("[dim]… some fields were folded; pass --full for complete output[/dim]")


# --------------------------------------------------------------------------- #
# File content (`file cat`)                                                    #
# --------------------------------------------------------------------------- #


def render_file_content(state: CliState, payload: Any) -> None:
    """Render a file's content for `file cat`: a dim metadata header followed by
    the raw text. JSON mode emits the structured payload for piping."""
    data = to_plain(payload)
    if state.output == "json":
        emit(state, data)
        return
    if not isinstance(data, dict):
        console.print(str(data))
        return
    if data.get("is_binary"):
        mime = data.get("mime_type") or "unknown type"
        size = data.get("size_bytes") or 0
        console.print(
            f"Binary file: {data.get('path', '')} ({mime}, {size} bytes)",
            style="yellow",
            markup=False,
            highlight=False,
        )
        console.print(
            "Not UTF-8 text. Try --markdown for a converted view, "
            "or `file download` to save it.",
            style="dim",
            markup=False,
            highlight=False,
        )
        return

    header = _file_content_header(data)
    if header:
        # style+markup=False so paths/facts are never mis-parsed as rich tags.
        console.print(header, style="dim", markup=False, highlight=False)
    console.print(
        data.get("content", ""), markup=False, highlight=False, soft_wrap=True
    )
    if data.get("truncated"):
        console.print(
            _truncation_hint(data), style="dim", markup=False, highlight=False
        )


def _file_content_header(data: dict[str, Any]) -> str:
    facts: list[str] = [str(data.get("mode") or "text")]
    if data.get("mode") == "markdown":
        page_count = data.get("page_count")
        start = data.get("page_start")
        end = data.get("page_end") or start
        if start:
            span = f"{start}" if end == start else f"{start}-{end}"
            facts.append(f"pages {span}/{page_count}" if page_count else f"pages {span}")
        elif page_count:
            facts.append(f"{page_count} page{_plural(page_count)}")
    if data.get("line_start") or data.get("line_end"):
        start = data.get("line_start") or 1
        end = data.get("line_end")
        facts.append(f"lines {start}-{end}" if end else f"lines {start}-")
    returned = data.get("returned_lines")
    if returned is not None:
        facts.append(f"{returned} line{_plural(returned)}")
    return f"{data.get('path', '')}  ·  {'  ·  '.join(facts)}"


def _truncation_hint(data: dict[str, Any]) -> str:
    chars = data.get("returned_chars")
    suffix = f" at {chars} chars" if chars else ""
    return (
        f"… output truncated{suffix}; raise --max-chars/--max-lines, narrow with "
        "--pages/--lines, or pass --full for everything."
    )


# --------------------------------------------------------------------------- #
# Directory tree (`file tree`)                                                 #
# --------------------------------------------------------------------------- #


def render_file_tree(state: CliState, payload: Any) -> None:
    """Render a directory tree as an ASCII tree. JSON mode emits the payload."""
    data = to_plain(payload)
    if state.output == "json":
        emit(state, data)
        return
    tree = data.get("tree") if isinstance(data, dict) else None
    if not isinstance(tree, dict):
        emit(state, data)
        return
    root_label = data.get("root_path") or _tree_node_label(tree)
    console.print(root_label, markup=False, highlight=False)
    _print_tree_children(tree, prefix="")


def _tree_node_label(node: dict[str, Any]) -> str:
    name = str(node.get("name") or node.get("path") or "")
    if node.get("kind") == "FOLDER":
        return f"{name.rstrip('/')}/"
    return name


def _print_tree_children(node: dict[str, Any], *, prefix: str) -> None:
    children = [c for c in node.get("children", []) if isinstance(c, dict)]
    has_more = bool(node.get("has_more_files"))
    last_index = len(children) - 1
    for index, child in enumerate(children):
        is_last = index == last_index and not has_more
        connector = "└── " if is_last else "├── "
        console.print(
            f"{prefix}{connector}{_tree_node_label(child)}",
            markup=False,
            highlight=False,
        )
        if child.get("children"):
            extension = "    " if is_last else "│   "
            _print_tree_children(child, prefix=prefix + extension)
    if has_more:
        console.print(f"{prefix}└── [dim]… more files[/dim]")


# --------------------------------------------------------------------------- #
# Conversation transcript                                                      #
# --------------------------------------------------------------------------- #

_TOOL_RESULT_MAX = 200
_TOOL_ARGS_MAX = 160
_THINKING_MAX = 240


def render_transcript(state: CliState, payload: Any) -> None:
    """Render conversation messages as a readable, content-focused transcript.

    Text takes focus; tool calls/returns show the tool name plus folded
    args/result; ids, sequence, and metadata are hidden unless --full.
    """
    if state.output == "json":
        emit(state, payload)
        return
    items = list_items(payload)
    if not items:
        console.print("[dim]No messages.[/dim]")
        return
    ctx = _Ctx(getattr(state, "full", False))
    items = sorted(items, key=lambda m: m.get("sequence") or 0)
    for message in items:
        _render_message(message, ctx)
    _emit_fold_hint(ctx)


def _render_message(message: dict[str, Any], ctx: _Ctx) -> None:
    kind = str(message.get("kind") or "text")
    role = str(message.get("role") or "")

    if ctx.full:
        meta = " ".join(
            str(part)
            for part in (
                f"#{message.get('sequence')}" if message.get("sequence") is not None else "",
                message.get("id") or "",
                message.get("created_at") or "",
            )
            if part
        )
        if meta:
            console.print(f"[dim]{meta}[/dim]")

    if kind in ("text", "notification"):
        text = str(message.get("text") or "").strip()
        if text:
            console.print(f"{_role_tag(role, 'green')} {text}")
    elif kind == "thinking":
        text = _trunc(str(message.get("text") or ""), ctx, _THINKING_MAX)
        if text:
            console.print(f"[dim]{_plain_role(role)}·thinking {text}[/dim]")
    elif kind == "tool_call":
        name = message.get("tool_name") or "?"
        args = _compact_value(message.get("tool_args"), ctx, _TOOL_ARGS_MAX)
        console.print(f"[yellow]{_plain_role(role)} ⚙ [bold]{name}[/bold]([/yellow]"
                      f"{args}[yellow])[/yellow]")
    elif kind == "tool_return":
        name = message.get("tool_name") or "?"
        result = _compact_value(message.get("tool_result"), ctx, _TOOL_RESULT_MAX)
        console.print(f"[yellow]{_plain_role(role)} ↩ [bold]{name}[/bold][/yellow] {result}")
    else:
        text = _trunc(str(message.get("text") or ""), ctx, 200)
        console.print(f"{_role_tag(role, 'white')} [dim]({kind})[/dim] {text}")


def _plain_role(role: str) -> str:
    return role or "?"


def _role_tag(role: str, default_style: str) -> str:
    style = {"user": "cyan", "assistant": "green", "system": "magenta"}.get(
        role.lower(), default_style
    )
    return f"[{style}]{role or '?'}:[/{style}]"


def _compact_value(value: Any, ctx: _Ctx, max_length: int) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str, ensure_ascii=False)
    else:
        text = str(value)
    return _trunc(text, ctx, max_length)


def _emit_dict(data: dict[str, Any], ctx: _Ctx) -> None:
    if set(data) <= {"ok", "path", "saved_to"}:
        message = "Done" if data.get("ok", True) else "Failed"
        suffix = data.get("path") or data.get("saved_to")
        console.print(f"[green]{message}[/green]" + (f"  {suffix}" if suffix else ""))
        return

    if isinstance(data.get("items"), list):
        _emit_list(_title_from_payload(data), data["items"], ctx)
        _emit_page_hint(data)
        return

    if isinstance(data.get("servers"), list):
        _emit_list("Servers", data["servers"], ctx)
        active = data.get("active_server")
        if active:
            console.print(f"[dim]active server:[/dim] {active}")
        return

    if len(data) == 1:
        key, value = next(iter(data.items()))
        if isinstance(value, dict):
            _emit_detail(_humanize(key), value, ctx)
            return
        if isinstance(value, list):
            _emit_list(_humanize(key), value, ctx)
            return

    _emit_detail(_title_from_item(data), data, ctx)


def _emit_list(title: str, values: list[Any], ctx: _Ctx) -> None:
    rows = [item for item in values if isinstance(item, dict)]
    if not rows:
        if values:
            for item in values:
                console.print(str(item))
        else:
            console.print("[dim]No results.[/dim]")
        return

    columns = _columns_for_rows(rows)
    view = Table(title=title, box=box.SIMPLE_HEAVY, show_lines=False)
    for heading, _key in columns:
        view.add_column(heading, overflow="fold")
    for row in rows:
        view.add_row(*(_format_row_cell(row, key, ctx) for _heading, key in columns))
    console.print(view)


def _emit_detail(title: str, data: dict[str, Any], ctx: _Ctx) -> None:
    lines: list[str] = []
    primary_id = data.get("id")
    if primary_id:
        lines.append(f"[dim]id[/dim] {primary_id}")

    for key in IMPORTANT_KEYS:
        if key in data and data.get(key) not in (None, "", [], {}):
            lines.append(
                f"[bold]{_humanize(key)}[/bold] "
                f"{_format_cell(data[key], ctx, max_length=160)}"
            )

    for key, value in data.items():
        if key in {"id", *IMPORTANT_KEYS} or key in NOISE_KEYS:
            continue
        if key in REDACTED_KEYS:
            if value not in (None, "", [], {}):
                lines.append(f"[bold]{_humanize(key)}[/bold] [dim][redacted][/dim]")
            continue
        if value in (None, "", [], {}):
            continue
        lines.extend(
            _render_field(key, value, ctx, primary_key=data.get("primary_key_column"))
        )

    if not lines:
        lines.append("[dim]No displayable fields.[/dim]")
    console.print(Panel("\n".join(lines), title=title, border_style="cyan"))


def _render_field(
    key: str, value: Any, ctx: _Ctx, *, primary_key: str | None = None
) -> list[str]:
    """Render one detail field, possibly as a multi-line indented block."""
    header = f"[bold]{_humanize(key)}[/bold]"

    if key == "columns" and isinstance(value, list):
        max_columns = 9999 if ctx.full else 10
        if not ctx.full and len(value) > max_columns:
            ctx.note_fold()
        summary = format_columns(value, primary_key=primary_key, max_columns=max_columns)
        return [f"{header} {summary}"] if summary else []

    if key in SCHEMA_KEYS or _looks_like_schema(value):
        body = format_schema(value, ctx)
        if body:
            return [header, *(f"{_INDENT}{line}" for line in body)]
        return [f"{header} [dim]{_schema_type(value)}[/dim]"]

    if isinstance(value, dict):
        body = _render_mapping(value, ctx, depth=0)
        if not body:
            return []
        return [header, *(f"{_INDENT}{line}" for line in body)]

    if isinstance(value, list):
        return _render_list_field(header, value, ctx)

    return [f"{header} {_format_cell(value, ctx, max_length=160)}"]


def _render_list_field(header: str, value: list[Any], ctx: _Ctx) -> list[str]:
    dict_items = [item for item in value if isinstance(item, dict)]
    if dict_items and len(dict_items) == len(value):
        count = len(value)
        if not ctx.full and count > 0:
            ctx.note_fold()
            preview = _compact_dict(dict_items[0], ctx)
            tail = f" — first: {preview}" if preview else ""
            return [f"{header} [dim]{count} item{_plural(count)}[/dim]{tail}"]
        out = [f"{header} [dim]{count} item{_plural(count)}[/dim]"]
        for item in value:
            compact = _compact_dict(item, ctx)
            out.append(f"{_INDENT}- {compact}" if compact else f"{_INDENT}- {{}}")
        return out
    return [f"{header} {_format_cell(value, ctx, max_length=200)}"]


def _render_mapping(value: dict[str, Any], ctx: _Ctx, *, depth: int) -> list[str]:
    lines: list[str] = []
    max_depth = 6 if ctx.full else 2
    for key, item in value.items():
        if key in NOISE_KEYS:
            continue
        if key in REDACTED_KEYS:
            if item not in (None, "", [], {}):
                lines.append(f"{key}: [dim][redacted][/dim]")
            continue
        if item in (None, "", [], {}):
            continue
        if isinstance(item, dict):
            if depth + 1 >= max_depth:
                ctx.note_fold()
                lines.append(f"{key}: [dim]{{…}}[/dim]")
                continue
            nested = _render_mapping(item, ctx, depth=depth + 1)
            lines.append(f"{key}:")
            lines.extend(f"{_INDENT}{line}" for line in nested)
        elif isinstance(item, list):
            lines.append(f"{key}: {_format_cell(item, ctx, max_length=120)}")
        else:
            lines.append(f"{key}: {_format_cell(item, ctx, max_length=120)}")
    return lines


# --------------------------------------------------------------------------- #
# JSON Schema rendering                                                        #
# --------------------------------------------------------------------------- #


def format_schema(schema: Any, ctx: _Ctx | None = None, *, _depth: int = 0) -> list[str]:
    """Render a JSON Schema as a compact ``field: type`` tree (list of lines)."""
    if ctx is None:
        ctx = _Ctx(True)
    if not isinstance(schema, dict):
        return [str(schema)]

    props = schema.get("properties")
    if isinstance(props, dict) and props:
        required = set(schema.get("required") or [])
        lines: list[str] = []
        max_depth = 8 if ctx.full else 3
        for name, sub in props.items():
            sub = sub if isinstance(sub, dict) else {}
            typ = _schema_type(sub)
            req = " [red]*[/red]" if name in required else ""
            desc = sub.get("description")
            desc_str = f"  [dim]— {_trunc(str(desc), ctx, 80)}[/dim]" if desc else ""
            lines.append(f"[cyan]{name}[/cyan]: {typ}{req}{desc_str}")
            child = _object_child(sub)
            if child is not None:
                if _depth + 1 >= max_depth:
                    ctx.note_fold()
                else:
                    lines.extend(
                        f"{_INDENT}{line}"
                        for line in format_schema(child, ctx, _depth=_depth + 1)
                    )
        return lines

    # No properties: describe the type itself (e.g. free-form object / array).
    desc = schema.get("description")
    suffix = f"  [dim]— {_trunc(str(desc), ctx, 80)}[/dim]" if desc else ""
    return [f"{_schema_type(schema)}{suffix}"]


def _object_child(sub: dict[str, Any]) -> dict[str, Any] | None:
    """Return the nested object schema to recurse into, if any."""
    if isinstance(sub.get("properties"), dict):
        return sub
    items = sub.get("items")
    if isinstance(items, dict) and isinstance(items.get("properties"), dict):
        return items
    return None


def _schema_type(sub: Any) -> str:
    if not isinstance(sub, dict):
        return "any"
    ref = sub.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    for combinator in ("anyOf", "oneOf", "allOf"):
        members = sub.get(combinator)
        if isinstance(members, list) and members:
            non_null = [m for m in members if isinstance(m, dict) and m.get("type") != "null"]
            nullable = len(non_null) != len(members)
            parts = [_schema_type(m) for m in non_null] or ["any"]
            base = " | ".join(dict.fromkeys(parts))
            return f"{base}?" if nullable else base
    if "enum" in sub and isinstance(sub["enum"], list):
        vals = sub["enum"]
        shown = ",".join(str(v) for v in vals[:5])
        more = ",…" if len(vals) > 5 else ""
        return f"enum({shown}{more})"
    t = sub.get("type")
    if t == "array":
        return f"array<{_schema_type(sub.get('items') or {})}>"
    if t == "object" or (t is None and isinstance(sub.get("properties"), dict)):
        return "object"
    if isinstance(t, list):
        return " | ".join(str(x) for x in t)
    return str(t) if t else "any"


def _looks_like_schema(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("properties"), dict)
        and bool(value["properties"])
    )


# --------------------------------------------------------------------------- #
# Column / cell helpers                                                        #
# --------------------------------------------------------------------------- #


def _columns_for_rows(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    keys: list[str] = []
    for preferred in LIST_KEYS:
        if any(preferred in row and row.get(preferred) not in (None, "", [], {}) for row in rows):
            keys.append(preferred)
    if len(keys) < 3:
        for row in rows:
            for key, value in row.items():
                if key in keys or key in SKIP_LIST_COLUMN_KEYS:
                    continue
                if value in (None, "", [], {}) or isinstance(value, (dict, list)):
                    continue
                keys.append(key)
                if len(keys) >= 5:
                    break
            if len(keys) >= 5:
                break
    return [(_humanize(key), key) for key in keys[:6]]


def format_columns(
    columns: Any,
    *,
    primary_key: str | None = None,
    max_columns: int = 10,
) -> str:
    """Compact table column definitions for pretty CLI output."""
    raw_columns = to_plain(columns)
    if not isinstance(raw_columns, list):
        return ""

    valid = [item for item in raw_columns if isinstance(item, dict) and item.get("name")]
    if not valid:
        return ""

    visible = [
        item
        for item in valid
        if not _is_hidden_system_column(item, primary_key=primary_key)
    ] or valid

    shown = visible[:max_columns]
    parts = [_format_column(item) for item in shown]
    remaining = len(visible) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining}")
    return ", ".join(part for part in parts if part)


def _is_hidden_system_column(column: dict[str, Any], *, primary_key: str | None) -> bool:
    name = str(column.get("name") or "")
    if primary_key and name == primary_key:
        return False
    return bool(column.get("system")) or name in SYSTEM_COLUMN_NAMES


def _format_column(column: dict[str, Any]) -> str:
    name = str(column.get("name") or "")
    raw_type = column.get("type") or column.get("type_") or column.get("data_type")
    if raw_type in (None, "", [], {}):
        return name
    return f"{name}:{str(raw_type).lower()}"


def _format_row_cell(row: dict[str, Any], key: str, ctx: _Ctx) -> str:
    if key == "columns":
        return format_columns(row.get(key), primary_key=row.get("primary_key_column"))
    return _format_cell(row.get(key), ctx)


def _format_cell(value: Any, ctx: _Ctx, *, max_length: int = 64) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        limit = len(value) if ctx.full else 3
        text = ", ".join(str(item) for item in value[:limit])
        if len(value) > limit:
            text += f" +{len(value) - limit}"
            ctx.note_fold()
    elif isinstance(value, dict):
        text = _compact_dict(value, ctx)
    else:
        text = str(value)
    text = text.replace("\n", " ").strip()
    if not ctx.full and len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
        ctx.note_fold()
    return text


def _compact_dict(value: dict[str, Any], ctx: _Ctx) -> str:
    parts: list[str] = []
    for key in IMPORTANT_KEYS:
        item = value.get(key)
        if item not in (None, "", [], {}):
            parts.append(f"{key}={_format_cell(item, ctx, max_length=36)}")
    if not parts:
        for key, item in value.items():
            if key in SKIP_LIST_COLUMN_KEYS or item in (None, "", [], {}):
                continue
            if isinstance(item, (dict, list)):
                continue
            parts.append(f"{key}={_format_cell(item, ctx, max_length=36)}")
            if len(parts) >= 3:
                break
    return ", ".join(parts)


def _trunc(text: str, ctx: _Ctx, max_length: int) -> str:
    text = text.replace("\n", " ").strip()
    if not ctx.full and len(text) > max_length:
        ctx.note_fold()
        return text[: max_length - 1].rstrip() + "…"
    return text


def _plural(n: int) -> str:
    return "s" if n != 1 else ""


def _title_from_payload(data: dict[str, Any]) -> str:
    for key in ("resource", "type", "kind"):
        if data.get(key):
            return _humanize(str(data[key]))
    return "Results"


def _title_from_item(data: dict[str, Any]) -> str:
    for key in ("name", "display_name", "title", "email", "id"):
        value = data.get(key)
        if value:
            return str(value)
    return "Details"


def _emit_page_hint(data: dict[str, Any]) -> None:
    token = data.get("next_page_token") or data.get("nextPageToken")
    if token:
        console.print(f"[dim]More results available. page_token={token}[/dim]")


def _humanize(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()
