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
from .schedules import schedule_target
from ..state import console, err_console, fail, run_with_client, state_from_ctx

app = typer.Typer(
    help="Pod commands.",
    invoke_without_command=True,
    no_args_is_help=False,
)

# `describe` is an overview, and the API returns the pod's *entire* file tree
# with no depth parameter — so the bound is ours to apply. Two levels shows the
# shape (top-level folders and what is directly under them) without burying the
# resource tables under a full recursive listing.
DEFAULT_DESCRIBE_TREE_DEPTH = 2

# Synthetic overlay of read-only system skill files spliced in at the pod root.
# It is not pod data and is identical in every pod, so it is noise here.
SYSTEM_SKILLS_ROOT_PATH = "/skills"

# Hard bound on name->id pod lookup. No CLI command should page an API an
# unbounded number of times; 5 x 200 covers any realistic org.
_MAX_POD_LOOKUP_PAGES = 5


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError, AttributeError, TypeError:
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
    # Page through pods so a name beyond the first page still resolves (and so
    # ambiguity is detected across the set, not just page one) — but BOUNDED. An
    # unbounded `while True` makes one CLI command's cost depend on how many pods
    # an org has; 5 pages of 200 covers a thousand, and past that a name lookup
    # is the wrong tool anyway, so we say so instead of paging forever.
    items: list[dict] = []
    page_token: str | None = None
    for _page in range(_MAX_POD_LOOKUP_PAGES):
        response = client.pods.list(org_id=org_id, limit=200, page_token=page_token)
        items.extend(list_items(response))
        page_token = str(to_plain(response).get("next_page_token") or "") or None
        if not page_token:
            break
    else:
        if page_token:
            fail(
                f"Too many pods to resolve '{selector}' by name (searched "
                f"{len(items)}). Pass the pod id instead — `lemma pods list` "
                "shows it."
            )
    needle = selector.casefold()
    matches = [
        item
        for item in items
        if str(item.get("id")) == selector
        or str(item.get("slug") or "").casefold() == needle
        or str(item.get("name") or "").casefold() == needle
    ]
    if not matches:
        names = (
            ", ".join(sorted(str(i.get("name")) for i in items if i.get("name")))
            or "(none)"
        )
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
    org: str | None = typer.Option(
        None, "--org", help="Organization to scope the pick."
    ),
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
        None,
        "--dir",
        help="Starter bundle directory (default: ./<name>). Implies --with-starter.",
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
        err_console.print(
            f"[green]starter[/green] scaffolded {len(scaffold.files)} files -> {target}"
        )

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
    pod_id = str(to_plain(result).get("id") or "")

    # A created pod is NOT the active pod, and `pods select` only affects the
    # current shell — which for anything scripted (every bash call a fresh
    # shell) means it does nothing at all. Without this, the next command writes
    # into whatever pod was previously active, possibly someone else's.
    if pod_id:
        err_console.print(
            f"[dim]this pod is not active yet — use[/dim] --pod {pod_id} "
            f"[dim]on later commands, or[/dim] export LEMMA_POD_ID={pod_id}"
        )

    if not want_starter or target is None:
        return
    from ...cli_app.pod_bundle import import_pod_bundle

    # Bind the scaffolded bundle to the new pod on the active server so later
    # `lemma` commands from that folder target it. Skipped under the env server.
    if pod_id and not state.server_read_only:
        from ..project_env import write_server_env

        values = {"LEMMA_POD_ID": pod_id}
        org_id = str(to_plain(result).get("organization_id") or "")
        if org_id:
            values["LEMMA_ORG_ID"] = org_id
        try:
            binding = write_server_env(target, state.server, values)
            err_console.print(
                f"[green]bound[/green] {target} -> pod on server "
                f"'{state.server}' ([dim]{binding}[/dim])"
            )
        except OSError:
            pass
    run_with_client(
        ctx,
        lambda client, s: import_pod_bundle(
            client, pod_id=pod_id, source_dir=target, upsert=True
        ),
    )
    err_console.print(f"[green]starter[/green] imported into pod {pod_id}")


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
    depth: int = typer.Option(
        DEFAULT_DESCRIBE_TREE_DEPTH,
        "--depth",
        min=1,
        help="Folder levels of the file tree to show. --full shows every level.",
    ),
    skills: bool = typer.Option(
        False,
        "--skills",
        help="Include the read-only /skills overlay in the file tree.",
    ),
) -> None:
    """Show a pod (by id or name) with all its resources."""
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        pod_id = resolve_pod_id(client, s, pod)
        pod_sdk = client.pod(pod_id)

        def optional(name: str):  # type: ignore[no-untyped-def]
            """List a resource whose endpoint an older server may not serve.

            describe is a survey, not a wiring check: one unavailable resource
            type should cost that section, not the whole command.
            """
            try:
                return list_items(getattr(pod_sdk, name).list(limit=limit))
            except Exception:  # noqa: BLE001 — a missing section, not a failure
                return []

        return {
            "pod": client.pods.get(pod_id),
            "tables": list_items(pod_sdk.tables.list(limit=limit)),
            "functions": list_items(pod_sdk.functions.list(limit=limit)),
            "agents": list_items(pod_sdk.agents.list(limit=limit)),
            "workflows": list_items(pod_sdk.workflows.list(limit=limit)),
            "schedules": list_items(pod_sdk.schedules.list(limit=limit)),
            "apps": optional("apps"),
            "surfaces": optional("surfaces"),
            "files": pod_sdk.files.tree("/"),
        }

    result = run_with_client(ctx, run)
    if result is None:
        return
    if state.output == "json":
        # JSON is a machine format: never elided.
        emit(state, result)
        return
    _render_pod_description(
        to_plain(result),
        # --full means "expand what was folded", which covers the tree too.
        tree_depth=None if state.full else depth,
        include_skills=skills or state.full,
    )


@app.command("doctor")
def doctor_pod(
    ctx: typer.Context,
    pod: str | None = typer.Argument(None),
    pod_option: str | None = typer.Option(
        None,
        "--pod",
        help="Pod id or name. Same as the positional; both skills present --pod "
        "as universal, and this was the one command that rejected it.",
    ),
) -> None:
    """Check a pod's wiring: grants pointing at missing tables, workflow/schedule
    targets that don't exist, and surfaces missing an agent or account."""

    target = pod_option or pod

    def run(client, s):  # type: ignore[no-untyped-def]
        from ...cli_app.pod_bundle import DESTRUCTIVE_PERMISSION_IDS

        pod_sdk = pod_client(client, s, target)
        tables = {
            str(t.get("name"))
            for t in to_plain(list_items(pod_sdk.tables.list(limit=1000)))
        }
        # `include=["permissions"]` attaches each row's grants, so the whole
        # check costs one request per resource type rather than one per resource.
        # This command used to make 19 requests on a two-agent pod.
        agent_items = to_plain(
            list_items(pod_sdk.agents.list(limit=1000, include=["permissions"]))
        )
        agents = {str(a.get("name")) for a in agent_items}
        function_items = to_plain(
            list_items(pod_sdk.functions.list(limit=1000, include=["permissions"]))
        )
        functions = {str(f.get("name")) for f in function_items}
        workflow_items = to_plain(list_items(pod_sdk.workflows.list(limit=1000)))
        workflows = {str(w.get("name")) for w in workflow_items}
        schedules = to_plain(list_items(pod_sdk.schedules.list(limit=1000)))

        errors: list[str] = []
        warnings: list[str] = []

        # Pod file tree, fetched once and shared by every folder-grant check.
        def _norm(path: Any) -> str:
            return "/" + "/".join(part for part in str(path or "").split("/") if part)

        folders: set[str] = set()
        documents: set[str] = set()

        def walk_tree(node: dict) -> None:
            for child in node.get("children") or []:
                if not isinstance(child, dict):
                    continue
                path = _norm(child.get("path"))
                if path == "/":
                    continue
                if str(child.get("kind") or "").upper() == "FOLDER":
                    folders.add(path)
                    walk_tree(child)
                else:
                    documents.add(path)

        try:
            tree = to_plain(pod_sdk.files.tree("/")).get("tree")
            if isinstance(tree, dict):
                walk_tree(tree)
        except Exception as exc:  # noqa: BLE001 — surface, don't hide, a failed check
            warnings.append(f"could not read the pod file tree: {exc}")

        indexing_cache: dict[str, bool | None] = {}

        def documents_under(prefix: str) -> list[str]:
            root = _norm(prefix).rstrip("/") + "/"
            return sorted(path for path in documents if path.startswith(root))

        def is_indexed(path: str) -> bool | None:
            """True/False, or None when the file's status can't be read."""
            if path not in indexing_cache:
                try:
                    meta = to_plain(pod_sdk.files.get(path))
                except Exception:  # noqa: BLE001 — unknown, not a finding
                    indexing_cache[path] = None
                else:
                    indexing_cache[path] = bool(meta.get("search_enabled"))
            return indexing_cache[path]

        def check_folder_grant(kind: str, name: str, path: str) -> None:
            """A folder grant used to produce an unconditional "verify it exists"
            warning, which is noise the moment the folder does exist — and taught
            people to stop reading doctor. Actually look."""
            singular = kind[:-1]
            target = _norm(path)
            if target != "/" and target not in folders and not documents_under(target):
                errors.append(
                    f"{singular} '{name}' is granted on folder '{path}' which does "
                    "not exist in this pod."
                )
                return
            found = documents_under(target)
            if not found:
                return
            # A granted knowledge folder whose documents are all unindexed answers
            # every question with nothing. Export/import used to produce exactly
            # this, and it is invisible without asking.
            statuses = [is_indexed(doc) for doc in found[:25]]
            known = [status for status in statuses if status is not None]
            if known and not any(known):
                warnings.append(
                    f"{singular} '{name}' is granted on folder '{path}', but none "
                    f"of its {len(known)} checked document(s) are indexed — "
                    "searches there return nothing. Re-upload them, or check "
                    "`lemma files stat <path>`."
                )

        account_cache: dict[str, bool] = {}

        def account_reachable(account_id: str) -> bool:
            if account_id not in account_cache:
                try:
                    client.connectors.accounts.get(account_id)
                except Exception:  # noqa: BLE001 — unreachable is the finding
                    account_cache[account_id] = False
                else:
                    account_cache[account_id] = True
            return account_cache[account_id]

        def check_grants(kind: str, name: str, embedded: list | None) -> list[dict]:
            # `embedded` is None only when the server didn't return grants for
            # this row (an older API); fall back to the per-resource call then,
            # so an upgrade isn't required to run doctor at all.
            if embedded is None:
                try:
                    embedded = (
                        to_plain(getattr(pod_sdk, kind).permissions(name)).get("grants")
                        or []
                    )
                except Exception as exc:  # noqa: BLE001 — surface, don't hide, a failed check
                    warnings.append(
                        f"could not read permissions for {kind[:-1]} '{name}': {exc}"
                    )
                    return []
            grants = [g for g in embedded if isinstance(g, dict)]
            # A workload with no grants isn't "fine by default" — it has zero
            # access and 403s the first time it touches anything. Say so here as
            # well as at import time, since a pod can also reach this state by a
            # create that dropped its permissions.
            if not grants:
                warnings.append(
                    f"{kind[:-1]} '{name}' holds NO grants — it cannot read any "
                    f"table, folder, or connector. Grant it with "
                    f"`lemma {kind} permissions add {name} <resource>:<perms>`."
                )
            for grant in grants:
                rtype = grant.get("resource_type")
                rname = str(grant.get("resource_name") or "")
                if rtype == "datastore_table" and rname not in tables:
                    errors.append(
                        f"{kind[:-1]} '{name}' is granted on table '{rname}' which does not exist."
                    )
                elif rtype == "agent" and rname not in agents:
                    errors.append(
                        f"{kind[:-1]} '{name}' is granted on agent '{rname}' which does not exist."
                    )
                elif rtype == "function" and rname not in functions:
                    errors.append(
                        f"{kind[:-1]} '{name}' is granted on function '{rname}' which does not exist."
                    )
                elif rtype == "connector_account" and not account_reachable(rname):
                    errors.append(
                        f"{kind[:-1]} '{name}' pins connector account '{rname}', "
                        "which this session cannot reach — the grant is dead. "
                        "Reconnect the account, or drop the grant to fall back to "
                        "the invoking user's own account."
                    )
                elif rtype in ("folder", "document"):
                    check_folder_grant(kind, name, rname)
                destructive = sorted(
                    p
                    for p in (grant.get("permission_ids") or [])
                    if p in DESTRUCTIVE_PERMISSION_IDS
                )
                if destructive:
                    warnings.append(
                        f"{kind[:-1]} '{name}' holds destructive permission(s) "
                        f"{', '.join(destructive)} on {rtype} '{rname}' — standing "
                        "authority, no approval prompt at runtime."
                    )
            return grants

        def agent_has_runtime(item: dict, name: str) -> bool:
            # `has_pinned_runtime` is on the list payload now; only an older API
            # (which omits it AND agent_runtime) costs a per-agent fetch.
            if item.get("has_pinned_runtime") is not None:
                return bool(item["has_pinned_runtime"])
            runtime = item.get("agent_runtime") or {}
            if runtime.get("profile_id"):
                return True
            try:
                detail = to_plain(pod_sdk.agents.get(name))
            except Exception:  # noqa: BLE001
                return False
            return bool((detail.get("agent_runtime") or {}).get("profile_id"))

        # agent name -> functions it may execute. Collected from the grants
        # check_grants already resolved, so the workflow check below costs
        # nothing extra.
        agent_executable_functions: dict[str, set[str]] = {}

        for item in agent_items:
            name = str(item.get("name"))
            grants = check_grants("agents", name, item.get("grants"))
            agent_executable_functions[name] = {
                str(g.get("resource_name"))
                for g in grants
                if g.get("resource_type") == "function"
                and "function.execute" in (g.get("permission_ids") or [])
            }
            if name and not agent_has_runtime(item, name):
                warnings.append(
                    f"agent '{name}' has no pinned runtime — relies on the backend default (system:lemma)."
                )
            toolsets = {
                str(t).upper()
                for t in (item.get("toolsets") or [])
                if isinstance(t, str)
            }
            if "SUBAGENTS" in toolsets and not any(
                g.get("resource_type") == "agent" for g in grants
            ):
                warnings.append(
                    f"agent '{name}' has the SUBAGENTS toolset but no agent grants — "
                    "self-spawn only; grant `agent:<other>:execute` to fan out."
                )
        for item in function_items:
            check_grants("functions", str(item.get("name")), item.get("grants"))

        for item in workflow_items:
            wname = str(item.get("name"))
            # `node_targets` is derived on the list response ("agent:x" /
            # "function:y"), which is all a wiring check needs. Fetching each
            # workflow's full graph made doctor cost one request per workflow.
            targets = item.get("node_targets")
            if targets is None:
                targets = [
                    f"{kind}:{cfg[key]}"
                    for node in (
                        to_plain(pod_sdk.workflows.get(wname)).get("nodes") or []
                    )
                    for cfg in [node.get("config") or {}]
                    for kind, key in (
                        ("agent", "agent_name"),
                        ("function", "function_name"),
                    )
                    if cfg.get(key)
                ]
            parsed = [str(entry).partition(":") for entry in targets]
            for kind, _, target_name in parsed:
                if kind == "agent" and target_name not in agents:
                    errors.append(
                        f"workflow '{wname}' targets missing agent '{target_name}'."
                    )
                if kind == "function" and target_name not in functions:
                    errors.append(
                        f"workflow '{wname}' targets missing function '{target_name}'."
                    )

            # An AGENT node runs its agent with that agent's full grant set — the
            # workflow orchestrates, it does not narrow what an agent may do. So
            # an agent that can execute a function the graph already runs at its
            # own node may do that work a second time, and the run ends with two
            # rows where the author expected one. Nothing catches that at
            # runtime, which is what makes it worth saying here.
            node_functions = {name for kind, _, name in parsed if kind == "function"}
            node_agents = {name for kind, _, name in parsed if kind == "agent"}
            for agent_name in sorted(node_agents):
                for fname in sorted(
                    agent_executable_functions.get(agent_name, set()) & node_functions
                ):
                    warnings.append(
                        f"workflow '{wname}' runs function '{fname}' at one node and "
                        f"agent '{agent_name}' at another, and '{agent_name}' also "
                        f"holds `function.execute` on '{fname}'. If the agent calls "
                        f"it, the run does that work twice — revoke the grant with "
                        f"`lemma agents permissions remove {agent_name} "
                        f"function:{fname}`, or give the node a narrower agent."
                    )

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
                errors.append(
                    f"surface '{plat}' points at missing agent '{agent_name}'."
                )
            if str(
                surf.get("credential_mode") or ""
            ).upper() == "CUSTOM" and not surf.get("account_id"):
                warnings.append(f"surface '{plat}' is CUSTOM but has no account_id.")

        return {
            "errors": errors,
            "warnings": warnings,
            "counts": {
                "tables": len(tables),
                "agents": len(agents),
                "functions": len(functions),
                "workflows": len(workflows),
                "schedules": len(schedules),
            },
        }

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
    output_dir: Path = typer.Argument(
        Path("."), help="Directory to write the bundle into."
    ),
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
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite the output directory."
    ),
    as_template: bool = typer.Option(
        False,
        "--as-template",
        help="Strip instance data (pinned agent runtimes, surface account ids) for a reusable starter.",
    ),
    data_table: list[str] = typer.Option(
        [],
        "--data-table",
        help="Export row data for this table (repeat for multiple). Tables are "
        "named one by one — there is no flag for every table, so rows leave the "
        "pod only where you asked for them.",
    ),
    folder: list[str] = typer.Option(
        [],
        "--folder",
        help="Export this folder and everything under it (repeat for multiple). "
        "Folders are named one by one — there is no flag for the whole file tree.",
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
            data_tables=set(data_table) or None,
            file_folders=list(folder) or None,
        ),
    )
    if result is None:
        return
    emit(state, result)
    if as_template:
        from ...cli_app.scaffold import templatize_bundle

        root, changed = templatize_bundle(output_dir)
        err_console.print(
            f"[green]template[/green] stripped instance data from {changed} file(s) in {root}"
        )


def _parse_import_variables(var: list[str], values: Path | None) -> dict[str, str]:
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


def _render_table(
    title: str, rows: list[dict[str, Any]], columns: list[tuple[str, str]]
) -> None:
    view = Table(title=f"{title} ({len(rows)})", box=box.SIMPLE_HEAVY)
    for heading, _key in columns:
        view.add_column(heading, overflow="fold")
    if rows:
        for row in rows:
            view.add_row(*(_short(row.get(key)) for _heading, key in columns))
    else:
        view.add_row(*([""] * len(columns)))
    console.print(view)


def _workflow_node_count(workflow: dict[str, Any]) -> str:
    # List responses carry a derived `node_count` and deliberately omit the
    # graph, so reading `nodes` here left the column blank on every row.
    count = workflow.get("node_count")
    if isinstance(count, int):
        return str(count)
    return _count(workflow.get("nodes"))


def _surface_agent(surface: dict[str, Any]) -> str:
    """A surface answers as its own agent, or as the pod's default."""
    name = surface.get("agent_name")
    if name:
        return str(name)
    return "(pod default)" if surface.get("uses_default_agent") else ""


def _render_pod_description(
    data: dict[str, Any],
    *,
    tree_depth: int | None = DEFAULT_DESCRIBE_TREE_DEPTH,
    include_skills: bool = False,
) -> None:
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
        [
            ("Name", "name"),
            ("Columns", "columns"),
            ("Primary Key", "primary_key_column"),
        ],
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
        {**item, "target": schedule_target(item)}
        for item in data.get("schedules", [])
        if isinstance(item, dict)
    ]
    _render_table(
        "Schedules",
        schedules,
        [
            ("ID", "id"),
            ("Type", "schedule_type"),
            ("Target", "target"),
            ("Active", "is_active"),
        ],
    )
    _render_table(
        "Apps",
        data.get("apps", []),
        [("Name", "name"), ("Status", "status"), ("URL", "url")],
    )
    surfaces = [
        {**item, "agent": _surface_agent(item)}
        for item in data.get("surfaces", [])
        if isinstance(item, dict)
    ]
    _render_table(
        "Surfaces",
        surfaces,
        [
            ("Name", "name"),
            ("Platform", "platform"),
            ("Agent", "agent"),
            ("Status", "status"),
        ],
    )
    _render_file_tree(
        data.get("files"),
        max_depth=tree_depth,
        include_skills=include_skills,
    )


def _render_file_tree(
    files_payload: Any,
    *,
    max_depth: int | None = DEFAULT_DESCRIBE_TREE_DEPTH,
    include_skills: bool = False,
) -> None:
    tree_payload = (
        files_payload.get("tree") if isinstance(files_payload, dict) else None
    )
    if not isinstance(tree_payload, dict):
        console.print(
            Panel("No file tree available.", title="Pod Files", box=box.ROUNDED)
        )
        return
    root = Tree("[bold]/[/bold]")
    _add_file_tree_children(
        root,
        tree_payload,
        max_depth=max_depth,
        include_skills=include_skills,
        depth=0,
    )
    title = "Pod Files"
    if max_depth is not None:
        title = f"Pod Files (depth {max_depth}; --full for everything)"
    console.print(Panel(root, title=title, box=box.ROUNDED))


def _add_file_tree_children(
    view: Tree,
    node: dict[str, Any],
    *,
    max_depth: int | None,
    include_skills: bool,
    depth: int,
) -> None:
    children = node.get("children") or []
    if not isinstance(children, list):
        return
    for child in children:
        if not isinstance(child, dict):
            continue
        path = str(child.get("path") or "")
        # /skills is a synthetic overlay of read-only system skill files, not
        # pod data — dozens of nodes that push the pod's own folders off screen.
        if not include_skills and path == SYSTEM_SKILLS_ROOT_PATH:
            continue
        name = str(child.get("name") or path)
        kind = str(child.get("kind") or "").upper()
        label = f"[cyan]{name}/[/cyan]" if kind == "FOLDER" else name
        if kind != "FOLDER":
            view.add(label)
            continue
        if max_depth is not None and depth + 1 >= max_depth:
            hidden = _descendant_count(child)
            if hidden:
                label = f"{label} [dim]({hidden} more)[/dim]"
            view.add(label)
            continue
        _add_file_tree_children(
            view.add(label),
            child,
            max_depth=max_depth,
            include_skills=include_skills,
            depth=depth + 1,
        )


def _descendant_count(node: dict[str, Any]) -> int:
    """How much of the tree a depth cut is hiding, so the elision is honest."""
    children = node.get("children") or []
    if not isinstance(children, list):
        return 0
    total = 0
    for child in children:
        if isinstance(child, dict):
            total += 1 + _descendant_count(child)
    return total
