from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import typer
from lemma_sdk.openapi_client.models.workflow_create_request import (
    WorkflowCreateRequest,
)
from lemma_sdk.openapi_client.models.workflow_update_request import (
    WorkflowUpdateRequest,
)

from ..confirm import confirm_destructive
from ..io import emit, to_plain
from ..payload import build_request, read_json
from ..sdk import pod_client
from ..state import console, fail, run_with_client, state_from_ctx

app = typer.Typer(help="Workflow commands.")
runs_app = typer.Typer(help="Workflow run commands.")
app.add_typer(runs_app, name="runs")

# Run statuses that mean "still going" while polling.
_NON_TERMINAL_STATUSES = {"PENDING", "RUNNING", "STARTED", "IN_PROGRESS"}


@app.command("init")
def init_workflow(
    name: str = typer.Argument(..., help="Workflow name (slug)."),
    root: Path | None = typer.Option(
        None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Scaffold a working FORM->END workflow graph. Edit nodes/edges, then import."""
    from ...cli_app.scaffold import ScaffoldError, init_resource, report

    try:
        result = init_resource("workflow", name, root=root, force=force)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report(result, next_hint="add nodes/edges, then `lemma pods import .`")


@app.command("validate")
def validate_workflow_cmd(
    path: Path = typer.Argument(
        ...,
        exists=True,
        help="A workflow JSON file or its bundle folder (workflows/<name>).",
    ),
) -> None:
    """Statically check a workflow graph before import (entry node, edges, END, targets)."""
    from ...cli_app.pod_bundle import loads_jsonc
    from ...cli_app.scaffold import validate_workflow

    json_path = path
    if path.is_dir():
        candidates = [path / f"{path.name}.json", *sorted(path.glob("*.json"))]
        json_path = next((p for p in candidates if p.is_file()), path)
    if not json_path.is_file():
        raise typer.BadParameter(f"No workflow JSON found at {path}.")
    try:
        payload = loads_jsonc(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"Could not parse {json_path}: {exc}")
    issues = validate_workflow(payload)
    if not issues:
        console.print(
            f"[green]ok[/green] {json_path.name}: workflow graph looks valid."
        )
        return
    for issue in issues:
        console.print(f"[red]error[/red]  {issue}")
    raise typer.Exit(1)


# --- Workflow definition lifecycle ---------------------------------------------


@app.command("list")
def list_workflows(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List workflows in the pod."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@app.command("get")
def get_workflow(
    ctx: typer.Context,
    workflow: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a workflow."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.get(workflow),
    )
    if result is not None:
        emit(state, result)


@app.command("create")
def create_workflow(
    ctx: typer.Context,
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create a workflow from a JSON payload.

    Required: name. Optional: start, nodes, edges, mode, visibility. Prefer
    `lemma workflow init <name>`; run `lemma workflow schema` for the full shape,
    and `lemma workflow validate` before importing.
    """
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.create(
            build_request(WorkflowCreateRequest, payload, context="workflow")
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("schema")
def schema_workflow() -> None:
    """Print the JSONC example/shape for a workflow bundle file."""
    from ._authoring import print_resource_schema

    print_resource_schema("workflow")


@app.command("update")
def update_workflow(
    ctx: typer.Context,
    workflow: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None,
        "--data",
        "-d",
        help="Metadata JSON (description, mode, visibility, start).",
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update workflow metadata. Use `update-graph` to replace nodes/edges."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.update(
            workflow, WorkflowUpdateRequest.from_dict(payload)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("update-graph")
def update_workflow_graph(
    ctx: typer.Context,
    workflow: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Replace the workflow graph (start, nodes, edges) from a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.update_graph(
            workflow, payload
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("delete")
def delete_workflow(
    ctx: typer.Context,
    workflow: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a workflow."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete workflow {workflow}?", yes)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.delete(workflow),
    )
    if result is None:
        emit(state, {"ok": True})


@app.command("run")
def run_workflow(
    ctx: typer.Context,
    workflow: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None,
        "--data",
        "-d",
        help=(
            "Form input JSON. Runs take no start inputs; when the workflow "
            "begins with a form, this payload is submitted to it."
        ),
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help=(
            "Wait for the run to finish. Polls through agent/function/timer "
            "waits; returns immediately when the run needs a human form."
        ),
    ),
    wait_timeout: float = typer.Option(180.0, "--wait-timeout", min=1.0),
    poll_interval: float = typer.Option(2.0, "--poll-interval", min=0.5),
) -> None:
    """Create a workflow run (and submit --data to its entry form, if any)."""
    payload = read_json(json_payload, file, required=False)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: _run_and_optionally_wait(
            client,
            pod_id=pod_client(client, s, pod).pod_id,
            workflow=workflow,
            form_inputs=payload,
            wait=wait,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        ),
    )
    if result is not None:
        emit(state, result)


# --- Run sub-resource ----------------------------------------------------------


@runs_app.command("list")
def list_runs(
    ctx: typer.Context,
    workflow: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List runs for a workflow."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.runs(
            workflow, limit=limit
        ),
    )
    if result is not None:
        emit(state, result)


@runs_app.command("get")
def get_run(
    ctx: typer.Context,
    run: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a workflow run."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.run_get(run),
    )
    if result is not None:
        emit(state, result)


@runs_app.command("submit-form")
def submit_run_form(
    ctx: typer.Context,
    run: str = typer.Argument(...),
    node: str | None = typer.Option(
        None,
        "--node",
        help="Form node id. Defaults to the run's active wait node.",
    ),
    json_payload: str | None = typer.Option(
        None,
        "--data",
        "-d",
        help=(
            "Form field values as JSON. Optional — omit to submit only the "
            "form's prefilled defaults (the backend fills any field you leave "
            "out from its schema default)."
        ),
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Submit the form a workflow run is waiting on."""
    payload = read_json(json_payload, file, required=False) or {}
    state = state_from_ctx(ctx)

    def _submit(client: Any, s: Any) -> Any:
        workflows = pod_client(client, s, pod).workflows
        node_id = node
        if node_id is None:
            current = to_plain(workflows.run_get(run))
            active_wait = current.get("active_wait") or {}
            if active_wait.get("wait_type") != "HUMAN":
                fail(
                    "Run is not waiting on a form (active wait: "
                    f"{active_wait.get('wait_type') or 'none'}). Pass --node to "
                    "target a specific form node."
                )
            node_id = active_wait["node_id"]
        return workflows.submit_form(run, node_id=node_id, inputs=payload)

    result = run_with_client(ctx, _submit)
    if result is not None:
        emit(state, result)


@runs_app.command("cancel")
def cancel_run(
    ctx: typer.Context,
    run: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Cancel a workflow run that is running or waiting."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.cancel_run(run),
    )
    if result is not None:
        emit(state, result)


@runs_app.command("waiting")
def list_waiting_runs(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """Your approval queue: form waits assigned TO YOU.

    A form with no assignee is submittable by any member with execute access,
    but it is nobody's queue item and does not appear here — so an empty queue
    does not mean no run is parked. Use `workflows runs get <run-id>` (its
    `active_wait`) to see what a specific run is blocked on.
    """
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).workflows.list_my_waits(
            limit=limit
        ),
    )
    if result is None:
        return
    payload = to_plain(result)
    if state.output == "json":
        emit(state, payload)
        return
    items = payload.get("items") or []
    if not items:
        # "Nothing is waiting on you" was read as "nothing is waiting" — a run
        # parked on an UNASSIGNED form is submittable by any member with execute
        # access but belongs to no one's queue, so it never shows here. The API
        # only exposes assigned waits, so say what this list covers rather than
        # paying for a scan of every workflow's runs.
        console.print(
            "[dim]No form waits are assigned to you. A run parked on an "
            "unassigned form won't appear here — check a specific run with "
            "`lemma workflows runs get <run-id>` (see its active_wait).[/dim]"
        )
        return
    rows = []
    for item in items:
        wait = item.get("wait") or {}
        run = item.get("run") or {}
        rows.append(
            {
                "run_id": run.get("id"),
                "node": wait.get("node_id"),
                "waiting_since": wait.get("created_at"),
                "status": run.get("status"),
            }
        )
    emit(state, rows)
    console.print(
        "[dim]Submit with: lemma workflows runs submit-form <run_id> "
        "--data '{...}'[/dim]"
    )


# --- helpers -------------------------------------------------------------------


def _form_fields(active_wait: dict[str, Any]) -> list[dict[str, Any]]:
    """Readable summary of a resolved form wait's fields, so an operator can
    see the (possibly dynamic) dropdown options and prefilled defaults before
    submitting. The schema on the wait is already resolved server-side."""
    schema = (active_wait.get("payload") or {}).get("input_schema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: list[dict[str, Any]] = []
    for key, prop in properties.items():
        prop = prop if isinstance(prop, dict) else {}
        field: dict[str, Any] = {
            "field": key,
            "type": prop.get("type", "string"),
            "required": key in required,
        }
        if isinstance(prop.get("enum"), list):
            field["options"] = prop["enum"]
        if "default" in prop:
            field["default"] = prop["default"]
        fields.append(field)
    return fields


def _waiting_on_form_payload(
    run_payload: dict[str, Any], active_wait: dict[str, Any], run_id: str
) -> dict[str, Any]:
    """Run payload annotated with the form fields and a submit hint."""
    return {
        **run_payload,
        "form_fields": _form_fields(active_wait),
        "message": (
            f"Run is waiting on form '{active_wait.get('node_id')}'. Review the "
            "form_fields (options and prefilled defaults), then submit with: "
            f"lemma workflows runs submit-form {run_id} --data '{{...}}' "
            "(omit --data to accept the prefilled defaults)."
        ),
    }


def _run_and_optionally_wait(
    client: Any,
    *,
    pod_id: str,
    workflow: str,
    form_inputs: dict[str, Any],
    wait: bool,
    wait_timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    workflows = client.pod(pod_id).workflows
    run_payload = to_plain(workflows.create_run(workflow))

    # When the run starts on a form and inputs were provided, submit them.
    active_wait = run_payload.get("active_wait") or {}
    if active_wait.get("wait_type") == "HUMAN":
        if form_inputs:
            run_payload = to_plain(
                workflows.submit_form(
                    str(run_payload["id"]),
                    node_id=active_wait["node_id"],
                    inputs=form_inputs,
                )
            )
        else:
            return _waiting_on_form_payload(
                run_payload, active_wait, str(run_payload.get("id"))
            )

    if not wait:
        return run_payload
    return _wait_for_run(
        workflows,
        run_payload,
        wait_timeout=wait_timeout,
        poll_interval=poll_interval,
    )


def _wait_for_run(
    workflows: Any,
    run_payload: dict[str, Any],
    *,
    wait_timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    run_id = str(run_payload.get("id") or "")
    if not run_id:
        return run_payload
    deadline = time.monotonic() + wait_timeout
    waiting_note_printed = False
    while True:
        status = str(run_payload.get("status") or "").upper()
        active_wait = run_payload.get("active_wait") or {}
        if status == "WAITING":
            # Human waits need an operator; return with instructions instead
            # of spinning.
            if active_wait.get("wait_type") == "HUMAN":
                return _waiting_on_form_payload(run_payload, active_wait, run_id)
            # Agent/function/timer waits resolve on their own — keep polling.
            if not waiting_note_printed:
                console.print(
                    f"[dim]waiting on {active_wait.get('wait_type', 'external')} "
                    f"({active_wait.get('node_id', '?')})…[/dim]"
                )
                waiting_note_printed = True
        elif status not in _NON_TERMINAL_STATUSES:
            return run_payload
        if time.monotonic() >= deadline:
            return {
                **run_payload,
                "wait_timed_out": True,
                "message": f"Workflow run did not finish within {wait_timeout:.0f}s.",
            }
        time.sleep(poll_interval)
        run_payload = to_plain(workflows.run_get(run_id))
