from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from lemma_sdk.openapi_client.models.create_schedule_request import (
    CreateScheduleRequest,
)
from lemma_sdk.openapi_client.models.schedule_run_status import ScheduleRunStatus
from lemma_sdk.openapi_client.models.update_schedule_request import (
    UpdateScheduleRequest,
)

from ...cli_app.enums import DATASTORE_OPERATIONS as _DATASTORE_OPERATIONS
from ..confirm import confirm_destructive
from ..deprecation import warn_deprecated
from ..io import emit, list_items, to_plain
from ..payload import build_request, read_json
from ..sdk import pod_client
from ..state import CliState, err_console, fail, run_with_client, state_from_ctx

app = typer.Typer(help="Schedule commands.")
runs_app = typer.Typer(help="Schedule run commands: what each event or tick became.")
app.add_typer(runs_app, name="runs")

# From the SDK's generated enum, so a status the API adds is accepted here
# without a second list to keep in step.
_RUN_STATUSES = tuple(status.value for status in ScheduleRunStatus)


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
def schema_schedule(ctx: typer.Context) -> None:
    """Print the JSONC example/shape for a schedule bundle file."""
    from ._authoring import print_resource_schema

    print_resource_schema(ctx, "schedule")


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
        emit(state, _shown(result))
        return
    # The list carried an `Agent Id` column and nothing for workflows, so a
    # workflow-targeted schedule showed a blank target — `schedules get` had to
    # be run just to learn what a schedule starts. Compute the target the same
    # way `pods describe` does.
    payload = _shown(result)
    items = [
        {**item, "target": schedule_target(item)}
        for item in list_items(payload)
        if isinstance(item, dict)
    ]
    emit(state, {**payload, "items": items} if isinstance(payload, dict) else items)


#: Response keys that are deprecated and always null. Left out of what is
#: printed: an agent reading a schedule took ``account_id`` for a field it
#: should fill in, though a schedule names a pod connector now.
_DEAD_FIELDS = ("account_id",)


def _shown(result: object) -> Any:
    """A schedule, or a page of them, without its dead fields."""
    payload = to_plain(result)

    def strip(item: object) -> object:
        if not isinstance(item, dict):
            return item
        return {k: v for k, v in item.items() if not (k in _DEAD_FIELDS and v is None)}

    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return {**payload, "items": [strip(item) for item in payload["items"]]}
    if isinstance(payload, list):
        return [strip(item) for item in payload]
    return strip(payload)


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
        emit(state, _shown(result))


def _text_field(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _create_target(
    payload: dict[str, object],
    *,
    agent: str | None,
    workflow: str | None,
    connector: str | None,
) -> tuple[str | None, str | None, str | None]:
    """``(agent, workflow, pod connector)``, from the flags or else the payload.

    A flag wins. Resolved before the check and before the request is built:
    the flags used to be written over the payload unconditionally, so a target
    named only in --data/--file was refused as missing or sent as null.
    """
    agent = agent or _text_field(payload, "agent_name")
    workflow = workflow or _text_field(payload, "workflow_name")
    if bool(agent) == bool(workflow):
        fail(
            "Provide exactly one of --agent or --workflow (or agent_name / "
            "workflow_name in the payload)."
        )
    return agent, workflow, connector or _text_field(payload, "pod_connector")


def _deprecated_account(payload: dict[str, object], account: str | None) -> str | None:
    """The account a schedule was told to listen as, the way it was before
    pod connectors: ``--account`` or ``account_id`` in the payload.

    Still sent, and the server turns it into the pod connector serving that
    account to the caller; it says so, and so does this, once.
    """
    account = account or _text_field(payload, "account_id")
    if account:
        warn_deprecated(
            "schedules create --account"
            if "account_id" not in payload
            else "account_id in a schedule's --data",
            "pass --connector <slug> (or pod_connector in the payload); the "
            "pod connector decides the account",
        )
    return account


@app.command("create")
def create_schedule(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None, "--name", help="Stable pod-scoped schedule name for import/export."
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help=(
            "Pod agent to wake, by name. Pass POD_DEFAULT to wake the pod's "
            "default assistant, which has no name of its own; that target "
            "requires --instruction."
        ),
    ),
    workflow: str | None = typer.Option(None, "--workflow"),
    instruction: str | None = typer.Option(
        None,
        "--instruction",
        help=(
            "What the target should do when this fires. Required with "
            "--agent POD_DEFAULT. Unlike --filter, which decides whether to "
            "fire at all, this directs the work afterwards."
        ),
    ),
    cron: str | None = typer.Option(
        None,
        "--cron",
        help=(
            "5-field cron, e.g. '0 9 * * 1-5'. Read in --timezone, and in UTC "
            "when that is not given."
        ),
    ),
    at: str | None = typer.Option(
        None,
        "--at",
        help=(
            "ISO timestamp for a one-time schedule. Without an offset it is "
            "read in --timezone, or UTC."
        ),
    ),
    tz: str | None = typer.Option(
        None,
        "--timezone",
        "--tz",
        help=(
            "IANA zone --cron/--at are read in, e.g. Europe/Berlin. Default "
            "UTC. Follows daylight saving, which a UTC cron cannot."
        ),
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
    connector: str | None = typer.Option(
        None,
        "--connector",
        help=(
            "Pod connector, by slug, a WEBHOOK schedule listens through -- for "
            "--connector-trigger or an MCP server's event. See `lemma "
            "connectors pod-list`."
        ),
    ),
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
    # Deprecated: the account it listens as, from before pod connectors.
    # Hidden, still served -- as the pod connector serving that account.
    account: str | None = typer.Option(None, "--account", hidden=True),
) -> None:
    """Create a schedule."""
    extra = read_json(json_payload, file, required=False)
    agent, workflow, connector = _create_target(
        extra, agent=agent, workflow=workflow, connector=connector
    )
    account = _deprecated_account(extra, account)
    # One schedule has one trigger. Each later one used to replace the config
    # the earlier built, so `--cron … --webhook-source …` quietly lost the cron.
    triggers = [
        flag
        for flag, given in (
            ("--cron/--at", cron or at),
            ("--datastore", datastore),
            ("--webhook-source", webhook_source),
        )
        if given
    ]
    if len(triggers) > 1:
        fail(f"A schedule has one trigger; pick one of {', '.join(triggers)}.")
    schedule_type = "TIME"
    config: dict[str, object] = {}
    if cron:
        config["cron"] = cron
    if at:
        config["scheduled_at"] = at
    if tz:
        config["timezone"] = tz
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
                    "instruction": instruction or extra.get("instruction"),
                    "pod_connector": connector,
                    "connector_trigger_id": connector_trigger
                    or extra.get("connector_trigger_id"),
                    "filter_instruction": filter_instruction
                    or extra.get("filter_instruction"),
                    "filter_output_schema": extra.get("filter_output_schema"),
                    **({"account_id": account} if account else {}),
                },
                context="schedule",
            )
        ),
    )
    if result is not None:
        emit(state, _shown(result))
        _note_next_fire(state, result)


def _note_next_fire(state: CliState, result: object) -> None:
    """Say on stderr when a new TIME schedule will first fire, in UTC and in
    its own zone -- so nobody has to create a test schedule and wait to find
    out which zone a cron is read in."""
    if state.output == "json":
        return
    payload = to_plain(result)
    if not isinstance(payload, dict) or not payload.get("next_fire_at"):
        return
    utc = payload["next_fire_at"]
    local = payload.get("next_fire_at_local")
    raw_config = payload.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    zone = config.get("timezone") or "UTC"
    detail = f" ({local} in {zone})" if local and zone != "UTC" else ""
    hint = (
        ""
        if config.get("timezone")
        else " The cron is read in UTC; pass --timezone <IANA zone> to change it."
    )
    err_console.print(f"[dim]next fire:[/dim] {utc} UTC{detail}.{hint}")


@app.command("update")
def update_schedule(
    ctx: typer.Context,
    schedule: str = typer.Argument(...),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help=(
            "Retarget at a pod agent, by name. Pass POD_DEFAULT for the pod's "
            "default assistant; that target requires --instruction."
        ),
    ),
    workflow: str | None = typer.Option(
        None, "--workflow", help="Retarget at a workflow, by name."
    ),
    instruction: str | None = typer.Option(
        None,
        "--instruction",
        help=(
            "What the target should do when this fires. Required with "
            "--agent POD_DEFAULT. Unlike --filter, which decides whether to "
            "fire at all, this directs the work afterwards."
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
    """Update a schedule."""
    extra = read_json(json_payload, file, required=False)
    flags = {
        "agent_name": agent,
        "workflow_name": workflow,
        "instruction": instruction,
        "filter_instruction": filter_instruction,
    }
    payload = {**extra, **{k: v for k, v in flags.items() if v is not None}}
    if not payload:
        fail("Nothing to update. Pass a flag, or --data/--file with a payload.")
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.update(
            schedule,
            # `build_request`, not `from_dict`: the generated model drops every
            # key it does not declare, so a hand-written payload naming the
            # wrong field was applied as an empty update and reported success.
            build_request(UpdateScheduleRequest, payload, context="schedule"),
        ),
    )
    if result is not None:
        emit(state, _shown(result))


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
        emit(state, _shown(result))


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
        emit(state, _shown(result))


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


@runs_app.command("list")
def list_schedule_runs(
    ctx: typer.Context,
    schedule: str = typer.Argument(..., help="Schedule name or id."),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Only runs with this status, e.g. COMPLETED, FILTERED, DEAD_LETTERED.",
    ),
    skipped: bool | None = typer.Option(
        None,
        "--skipped/--no-skipped",
        help=(
            "--skipped: only events the schedule's filter skipped. "
            "--no-skipped: leave them out. Default: both."
        ),
    ),
) -> None:
    """List a schedule's runs, newest first."""
    wanted = status.upper() if status else None
    if wanted is not None and wanted not in _RUN_STATUSES:
        raise typer.BadParameter(
            f"--status is one of {', '.join(_RUN_STATUSES)}.", param_hint="--status"
        )
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.runs(
            schedule, limit=limit, status=wanted, skipped=skipped
        ),
    )
    if result is not None:
        emit(state, result)


@runs_app.command("retry")
def retry_schedule_run(
    ctx: typer.Context,
    schedule: str = typer.Argument(..., help="Schedule name or id."),
    run: str = typer.Argument(..., help="The run to retry."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Run a failed or dead-lettered run again with the same event."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).schedules.retry_run(schedule, run),
    )
    if result is not None:
        emit(state, result)
