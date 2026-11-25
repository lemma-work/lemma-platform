"""Hooks: an address a form tool, Zapier or Make posts JSON to, one row per delivery.

The address carries a secret, and it is printed twice only: by `create` and
by `rotate`. Every other command shows the hook without it, along with when a
delivery last arrived and why the last refused one was refused -- which is
where to look when a form stops reaching the pod.

The endpoints are newer than the SDK's typed resources, so these go through
its `request` escape hatch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from ..confirm import confirm_destructive
from ..io import emit
from ..payload import read_json
from ..sdk import pod_client
from ..state import err_console, run_with_client, state_from_ctx

app = typer.Typer(
    help="Hooks: addresses services post JSON to, each delivery a row in a table."
)

_EXAMPLE = """\
  lemma hook create typeform --table form_submissions
  lemma hook create tally --table leads -F email=data.fields.0.value -F raw='$'"""

_FIELD_HELP = (
    "column=path, repeatable: the value at a dotted path into the posted JSON "
    "(`email`, `form_response.hidden.ref`, `answers.0.text`; `$` is the whole "
    "body). With none, the whole body goes to the table's `payload` JSON column."
)


def _hooks_path(pod) -> str:  # type: ignore[no-untyped-def]
    return f"/pods/{pod.pod_id}/datastore/hooks"


def _fields(pairs: list[str] | None) -> dict[str, str] | None:
    if not pairs:
        return None
    fields: dict[str, str] = {}
    for pair in pairs:
        column, sep, path = pair.partition("=")
        if not sep or not column.strip() or not path.strip():
            raise typer.BadParameter(
                f"--field takes column=path, got {pair!r}.", param_hint="--field"
            )
        fields[column.strip()] = path.strip()
    return fields


def _say_once() -> None:
    err_console.print(
        "Copy the url now: it carries the secret and is not shown again. "
        "Give it to the service as its webhook address."
    )


@app.command("list")
def list_hooks(
    ctx: typer.Context,
    limit: int | None = typer.Option(None, "--limit", min=1),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """List the pod's hooks."""
    state = state_from_ctx(ctx)

    def call(client, s):  # type: ignore[no-untyped-def]
        target = pod_client(client, s, pod)
        return target.request("GET", _hooks_path(target))

    result = run_with_client(ctx, call)
    if result is None:
        return
    if limit is not None and isinstance(result, dict):
        result = {**result, "items": list(result.get("items") or [])[:limit]}
    emit(state, result)


@app.command("get")
def get_hook(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The hook's name."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a hook: its table, mapping, last delivery and last error."""
    _call(ctx, pod, "GET", name)


@app.command("create", epilog=f"Examples:\n\n{_EXAMPLE}")
def create_hook(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Lowercase letters, digits, '-' or '_'."),
    table: str | None = typer.Option(
        None, "--table", "-t", help="The table each delivery adds a row to."
    ),
    field: list[str] | None = typer.Option(None, "--field", "-F", help=_FIELD_HELP),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="The request as JSON: table, fields."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Make a hook, and print its address once."""
    body: dict[str, Any] = {**read_json(json_payload, file), "name": name}
    if table is not None:
        body["table"] = table
    fields = _fields(field)
    if fields is not None:
        body["fields"] = fields
    if not body.get("table"):
        raise typer.BadParameter("Name the table with --table.", param_hint="--table")
    if _call(ctx, pod, "POST", None, body) is not None:
        _say_once()


@app.command("update")
def update_hook(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The hook's name."),
    field: list[str] | None = typer.Option(
        None, "--field", "-F", help=_FIELD_HELP + " Replaces the whole mapping."
    ),
    whole_body: bool = typer.Option(
        False,
        "--whole-body",
        help="Drop the mapping: keep the whole body in `payload`.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Change what a delivery becomes."""
    if whole_body and field:
        raise typer.BadParameter("Use --field or --whole-body, not both.")
    if not whole_body and not field:
        raise typer.BadParameter("Pass --field (repeatable) or --whole-body.")
    _call(ctx, pod, "PATCH", name, {"fields": None if whole_body else _fields(field)})


@app.command("enable")
def enable_hook(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The hook's name."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Switch a hook on: deliveries add rows again."""
    _call(ctx, pod, "PATCH", name, {"enabled": True})


@app.command("disable")
def disable_hook(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The hook's name."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Switch a hook off: deliveries are refused, and the sender is told why."""
    _call(ctx, pod, "PATCH", name, {"enabled": False})


@app.command("rotate")
def rotate_hook(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The hook's name."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Give a hook a new secret; the old address stops working at once."""
    confirm_destructive(
        f"Give hook {name} a new address? The old one stops working now, so "
        "every service posting to it must be given the new one.",
        yes,
    )
    if _call(ctx, pod, "POST", f"{name}/rotate") is not None:
        _say_once()


@app.command("delete")
def delete_hook(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The hook's name."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Delete a hook. Rows it added stay in their table."""
    confirm_destructive(f"Delete hook {name}?", yes)
    state = state_from_ctx(ctx)

    def call(client, s):  # type: ignore[no-untyped-def]
        target = pod_client(client, s, pod)
        target.request("DELETE", f"{_hooks_path(target)}/{name}")
        return {"deleted": name}

    result = run_with_client(ctx, call)
    if result is not None:
        emit(state, result)


def _call(
    ctx: typer.Context,
    pod: str | None,
    method: str,
    suffix: str | None,
    body: dict[str, Any] | None = None,
) -> Any:
    state = state_from_ctx(ctx)

    def call(client, s):  # type: ignore[no-untyped-def]
        target = pod_client(client, s, pod)
        path = _hooks_path(target) + (f"/{suffix}" if suffix else "")
        if body is None:
            return target.request(method, path)
        return target.request(method, path, json=body)

    result = run_with_client(ctx, call)
    if result is not None:
        emit(state, result)
    return result
