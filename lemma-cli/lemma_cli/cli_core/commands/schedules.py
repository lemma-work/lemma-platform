from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from lemma_sdk.openapi_client.models.create_schedule_request import (
    CreateScheduleRequest,
)
from lemma_sdk.openapi_client.models.update_schedule_request import (
    UpdateScheduleRequest,
)

from ...cli_app.enums import DATASTORE_OPERATIONS as _DATASTORE_OPERATIONS
from ..confirm import confirm_destructive
from ..io import emit, list_items, to_plain
from ..payload import build_request, read_json
from ..sdk import pod_client
from ..state import fail, run_with_client, state_from_ctx

app = typer.Typer(help="Schedule commands.")


@app.command("init")
def init_schedule(
    name: str = typer.Argument(..., help="Schedule name (slug)."),
    root: Path | None = typer.Option(
        None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Scaffold a TIME/cron schedule bundle file. Set the target + cron, then import."""
    from ...cli_app.scaffold import ScaffoldError, init_resource, report

    try:
        result = init_resource("schedule", name, root=root, force=force)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report(result, next_hint="set target + cron, then `lemma pods import .`")


@app.command("schema")
def schema_schedule() -> None:
    """Print the JSONC example/shape for a schedule bundle file."""
    from ._authoring import print_resource_schema

    print_resource_schema("schedule")


def _normalize_datastore_operations(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        op = raw.strip().upper()
        if op in {"ALL", "*"}:
            return list(_DATASTORE_OPERATIONS)
        if op not in _DATASTORE_OPERATIONS:
            fail(
                f"Invalid --on value '{raw}'. Valid values: insert, update, delete, all."
            )
        if op not in normalized:
            normalized.append(op)
    return normalized


@app.command("list")
def list_schedules(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    name: str | None = typer.Option(None, "--name"),
    agent: str | None = typer.Option(None, "--agent"),
    workflow: str | None = typer.Option(None, "--workflow"),
    kind: str | None = typer.Option(
        None, "--type", help="TIME, WEBHOOK, or DATASTORE."
    ),
    active: bool | None = typer.Option(None, "--active/--inactive"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List schedules in the pod."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.list(
            schedule_type=kind.upper() if kind else None,
            is_active=active,
            name=name,
            agent_name=agent,
            workflow_name=workflow,
            limit=limit,
        ),
    )
    if result is None:
        return
    if state.output == "json":
        emit(state, result)
        return
    # The list carried an `Agent Id` column and nothing for workflows, so a
    # workflow-targeted schedule showed a blank target — `schedules get` had to
    # be run just to learn what a schedule starts. Compute the target the same
    # way `pods describe` does.
    payload = to_plain(result)
    items = [
        {**item, "target": schedule_target(item)}
        for item in list_items(payload)
        if isinstance(item, dict)
    ]
    emit(state, {**payload, "items": items} if isinstance(payload, dict) else items)


def schedule_target(schedule: dict[str, Any]) -> str:
    """`agent:<name>` / `workflow:<name>` — a schedule starts exactly one."""
    if schedule.get("agent_name"):
        return f"agent:{schedule['agent_name']}"
    if schedule.get("workflow_name"):
        return f"workflow:{schedule['workflow_name']}"
    return ""


@app.command("get")
def get_schedule(
    ctx: typer.Context,
    schedule: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a schedule."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.get(schedule),
    )
    if result is not None:
        emit(state, result)


@app.command("create")
def create_schedule(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None, "--name", help="Stable pod-scoped schedule name for import/export."
    ),
    agent: str | None = typer.Option(None, "--agent"),
    workflow: str | None = typer.Option(None, "--workflow"),
    cron: str | None = typer.Option(None, "--cron"),
    at: str | None = typer.Option(
        None, "--at", help="ISO timestamp for a one-time schedule."
    ),
    datastore: str | None = typer.Option(
        None, "--datastore", help="Table name for datastore schedules."
    ),
    on: list[str] = typer.Option(
        [],
        "--on",
        help=(
            "Datastore operation to react to (insert, update, delete, or all). "
            "Repeat for multiple. Required with --datastore."
        ),
    ),
    webhook_source: str | None = typer.Option(None, "--webhook-source"),
    account: str | None = typer.Option(None, "--account"),
    connector_trigger: str | None = typer.Option(
        None,
        "--connector-trigger",
        help=(
            "Connector trigger id for agent WEBHOOK schedules. Workflow WEBHOOK "
            "schedules derive the trigger from workflow start.config and reject this."
        ),
    ),
    filter_instruction: str | None = typer.Option(None, "--filter"),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create a schedule."""
    if bool(agent) == bool(workflow):
        fail("Provide exactly one of --agent or --workflow.")
    extra = read_json(json_payload, file, required=False)
    schedule_type = "TIME"
    config: dict[str, object] = {}
    if cron:
        config["cron"] = cron
    if at:
        config["scheduled_at"] = at
    if datastore:
        schedule_type = "DATASTORE"
        if not on and not extra.get("config", {}).get("operations"):
            fail(
                "Datastore schedules must declare operations explicitly: pass "
                "--on for each operation (insert, update, delete) or --on all."
            )
        config = {"table_name": datastore}
        if on:
            config["operations"] = _normalize_datastore_operations(on)
    if webhook_source:
        schedule_type = "WEBHOOK"
        config = {"source": webhook_source, **extra.get("config", {})}
    config = {**config, **extra.get("config", {})}
    schedule_type = str(extra.get("schedule_type") or schedule_type).upper()

    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.create(
            build_request(
                CreateScheduleRequest,
                {
                    **extra,
                    "name": name or extra.get("name"),
                    "schedule_type": schedule_type,
                    "agent_name": agent,
                    "workflow_name": workflow,
                    "config": config,
                    "account_id": account,
                    "connector_trigger_id": connector_trigger
                    or extra.get("connector_trigger_id"),
                    "filter_instruction": filter_instruction
                    or extra.get("filter_instruction"),
                    "filter_output_schema": extra.get("filter_output_schema"),
                },
                context="schedule",
            )
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("update")
def update_schedule(
    ctx: typer.Context,
    schedule: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update a schedule from a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.update(
            schedule, UpdateScheduleRequest.from_dict(payload)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("pause")
def pause_schedule(
    ctx: typer.Context,
    schedule: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Pause a schedule."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.update(
            schedule, UpdateScheduleRequest.from_dict({"is_active": False})
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("resume")
def resume_schedule(
    ctx: typer.Context,
    schedule: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Resume a paused schedule."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.update(
            schedule, UpdateScheduleRequest.from_dict({"is_active": True})
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("delete")
def delete_schedule(
    ctx: typer.Context,
    schedule: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a schedule."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete schedule {schedule}?", yes)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.delete(schedule),
    )
    if result is None:
        emit(state, {"ok": True})
