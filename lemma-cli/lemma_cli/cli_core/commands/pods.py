from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from lemma_sdk.openapi_client.models.pod_create_request import PodCreateRequest
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from ..context import (
    remember_org,
    remember_pod,
    render_session_selection,
    resolve_pod,
    selected_org,
    selected_pod,
)
from ..confirm import confirm_destructive
from ..io import emit, format_columns, list_items, to_plain
from ..payload import build_request
from ..sdk import pod_client
from ..select import select_from_items
from ..state import console, fail, run_with_client, state_from_ctx

app = typer.Typer(
    help="Pod commands.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def resolve_pod_id(client, state, explicit: str | None = None) -> str:  # type: ignore[no-untyped-def]
    """Resolve a pod selector (passed as `--pod`/positional, or a stored default)
    to a pod UUID, accepting EITHER a UUID OR a pod name/slug.

    Pod-scoped and pod-detail routes require a UUID; passing a name raised
    "badly formed hexadecimal UUID string". Stored defaults are already UUIDs,
    so the common path short-circuits and only an explicit name pays for the
    `pods list` lookup.
    """
    selector = selected_pod(state, explicit)
    if not selector:
        # selected_pod(required=True) already failed; this is just for typing.
        fail("No pod selected. Run `lemma pods`, pass --pod, or set LEMMA_POD_ID.")
    selector = str(selector)
    if _is_uuid(selector):
        return selector

    org_id = selected_org(state, required=False)
    # Page through every pod so a name beyond the first page still resolves
    # (and so ambiguity is detected across the full set, not just page one).
    items: list[dict] = []
    page_token: str | None = None
    while True:
        response = client.pods.list(org_id=org_id, limit=200, page_token=page_token)
        items.extend(list_items(response))
        page_token = str(to_plain(response).get("next_page_token") or "") or None
        if not page_token:
            break
    needle = selector.casefold()
    matches = [
        item
        for item in items
        if str(item.get("id")) == selector
        or str(item.get("slug") or "").casefold() == needle
        or str(item.get("name") or "").casefold() == needle
    ]
    if not matches:
        names = ", ".join(sorted(str(i.get("name")) for i in items if i.get("name"))) or "(none)"
        fail(f"Pod not found: '{selector}'. Available pods: {names}.")
    if len(matches) > 1:
        ids = ", ".join(f"{m.get('name')} ({m.get('id')})" for m in matches)
        fail(
            f"Pod name '{selector}' is ambiguous; it matches {len(matches)} pods: {ids}. "
            "Pass the pod id instead."
        )
    return str(matches[0].get("id") or selector)


@app.callback()
def pods_root(
    ctx: typer.Context,
    org: str | None = typer.Option(
        None, "--org", help="Organization id or selected org fallback."
    ),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """Open the pod selector."""
    if ctx.invoked_subcommand is not None:
        return
    select_pod(ctx, org=org, limit=limit)


@app.command("list")
def list_pods(
    ctx: typer.Context,
    org: str | None = typer.Option(
        None, "--org", help="Organization id or selected org fallback."
    ),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List pods in the organization."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: _mark_current(
            client.pods.list(org_id=selected_org(s, org), limit=limit),
            selected_pod(s, required=False),
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("select")
def select_pod(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None, help="Pod id, slug, or name. Omit for an interactive picker."
    ),
    org: str | None = typer.Option(None, "--org", help="Organization to scope the pick."),
    limit: int = typer.Option(100, "--limit"),
    export: bool = typer.Option(
        False,
        "--export",
        "-x",
        help='Print only `export LEMMA_*` lines, for: eval "$(lemma pods select X -x)".',
    ),
    save_default: bool = typer.Option(
        False,
        "--save-default",
        help="Also persist as this server's default pod (survives new shells).",
    ),
) -> None:
    """Set the active pod for THIS shell session only — never other terminals.

    Prints `export LEMMA_POD_ID=…`; apply it to your shell with
    `eval "$(lemma pods select <name> -x)"`. Other terminals keep their own pod
    (their env, else this server's saved default). Change the persistent per-server
    default with `--save-default` or `lemma config set-default-pod`.
    """
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        org_id = selected_org(s, org, required=False)
        if name:
            return resolve_pod(client, s, name, org=org_id)
        items = list_items(client.pods.list(org_id=org_id, limit=limit))
        return select_from_items(
            items, label="pod", current_id=selected_pod(s, required=False)
        )

    selected = run_with_client(ctx, run)
    if not selected:
        return
    pod_id = str(selected.get("id") or "")
    org_id = str(selected.get("organization_id") or "") or None
    if save_default:
        remember_pod(state, pod_id)
        if org_id:
            remember_org(state, org_id)
    env = {"LEMMA_POD_ID": pod_id}
    if org_id:
        env["LEMMA_ORG_ID"] = org_id
    display = str(selected.get("name") or selected.get("slug") or pod_id)
    render_session_selection(
        state,
        env=env,
        label="pod",
        name=display,
        command_hint=f"lemma pods select {display}",
        export_only=export,
        saved=save_default,
    )


@app.command("init")
def init_pod_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pod name; also the bundle directory."),
    directory: Path | None = typer.Option(
        None, "--dir", help="Target directory (default: ./<name>)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
    starter: bool = typer.Option(
        True,
        "--starter/--no-starter",
        help="Include the starter items table + a starter agent. "
        "Use --no-starter for a bare pod.json + README when you already know your "
        "resource names.",
    ),
) -> None:
    """Scaffold a pod bundle on disk (pod.json + README + AGENTS.md, plus a shared
    table and a starter agent granted to it unless --no-starter). Edit, then import."""
    from ...cli_app.scaffold import ScaffoldError, init_pod, report, slugify

    try:
        target = directory or (Path.cwd() / slugify(name))
        result = init_pod(target, name, force=force, with_starter=starter)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report(
        result,
        next_hint=(
            f"lemma pods create {result.name} && "
            f"lemma pods import {target} --pod {result.name}"
        ),
    )


@app.command("create")
def create_pod(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    org: str | None = typer.Option(None, "--org"),
    description: str | None = typer.Option(None, "--description"),
    with_starter: bool = typer.Option(
        False,
        "--with-starter",
        help="Scaffold a starter bundle (./<name>) and import it into the new pod.",
    ),
    directory: Path | None = typer.Option(
        None, "--dir", help="Starter bundle directory (default: ./<name>). Implies --with-starter."
    ),
) -> None:
    """Create a pod. With --with-starter, also scaffold a starter bundle and import it."""
    state = state_from_ctx(ctx)
    want_starter = with_starter or directory is not None

    # Scaffold BEFORE creating the pod so a scaffold failure (e.g. the target
    # dir already exists) can't leave an empty backend pod orphaned.
    target: Path | None = None
    if want_starter:
        from ...cli_app.scaffold import ScaffoldError, init_pod, slugify

        try:
            target = directory or (Path.cwd() / slugify(name))
            scaffold = init_pod(target, name)
        except ScaffoldError as exc:
            raise typer.BadParameter(str(exc)) from exc
        console.print(f"[green]starter[/green] scaffolded {len(scaffold.files)} files -> {target}")

    result = run_with_client(
        ctx,
        lambda client, s: client.pods.create(
            build_request(
                PodCreateRequest,
                {
                    "organization_id": selected_org(s, org) or client.org_id,
                    "name": name,
                    "description": description,
                },
            )
        ),
    )
    if result is None:
        return
    emit(state, result)

    if not want_starter or target is None:
        return
    from ...cli_app.pod_bundle import import_pod_bundle

    pod_id = str(to_plain(result).get("id") or "")
    run_with_client(
        ctx,
        lambda client, s: import_pod_bundle(
            client, pod_id=pod_id, source_dir=target, upsert=True
        ),
    )
    console.print(f"[green]starter[/green] imported into pod {pod_id}")


@app.command("get")
def get_pod(ctx: typer.Context, pod: str | None = typer.Argument(None)) -> None:
    """Show a pod (by id or name)."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, s: client.pods.get(resolve_pod_id(client, s, pod))
    )
    if result is not None:
        emit(state, result)


@app.command("describe")
def describe_pod(
    ctx: typer.Context,
    pod: str | None = typer.Argument(None),
    limit: int = typer.Option(50, "--limit", help="Maximum rows per resource table."),
) -> None:
    """Show a pod (by id or name) with all its resources."""
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        pod_id = resolve_pod_id(client, s, pod)
        pod_sdk = client.pod(pod_id)
        return {
            "pod": client.pods.get(pod_id),
            "tables": list_items(pod_sdk.tables.list(limit=limit)),
            "functions": list_items(pod_sdk.functions.list(limit=limit)),
            "agents": list_items(pod_sdk.agents.list(limit=limit)),
            "workflows": list_items(pod_sdk.workflows.list(limit=limit)),
            "schedules": list_items(pod_sdk.schedules.list(limit=limit)),
            "files": pod_sdk.files.tree("/"),
        }

    result = run_with_client(ctx, run)
    if result is None:
        return
    if state.output == "json":
        emit(state, result)
        return
    _render_pod_description(to_plain(result))


@app.command("doctor")
def doctor_pod(
    ctx: typer.Context,
    pod: str | None = typer.Argument(None),
) -> None:
    """Check a pod's wiring: grants pointing at missing tables, workflow/schedule
    targets that don't exist, and surfaces missing an agent or account."""

    def run(client, s):  # type: ignore[no-untyped-def]
        from ...cli_app.pod_bundle import DESTRUCTIVE_PERMISSION_IDS

        pod_sdk = pod_client(client, s, pod)
        tables = {str(t.get("name")) for t in to_plain(list_items(pod_sdk.tables.list(limit=1000)))}
        agent_items = to_plain(list_items(pod_sdk.agents.list(limit=1000)))
        agents = {str(a.get("name")) for a in agent_items}
        functions = {str(f.get("name")) for f in to_plain(list_items(pod_sdk.functions.list(limit=1000)))}
        workflows = {str(w.get("name")) for w in to_plain(list_items(pod_sdk.workflows.list(limit=1000)))}
        schedules = to_plain(list_items(pod_sdk.schedules.list(limit=1000)))

        errors: list[str] = []
        warnings: list[str] = []

        def check_grants(kind: str, name: str) -> list[dict]:
            try:
                perms = to_plain(getattr(pod_sdk, kind).permissions(name))
            except Exception as exc:  # noqa: BLE001 — surface, don't hide, a failed check
                warnings.append(f"could not read permissions for {kind[:-1]} '{name}': {exc}")
                return []
            grants = [g for g in (perms.get("grants") or []) if isinstance(g, dict)]
            for grant in grants:
                rtype = grant.get("resource_type")
                rname = str(grant.get("resource_name") or "")
                if rtype == "datastore_table" and rname not in tables:
                    errors.append(f"{kind[:-1]} '{name}' is granted on table '{rname}' which does not exist.")
                elif rtype == "agent" and rname not in agents:
                    errors.append(f"{kind[:-1]} '{name}' is granted on agent '{rname}' which does not exist.")
                elif rtype == "function" and rname not in functions:
                    errors.append(f"{kind[:-1]} '{name}' is granted on function '{rname}' which does not exist.")
                elif rtype == "folder":
                    warnings.append(f"{kind[:-1]} '{name}' grants folder '{rname}' — verify it exists / will be created.")
                destructive = sorted(
                    p for p in (grant.get("permission_ids") or []) if p in DESTRUCTIVE_PERMISSION_IDS
                )
                if destructive:
                    warnings.append(
                        f"{kind[:-1]} '{name}' holds destructive permission(s) "
                        f"{', '.join(destructive)} on {rtype} '{rname}' — standing "
                        "authority, no approval prompt at runtime."
                    )
            return grants

        def agent_has_runtime(item: dict, name: str) -> bool:
            # Prefer the list payload; only fetch the detail if it omits runtime.
            runtime = item.get("agent_runtime") or {}
            if runtime.get("profile_id"):
                return True
            try:
                detail = to_plain(pod_sdk.agents.get(name))
            except Exception:  # noqa: BLE001
                return False
            return bool((detail.get("agent_runtime") or {}).get("profile_id"))

        for item in agent_items:
            name = str(item.get("name"))
            grants = check_grants("agents", name)
            if name and not agent_has_runtime(item, name):
                warnings.append(f"agent '{name}' has no pinned runtime — relies on the backend default (system:lemma).")
            toolsets = {str(t).upper() for t in (item.get("toolsets") or []) if isinstance(t, str)}
            if "SUBAGENTS" in toolsets and not any(
                g.get("resource_type") == "agent" for g in grants
            ):
                warnings.append(
                    f"agent '{name}' has the SUBAGENTS toolset but no agent grants — "
                    "self-spawn only; grant `agent:<other>:execute` to fan out."
                )
        for name in functions:
            check_grants("functions", name)

        for wname in workflows:
            wf = to_plain(pod_sdk.workflows.get(wname))
            for node in (wf.get("nodes") or []):
                cfg = node.get("config") or {}
                target_agent = cfg.get("agent_name")
                target_fn = cfg.get("function_name")
                if target_agent and target_agent not in agents:
                    errors.append(f"workflow '{wname}' node '{node.get('id')}' targets missing agent '{target_agent}'.")
                if target_fn and target_fn not in functions:
                    errors.append(f"workflow '{wname}' node '{node.get('id')}' targets missing function '{target_fn}'.")

        for sched in schedules:
            sname = sched.get("name") or sched.get("id")
            a, w = sched.get("agent_name"), sched.get("workflow_name")
            if a and a not in agents:
                errors.append(f"schedule '{sname}' targets missing agent '{a}'.")
            if w and w not in workflows:
                errors.append(f"schedule '{sname}' targets missing workflow '{w}'.")

        try:
            surfaces = to_plain(list_items(pod_sdk.surfaces.list(limit=1000)))
        except Exception as exc:  # noqa: BLE001 — surface, don't hide, a failed check
            surfaces = []
            warnings.append(f"could not list surfaces: {exc}")
        for surf in surfaces:
            plat = surf.get("platform") or surf.get("name")
            agent_name = surf.get("default_agent_name") or surf.get("agent_name")
            if agent_name and agent_name not in agents:
                errors.append(f"surface '{plat}' points at missing agent '{agent_name}'.")
            if str(surf.get("credential_mode") or "").upper() == "CUSTOM" and not surf.get("account_id"):
                warnings.append(f"surface '{plat}' is CUSTOM but has no account_id.")

        return {"errors": errors, "warnings": warnings, "counts": {
            "tables": len(tables), "agents": len(agents), "functions": len(functions),
            "workflows": len(workflows), "schedules": len(schedules)}}

    result = run_with_client(ctx, run)
    if result is None:
        return
    report = to_plain(result)
    errors, warnings = report["errors"], report["warnings"]
    for msg in errors:
        console.print(f"[red]error[/red]  {msg}")
    for msg in warnings:
        console.print(f"[yellow]warn[/yellow]   {msg}")
    if not errors and not warnings:
        console.print("[green]ok[/green] pod wiring looks healthy.")
    elif not errors:
        console.print(f"[green]ok[/green] no errors ({len(warnings)} warning(s)).")
    else:
        console.print(f"[red]{len(errors)} error(s)[/red], {len(warnings)} warning(s).")
        raise typer.Exit(1)


@app.command("delete")
def delete_pod(
    ctx: typer.Context,
    pod: str | None = typer.Argument(None),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a pod (by id or name)."""
    state = state_from_ctx(ctx)
    selector = selected_pod(state, pod) or ""
    confirm_destructive(f"Delete pod {selector}?", yes)
    result = run_with_client(
        ctx, lambda client, s: client.pods.delete(resolve_pod_id(client, s, pod))
    )
    if result is None:
        emit(state, {"ok": True})


@app.command("members")
def members(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List pod members."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).members.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@app.command("export")
def export_pod(
    ctx: typer.Context,
    output_dir: Path = typer.Argument(Path("."), help="Directory to write the bundle into."),
    pod: str | None = typer.Option(None, "--pod"),
    resource: list[str] = typer.Option(
        [],
        "--resource",
        "-r",
        help="Resource type to export. Repeat for multiple. Defaults to the whole pod.",
    ),
    name: list[str] = typer.Option(
        [],
        "--name",
        "-n",
        help="Resource name/id to export. Repeat for multiple.",
    ),
    exclude: list[str] = typer.Option(
        [],
        "--exclude",
        help="Resource type to skip when exporting a full pod bundle.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite the output directory."),
    as_template: bool = typer.Option(
        False,
        "--as-template",
        help="Strip instance data (pinned agent runtimes, surface account ids) for a reusable starter.",
    ),
    with_data: bool = typer.Option(
        False,
        "--with-data",
        help="Also export table rows to data.csv (up to 10k rows/table).",
    ),
    with_files: bool = typer.Option(
        False,
        "--with-files",
        help="Also download pod file contents into the bundle.",
    ),
) -> None:
    """Export pod resources to a local bundle."""
    state = state_from_ctx(ctx)
    include = _normalize_resource_types(resource, option="--resource")
    excluded = _normalize_resource_types(exclude, option="--exclude")
    from ...cli_app.pod_bundle import export_pod_bundle

    result = run_with_client(
        ctx,
        lambda client, s: export_pod_bundle(
            client,
            pod_id=resolve_pod_id(client, s, pod),
            output_dir=output_dir,
            force=force,
            include=include or None,
            names=set(name) or None,
            exclude=excluded or None,
            with_data=with_data,
            with_files=with_files,
        ),
    )
    if result is None:
        return
    emit(state, result)
    if as_template:
        from ...cli_app.scaffold import templatize_bundle

        root, changed = templatize_bundle(output_dir)
        console.print(
            f"[green]template[/green] stripped instance data from {changed} file(s) in {root}"
        )


def _parse_import_variables(
    var: list[str], values: Path | None
) -> dict[str, str]:
    """Merge a --values JSON file and repeated --var NAME=VALUE flags into one
    {name: value} map (--var wins on conflict)."""
    merged: dict[str, str] = {}
    if values is not None:
        import json

        try:
            data = json.loads(values.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"--values: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise typer.BadParameter("--values must be a JSON object of {name: value}.")
        merged.update({str(key): str(value) for key, value in data.items()})
    for item in var:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise typer.BadParameter(f"--var must be NAME=VALUE, got: {item!r}")
        merged[name.strip()] = value
    return merged


@app.command("import")
def import_pod(
    ctx: typer.Context,
    source_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Pod bundle, resource folder, or single resource directory.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
    upsert: bool = typer.Option(True, "--upsert/--no-upsert"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    pod_member: str | None = typer.Option(
        None,
        "--pod-member",
        help="Pod-member id to resolve workflow assignee variables to "
        "(defaults to the importing user's own membership).",
    ),
    var: list[str] = typer.Option(
        [],
        "--var",
        help="Resolve a pod.json variable: NAME=VALUE (repeat for multiple).",
    ),
    values: Path | None = typer.Option(
        None,
        "--values",
        exists=True,
        dir_okay=False,
        readable=True,
        help="JSON file of {variable: value} mappings for pod.json variables.",
    ),
    with_data: bool = typer.Option(
        False,
        "--with-data",
        help="Seed table rows from bundled data.csv files (new tables only).",
    ),
    with_files: bool = typer.Option(
        False,
        "--with-files",
        help="Upload bundled file contents into the pod.",
    ),
    set_pod_meta: bool = typer.Option(
        False,
        "--set-pod-meta",
        help="Also apply the bundle's pod name/description/icon (off by default "
        "so importing never renames the target pod).",
    ),
) -> None:
    """Import a local bundle into the pod.

    Non-portable ids (workflow assignees, schedule/surface accounts) are exported
    as ${name} variables listed under `variables` in pod.json. Resolve them with
    `--var name=value` or a `--values file.json`; pod-member variables default to
    your own membership, and any unresolved account variable simply drops its
    field so the import still succeeds (wire the account up afterwards).
    """
    state = state_from_ctx(ctx)
    from ...cli_app.pod_bundle import import_pod_bundle

    variables = _parse_import_variables(var, values)

    result = run_with_client(
        ctx,
        lambda client, s: import_pod_bundle(
            client,
            pod_id=resolve_pod_id(client, s, pod),
            source_dir=source_dir,
            upsert=upsert,
            dry_run=dry_run,
            pod_member_id=pod_member,
            with_data=with_data,
            with_files=with_files,
            variables=variables,
            set_pod_meta=set_pod_meta,
        ),
    )
    if result is not None:
        if state.output == "json":
            emit(state, result)
        else:
            _emit_import_result(result)
        if not result.get("ok", True):
            raise typer.Exit(code=1)


def _emit_import_result(result: dict[str, Any]) -> None:
    """Render an import/dry-run result with full error messages and plan."""
    dry_run = bool(result.get("dry_run"))
    header = "Import plan (dry run)" if dry_run else "Import"
    console.print(f"[bold]{header}[/bold]  [dim]{result.get('source_dir', '')}[/dim]")

    summary = result.get("summary") or {}
    actions = [
        (resource_type, action)
        for resource_type, entries in summary.items()
        for action in entries or []
    ]
    if actions:
        view = Table(box=box.SIMPLE_HEAVY)
        view.add_column("Resource")
        view.add_column("Action" if not dry_run else "Planned")
        view.add_column("Name")
        for resource_type, action in actions:
            verb, _, name = str(action).partition(":")
            view.add_row(resource_type, verb, name)
        console.print(view)
    elif not result.get("errors"):
        console.print("[dim]Nothing to import.[/dim]")

    errors = result.get("errors") or []
    for error in errors:
        path = error.get("path") if isinstance(error, dict) else ""
        message = error.get("message") if isinstance(error, dict) else str(error)
        console.print(f"[red]error[/red] {path}: {message}")

    # Non-fatal findings (connector grants are env-specific, destructive grants
    # are standing authority, SUBAGENTS without agent grants, ...). During a
    # real import these were already streamed; a dry run surfaces them here.
    if dry_run:
        for advisory in result.get("advisories") or []:
            console.print(f"[yellow]advisory[/yellow] {advisory}")

    if result.get("ok", True):
        console.print("[green]OK[/green]" if dry_run else "[green]Imported[/green]")
    else:
        console.print(f"[red]Failed with {len(errors)} error(s)[/red]")


def _normalize_resource_types(values: list[str], *, option: str) -> set[str]:
    from ...cli_app.pod_bundle import normalize_resource_dir_name

    normalized: set[str] = set()
    for value in values:
        resource_type = normalize_resource_dir_name(value)
        if not resource_type:
            raise typer.BadParameter(
                f"Unknown resource type for {option}: {value}. "
                "Use tables, functions, agents, workflows, schedules, surfaces, apps, or files."
            )
        normalized.add(resource_type)
    return normalized


def _mark_current(payload, selected_id: str | None):  # type: ignore[no-untyped-def]
    items = list_items(payload)
    if not items:
        return payload
    for item in items:
        item["active"] = bool(selected_id and str(item.get("id")) == selected_id)
    if isinstance(payload, dict):
        next_payload = dict(payload)
        next_payload["items"] = items
        return next_payload
    return items


def _short(value: Any, max_length: int = 48) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= max_length else text[: max_length - 1] + "..."


def _count(value: Any) -> str:
    return str(len(value)) if isinstance(value, list) else ""


def _render_table(title: str, rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> None:
    view = Table(title=f"{title} ({len(rows)})", box=box.SIMPLE_HEAVY)
    for heading, _key in columns:
        view.add_column(heading, overflow="fold")
    if rows:
        for row in rows:
            view.add_row(*(_short(row.get(key)) for _heading, key in columns))
    else:
        view.add_row(*([""] * len(columns)))
    console.print(view)


def _schedule_target(schedule: dict[str, Any]) -> str:
    if schedule.get("agent_name"):
        return f"agent:{schedule['agent_name']}"
    if schedule.get("workflow_name"):
        return f"workflow:{schedule['workflow_name']}"
    return ""


def _workflow_node_count(workflow: dict[str, Any]) -> str:
    return _count(workflow.get("nodes"))


def _render_pod_description(data: dict[str, Any]) -> None:
    pod = data.get("pod") if isinstance(data.get("pod"), dict) else {}
    pod_title = str(pod.get("name") or pod.get("id") or "Pod")
    pod_lines = [
        f"[bold]{pod_title}[/bold]",
        f"id: {pod.get('id', '')}",
    ]
    if pod.get("description"):
        pod_lines.append(str(pod["description"]))
    console.print(Panel("\n".join(pod_lines), title="Pod", box=box.ROUNDED))

    tables = [
        {
            **item,
            "columns": format_columns(
                item.get("columns"),
                primary_key=item.get("primary_key_column"),
                max_columns=8,
            ),
        }
        for item in data.get("tables", [])
        if isinstance(item, dict)
    ]
    _render_table(
        "Tables",
        tables,
        [("Name", "name"), ("Columns", "columns"), ("Primary Key", "primary_key_column")],
    )
    _render_table(
        "Agents",
        data.get("agents", []),
        [("Name", "name"), ("Model", "model"), ("Description", "description")],
    )
    _render_table(
        "Functions",
        data.get("functions", []),
        [("Name", "name"), ("Type", "type"), ("Description", "description")],
    )
    workflows = [
        {**item, "node_count": _workflow_node_count(item)}
        for item in data.get("workflows", [])
        if isinstance(item, dict)
    ]
    _render_table(
        "Workflows",
        workflows,
        [("Name", "name"), ("Nodes", "node_count"), ("Description", "description")],
    )
    schedules = [
        {**item, "target": _schedule_target(item)}
        for item in data.get("schedules", [])
        if isinstance(item, dict)
    ]
    _render_table(
        "Schedules",
        schedules,
        [("ID", "id"), ("Type", "schedule_type"), ("Target", "target"), ("Active", "is_active")],
    )
    _render_file_tree(data.get("files"))


def _render_file_tree(files_payload: Any) -> None:
    tree_payload = files_payload.get("tree") if isinstance(files_payload, dict) else None
    if not isinstance(tree_payload, dict):
        console.print(Panel("No file tree available.", title="Pod Files", box=box.ROUNDED))
        return
    root = Tree("[bold]/[/bold]")
    _add_file_tree_children(root, tree_payload)
    console.print(Panel(root, title="Pod Files", box=box.ROUNDED))


def _add_file_tree_children(view: Tree, node: dict[str, Any]) -> None:
    children = node.get("children") or []
    if not isinstance(children, list):
        return
    for child in children:
        if not isinstance(child, dict):
            continue
        name = str(child.get("name") or child.get("path") or "")
        kind = str(child.get("kind") or "").upper()
        label = f"[cyan]{name}/[/cyan]" if kind == "FOLDER" else name
        child_view = view.add(label)
        if kind == "FOLDER":
            _add_file_tree_children(child_view, child)
