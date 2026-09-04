from __future__ import annotations

from pathlib import Path

import typer
from lemma_sdk.openapi_client.models.add_column_request import AddColumnRequest
from lemma_sdk.openapi_client.models.create_table_request import CreateTableRequest
from lemma_sdk.openapi_client.models.update_table_request import UpdateTableRequest

from ...cli_app.enums import COLUMN_TYPES, VISIBILITY_VALUES
from ...cli_app.record_io import (
    RECORD_EXPORT_DEFAULT_LIMIT,
    fetch_records_capped,
    read_record_rows,
    write_export_rows,
)
from ..confirm import confirm_destructive
from ..context import selected_pod
from ..io import emit
from ..payload import build_request, read_json
from ..sdk import _ensure_pod_uuid, pod_client
from ..state import fail, run_with_client, state_from_ctx

tables_app = typer.Typer(help="Table commands.")
records_app = typer.Typer(help="Record commands.")
query_app = typer.Typer(help="Query commands.")
datastore_app = typer.Typer(help="Stream live datastore record changes.")


@tables_app.command("init")
def init_table(
    name: str = typer.Argument(..., help="Table name (snake_case)."),
    root: Path | None = typer.Option(
        None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."
    ),
    shared: bool = typer.Option(
        False,
        "--shared",
        help="Shared team table (enable_rls=false); default is per-user RLS.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Scaffold a table bundle file. Edit the columns, then `lemma pods import`."""
    from ...cli_app.scaffold import ScaffoldError, init_resource, report

    try:
        result = init_resource("table", name, root=root, force=force, shared=shared)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report(result, next_hint="edit columns, then `lemma pods import .`")


@tables_app.command("schema")
def schema_table(ctx: typer.Context) -> None:
    """Print the JSONC example/shape for a table bundle file."""
    from ._authoring import print_resource_schema

    print_resource_schema(ctx, "table")


@tables_app.command("list")
def list_tables(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List tables in the pod."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).tables.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@tables_app.command("get")
def get_table(
    ctx: typer.Context,
    table: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a table."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, s: pod_client(client, s, pod).tables.get(table)
    )
    if result is not None:
        emit(state, result)


@tables_app.command("create")
def create_table(
    ctx: typer.Context,
    table: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create a table from a JSON payload.

    The payload carries typed `columns`, a `primary_key_column`, optional
    `config`, and `enable_rls` (row-level security). `enable_rls` defaults to
    true: each row is owned by its creator and non-admin members see only their
    own rows (other users' rows return 404), while pod admins see all rows — use
    this for per-user/personal data. Set `"enable_rls": false` for shared/
    reference/team tables that all members read and mutate. RLS only scopes which
    rows a non-admin can touch; writing any table still needs the POD_USER role.
    """
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).tables.create(
            build_request(
                CreateTableRequest,
                {
                    **payload,
                    "name": payload.get("name", table),
                    "columns": payload.get("columns", []),
                },
                context="table",
            )
        ),
    )
    if result is not None:
        emit(state, result)


@tables_app.command("update")
def update_table(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name."),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Replacement config JSON (table `config` metadata)."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    visibility: str | None = typer.Option(
        None,
        "--visibility",
        help=f"Set table visibility. One of: {' | '.join(VISIBILITY_VALUES)}.",
    ),
    enable_rls: bool | None = typer.Option(
        None,
        "--enable-rls/--disable-rls",
        help=(
            "Toggle per-user row-level security. Empty tables only: enabling adds "
            "the user_id ownership column + isolation policy; disabling removes it."
        ),
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update a live table's config, visibility, or row-level security.

    Pass `--data`/`--file` to replace the `config` metadata, `--visibility` to
    re-scope it, and `--enable-rls`/`--disable-rls` to flip RLS (only on an empty
    table). Use `lemma table add-column`/`drop-column` to change columns.
    """
    payload = read_json(json_payload, file) if (json_payload or file) else {}
    update_fields: dict = {}
    if payload:
        update_fields["config"] = payload.get("config", payload)
    if visibility is not None:
        update_fields["visibility"] = visibility.upper()
    if enable_rls is not None:
        update_fields["enable_rls"] = enable_rls
    if not update_fields:
        raise typer.BadParameter(
            "Nothing to update: pass --data/--file, --visibility, or "
            "--enable-rls/--disable-rls."
        )
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).tables.update(
            table, build_request(UpdateTableRequest, update_fields)
        ),
    )
    if result is not None:
        emit(state, result)


@tables_app.command("add-column")
def add_table_column(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name."),
    name: str = typer.Argument(None, help="New column name (omit when using --data)."),
    type_: str = typer.Option(
        "TEXT", "--type", help=f"Column type. One of: {' '.join(COLUMN_TYPES)}."
    ),
    required: bool = typer.Option(False, "--required"),
    unique: bool = typer.Option(False, "--unique"),
    default: str | None = typer.Option(None, "--default"),
    option: list[str] = typer.Option(
        [], "--option", help="ENUM option (repeat). Required for --type ENUM."
    ),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Full column JSON (overrides the flags)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Add a column to a live table. (Type changes aren't in-place: drop + re-add.)"""
    state = state_from_ctx(ctx)
    if json_payload:
        column = read_json(json_payload, None, required=True)
    else:
        if not name:
            raise typer.BadParameter(
                "Provide a column NAME (or --data with full column JSON)."
            )
        column = {
            "name": name,
            "type": type_.upper(),
            "required": required,
            "unique": unique,
        }
        if default is not None:
            column["default"] = default
        if option:
            column["options"] = option
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).tables.add_column(
            table, build_request(AddColumnRequest, {"column": column})
        ),
    )
    if result is not None:
        emit(state, result)


@tables_app.command("drop-column")
def drop_table_column(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name."),
    name: str = typer.Argument(..., help="Column name to drop."),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Drop a column from a live table (irreversible — the column's data is lost)."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Drop column '{name}' from table '{table}'?", yes)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).tables.remove_column(table, name),
    )
    if result is not None:
        emit(state, result)


@tables_app.command("delete")
def delete_table(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name."),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a table and ALL its rows (irreversible)."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete table '{table}' and all its data?", yes)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).tables.delete(table),
    )
    emit(state, result if result is not None else {"ok": True, "deleted": table})


@records_app.command("list")
def list_records(
    ctx: typer.Context,
    table: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List records in a table."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).records.list(table, limit=limit),
    )
    if result is not None:
        emit(state, result)


@records_app.command("create")
def create_record(
    ctx: typer.Context,
    table: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create a record from a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).records.create(
            table, data=payload.get("data", payload)
        ),
    )
    if result is not None:
        emit(state, result)


@records_app.command("import")
def import_records(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Target table."),
    file: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="CSV, JSONL, or JSON array.",
    ),
    fmt: str | None = typer.Option(
        None, "--format", help="csv | jsonl | json (default: from file extension)."
    ),
    limit: int = typer.Option(0, "--limit", help="Import at most N rows (0 = all)."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Bulk-seed records into a table from a CSV/JSONL/JSON file."""
    rows = read_record_rows(file, fmt)
    if limit:
        rows = rows[:limit]
    if not rows:
        typer.echo("No rows to import.")
        return

    count = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).records.bulk_create(table, rows),
    )
    if count is not None:
        typer.echo(f"Imported {count} record(s) into {table}.")


@records_app.command("export")
def export_records(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Source table."),
    output: Path = typer.Argument(
        ..., help="Destination file (.csv, .jsonl, or .json)."
    ),
    fmt: str | None = typer.Option(
        None, "--format", help="csv | jsonl | json (default: from file extension)."
    ),
    limit: int = typer.Option(
        RECORD_EXPORT_DEFAULT_LIMIT,
        "--limit",
        help=f"Export at most N rows (default {RECORD_EXPORT_DEFAULT_LIMIT}).",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Dump a table's records to CSV/JSONL/JSON.

    CSV is the default; complex (object/array) cell values are written as JSON
    text so they round-trip back through `lemma records import`.
    """
    rows = run_with_client(
        ctx,
        lambda client, s: fetch_records_capped(
            pod_client(client, s, pod), table, limit
        ),
    )
    if rows is None:
        return
    write_export_rows(output, rows, fmt)
    typer.echo(f"Exported {len(rows)} record(s) from {table} to {output}.")


@records_app.command("get")
def get_record(
    ctx: typer.Context,
    table: str = typer.Argument(...),
    record: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a record."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).records.get(table, record),
    )
    if result is not None:
        emit(state, result)


@records_app.command("update")
def update_record(
    ctx: typer.Context,
    table: str = typer.Argument(...),
    record: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update a record from a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).records.update(
            table, record, data=payload.get("data", payload)
        ),
    )
    if result is not None:
        emit(state, result)


@records_app.command("delete")
def delete_record(
    ctx: typer.Context,
    table: str = typer.Argument(...),
    record: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a record."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete record {record} from {table}?", yes)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).records.delete(table, record),
    )
    emit(state, result if result is not None else {"ok": True})


@query_app.command("run")
def run_query(
    ctx: typer.Context,
    sql: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Run a read-only SQL query against the pod datastore.

    A single SELECT only (no writes); returns {items, total, truncated}. `total`
    counts the rows that came back, not the rows that matched — the result is
    capped, and `truncated` says whether more exist. Joins, aggregates, and
    subqueries across tables are allowed, including RLS tables — rows of an RLS
    table are scoped to you unless you administer it.
    """
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).queries.run(sql),
    )
    if result is not None:
        emit(state, result)


@datastore_app.command("watch")
def watch_changes(
    ctx: typer.Context,
    table: str | None = typer.Argument(
        None, help="Table to watch; omit to watch every table you can read."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    since: str | None = typer.Option(
        None,
        "--since",
        help="Resume after a stream id seen earlier (replays missed changes).",
    ),
) -> None:
    """Stream live record changes (insert/update/delete) over a websocket.

    On RLS (per-user) tables you receive only your own rows; on shared tables you
    receive every member's changes. Reconnects automatically and resumes from the
    last change seen. Use `--output json` for newline-delimited JSON to pipe.
    """
    state = state_from_ctx(ctx)

    def _resolve(client, s) -> str:
        pod_id = selected_pod(s, pod)
        if not pod_id:
            fail("No pod selected. Run `lemma pods`, pass --pod, or set LEMMA_POD_ID.")
        return _ensure_pod_uuid(client, s, pod_id)

    pod_id = run_with_client(ctx, _resolve)
    if not pod_id:
        return
    from ..watch import watch_datastore_changes

    watch_datastore_changes(state, str(pod_id), table, since)
