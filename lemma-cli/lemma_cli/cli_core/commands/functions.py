from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import typer
from lemma_sdk.openapi_client.models.create_function_request import CreateFunctionRequest
from lemma_sdk.openapi_client.models.function_permissions_replace_request import (
    FunctionPermissionsReplaceRequest,
)
from lemma_sdk.openapi_client.models.update_function_request import UpdateFunctionRequest

from ..confirm import confirm_destructive
from ..io import emit, to_plain
from ..payload import build_request, read_json
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx

app = typer.Typer(help="Function commands.")
permissions_app = typer.Typer(help="Function resource permission commands.")
app.add_typer(permissions_app, name="permissions")
runs_app = typer.Typer(help="Function run commands (inspect past runs).")
app.add_typer(runs_app, name="runs")

# Function run statuses that mean "still going" while polling.
_NON_TERMINAL_STATUSES = {"PENDING", "RUNNING", "STARTED", "IN_PROGRESS"}


@app.command("init")
def init_function(
    name: str = typer.Argument(..., help="Function name (snake_case)."),
    root: Path | None = typer.Option(
        None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Scaffold a function bundle (JSON + code.py with required headers). Edit, then import."""
    from ...cli_app.scaffold import ScaffoldError, init_resource, report

    try:
        result = init_resource("function", name, root=root, force=force)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report(result, next_hint="implement code.py + grants, then `lemma pods import .`")


@app.command("list")
def list_functions(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List functions in the pod."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@app.command("get")
def get_function(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a function."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.get(function),
    )
    if result is not None:
        emit(state, result)


@app.command("create")
def create_function(
    ctx: typer.Context,
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create a function from a JSON payload.

    Required: name (+ code with #function_name/#input_type_name/#output_type_name
    headers). Optional: type (API|JOB), visibility, permissions.grants. Prefer
    `lemma function init <name>`; run `lemma function schema` for the full shape.
    """
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.create(
            build_request(CreateFunctionRequest, payload, context="function")
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("update")
def update_function(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update a function from a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.update(
            function, UpdateFunctionRequest.from_dict(payload)
        ),
    )
    if result is not None:
        emit(state, result)


@permissions_app.command("get")
def get_function_permissions(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show resource permissions for a function."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.permissions(function),
    )
    if result is not None:
        emit(state, result)


@permissions_app.command("replace")
def replace_function_permissions(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Replace resource permissions for a function."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.replace_permissions(
            function, FunctionPermissionsReplaceRequest.from_dict(payload)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("grant")
def grant_function(
    name: str = typer.Argument(..., help="Function name (matches the bundle folder)."),
    specs: list[str] = typer.Argument(
        ...,
        metavar="GRANT...",
        help="name:perms or type:name:perms, e.g. tickets:read,write /knowledge:read app:gmail:use",
    ),
    root: Path | None = typer.Option(None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."),
    show: bool = typer.Option(False, "--print", help="Print grant JSON instead of editing the bundle file."),
) -> None:
    """Add resource grants to a function's bundle JSON (functions have zero access by default)."""
    from ._authoring import grant_resource

    grant_resource("function", name, specs, root=root, show=show)


@app.command("schema")
def schema_function() -> None:
    """Print the JSONC example/shape for a function bundle file."""
    from ._authoring import print_resource_schema

    print_resource_schema("function")


@app.command("run")
def run_function(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help=(
            "Wait for an async run to finish and return the final run "
            "(default). With --no-wait, return as soon as the run is created. "
            "Synchronous functions return their result either way."
        ),
    ),
    wait_timeout: float = typer.Option(180.0, "--wait-timeout", min=1.0),
    poll_interval: float = typer.Option(0.5, "--poll-interval", min=0.5),
) -> None:
    """Run a function with an optional JSON input payload."""
    payload = read_json(json_payload, file)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: _run_and_optionally_wait(
            pod_client(client, s, pod).functions,
            function=function,
            inputs=payload,
            wait=wait,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        ),
    )
    if result is not None:
        emit(state, result)


def _run_and_optionally_wait(
    functions: Any,
    *,
    function: str,
    inputs: Any,
    wait: bool,
    wait_timeout: float,
    poll_interval: float,
) -> Any:
    run_payload = to_plain(functions.run(function, inputs))
    if not wait or not isinstance(run_payload, dict):
        return run_payload

    run_id = str(run_payload.get("id") or "")
    status = str(run_payload.get("status") or "").upper()
    if not run_id or status not in _NON_TERMINAL_STATUSES:
        return run_payload

    deadline = time.monotonic() + wait_timeout
    while status in _NON_TERMINAL_STATUSES:
        if time.monotonic() >= deadline:
            return {
                **run_payload,
                "wait_timed_out": True,
                "message": f"Function run did not finish within {wait_timeout:.0f}s.",
            }
        time.sleep(poll_interval)
        run_payload = to_plain(functions.run_get(function, run_id))
        status = str(run_payload.get("status") or "").upper()
    return run_payload


@app.command("delete")
def delete_function(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a function."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete function {function}?", yes)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.delete(function),
    )
    emit(state, result if result is not None else {"ok": True})


@runs_app.command("list")
def list_function_runs(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List past runs of a function (latest first) for debugging."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.runs(
            function, limit=limit
        ),
    )
    if result is not None:
        emit(state, result)


@runs_app.command("get")
def get_function_run(
    ctx: typer.Context,
    function: str = typer.Argument(...),
    run: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a single function run (status, input, output, logs, error)."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).functions.run_get(function, run),
    )
    if result is not None:
        emit(state, result)
