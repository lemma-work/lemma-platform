"""One grant vocabulary and one code path, whether the target is a bundle file
or a live pod.

Agents and functions both hold resource grants in the same
``{"grants": [{resource_type, resource_name, permission_ids}]}`` shape, and the
bundle carries that shape verbatim under a resource's ``permissions`` key. Three
callers need it and used to disagree:

* ``lemma pods import`` — splits ``permissions`` off the manifest and replaces
  the workload's grants after every referenced resource exists.
* ``lemma <agent|function> create/update`` — used to *advertise* inline
  ``permissions`` and silently drop it for functions, because the function
  create endpoint has no such field (agents do). Both now take the same path
  import does: create/update, then replace permissions.
* ``lemma <agent|function> permissions add/remove`` — read, merge or subtract,
  replace, since the API only offers whole-list replacement.

Everything here speaks `resource_name`, never `resource_id`: names survive an
export/import into another pod, ids don't.
"""

from __future__ import annotations

from typing import Any

from lemma_pod_bundle.normalize import _split_resource_permissions_payload

from ..io import to_plain
from ..payload import build_request
from ..state import console

# Grantee kinds and the pod-SDK resource that serves each. Both expose the same
# `permissions(name)` / `replace_permissions(name, request)` pair.
GRANTEE_KINDS = ("agent", "function")


def split_inline_permissions(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Split a resource payload into (resource fields, permissions or None).

    ``None`` means the payload carried no ``permissions`` key at all — the
    caller must leave existing grants alone. An empty ``{"grants": []}`` is a
    different statement: replace with nothing. That distinction is the bundle's
    documented semantics, so create/update honors it identically.
    """
    return _split_resource_permissions_payload(payload)


def _resource_api(pod_sdk: Any, kind: str) -> Any:
    if kind not in GRANTEE_KINDS:
        raise ValueError(f"Unknown grantee kind: {kind}")
    return pod_sdk.agents if kind == "agent" else pod_sdk.functions


def _replace_request_model(kind: str) -> Any:
    if kind == "agent":
        from lemma_sdk.openapi_client.models.agent_permissions_replace_request import (
            AgentPermissionsReplaceRequest,
        )

        return AgentPermissionsReplaceRequest
    from lemma_sdk.openapi_client.models.function_permissions_replace_request import (
        FunctionPermissionsReplaceRequest,
    )

    return FunctionPermissionsReplaceRequest


def list_grants(pod_sdk: Any, kind: str, name: str) -> list[dict[str, Any]]:
    """The workload's current grants, normalized to plain dicts."""
    payload = to_plain(_resource_api(pod_sdk, kind).permissions(name))
    return [
        grant for grant in (payload.get("grants") or []) if isinstance(grant, dict)
    ]


def replace_grants(
    pod_sdk: Any, kind: str, name: str, grants: list[dict[str, Any]]
) -> Any:
    """Replace the workload's whole grant list (the only write the API offers)."""
    return _resource_api(pod_sdk, kind).replace_permissions(
        name,
        build_request(
            _replace_request_model(kind),
            {"grants": grants},
            context=f"{kind} {name} permissions",
        ),
    )


def apply_inline_permissions(
    pod_sdk: Any, kind: str, name: str, permissions: dict[str, Any] | None
) -> int | None:
    """Push a payload's inline ``permissions`` block, if it carried one.

    Returns the number of grants applied, or None when the payload said nothing
    about permissions (existing grants untouched).
    """
    if permissions is None:
        return None
    grants = [
        grant for grant in (permissions.get("grants") or []) if isinstance(grant, dict)
    ]
    replace_grants(pod_sdk, kind, name, grants)
    return len(grants)


def report_inline_permissions(
    kind: str, name: str, applied: int | None, *, state: Any = None
) -> None:
    """Say what happened to the grants, so an inline ``permissions`` block is
    never a silent no-op the way it used to be for functions.

    Skipped under ``--json``: the command's payload is the whole stdout there,
    and a status line would make it unparseable.
    """
    if applied is None or getattr(state, "output", None) == "json":
        return
    if applied:
        console.print(
            f"[green]permissions[/green] {kind} [bold]{name}[/bold]: "
            f"replaced with {applied} grant(s)"
        )
    else:
        console.print(
            f"[yellow]permissions[/yellow] {kind} [bold]{name}[/bold]: "
            "replaced with an EMPTY grant list — it can no longer reach any "
            "table, folder, or connector."
        )


def render_grants(kind: str, name: str, grants: list[dict[str, Any]]) -> None:
    """Print a grant list the same way everywhere (bundle edit, add, remove)."""
    if not grants:
        console.print(
            f"  [yellow]none[/yellow] — {kind} '{name}' has zero access to pod "
            "resources."
        )
        return
    for grant in grants:
        permission_ids = ", ".join(grant.get("permission_ids") or [])
        console.print(
            f"  {grant.get('resource_type')} {grant.get('resource_name')}: "
            f"{permission_ids}"
        )


def parse_specs(specs: list[str]) -> list[dict[str, Any]]:
    """Parse `name:perms` / `type:name:perms` specs — the same grammar the
    bundle-editing `lemma <agent|function> grant` command uses."""
    import typer

    from ...cli_app.scaffold import ScaffoldError, parse_grant_spec

    try:
        return [parse_grant_spec(spec) for spec in specs]
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc


def grants_from_options(
    kind: str,
    name: str,
    json_payload: str | None,
    file: Any,
    from_bundle: Any,
) -> list[dict[str, Any]]:
    """Resolve `permissions replace`'s input to a grant list.

    Accepts the raw `{"grants": [...]}` payload via --data/--file, or lifts the
    block straight out of a bundle with --from-bundle — the manual
    "open the JSON, copy permissions.grants, paste into --data" round trip that
    every 403-chasing session used to end in.
    """
    import typer

    from ...cli_app.scaffold import find_bundle_root

    sources = [option for option in (json_payload, file, from_bundle) if option]
    if len(sources) > 1:
        raise typer.BadParameter(
            "Use only one of --data, --file, or --from-bundle."
        )
    if from_bundle is not None:
        from pathlib import Path

        from lemma_pod_bundle.layout import load_resource_payload

        folder = "agents" if kind == "agent" else "functions"
        candidates = [
            Path(from_bundle) / folder / name,  # bundle root
            Path(from_bundle),  # the resource dir itself
        ]
        root = find_bundle_root(Path(from_bundle))
        if root is not None:
            candidates.insert(0, root / folder / name)
        for resource_dir in candidates:
            if (resource_dir / f"{name}.json").is_file():
                _body, permissions = split_inline_permissions(
                    load_resource_payload(resource_dir, name, resource_type=folder)
                )
                if permissions is None:
                    raise typer.BadParameter(
                        f"{resource_dir / f'{name}.json'} declares no "
                        "'permissions' block, so there are no grants to push."
                    )
                return [
                    grant
                    for grant in (permissions.get("grants") or [])
                    if isinstance(grant, dict)
                ]
        raise typer.BadParameter(
            f"Could not find {folder}/{name}/{name}.json under {from_bundle}."
        )

    from ..payload import read_json

    payload = read_json(json_payload, file, required=True)
    grants = payload.get("grants")
    if grants is None:
        raise typer.BadParameter(
            "Payload must be an object with a 'grants' list, e.g. "
            '{"grants": [{"resource_type": "datastore_table", "resource_name": '
            '"tickets", "permission_ids": ["datastore.table.read"]}]}.'
        )
    if not isinstance(grants, list):
        raise typer.BadParameter("'grants' must be a list.")
    return [grant for grant in grants if isinstance(grant, dict)]


def change_live_grants(
    ctx: Any,
    *,
    kind: str,
    name: str,
    specs: list[str],
    pod: str | None,
    show: bool,
    remove: bool,
) -> None:
    """Shared body of `permissions add` / `permissions remove` for both kinds.

    The API exposes replacement only, so an additive change is read → merge →
    replace. Doing that here (rather than in every caller's head) is the whole
    point: hand-merging a grant list is where grants get dropped.
    """
    from ...cli_app.scaffold import merge_grants, subtract_grants
    from ..sdk import pod_client
    from ..state import run_with_client, state_from_ctx

    parsed = parse_specs(specs)
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        pod_sdk = pod_client(client, s, pod)
        before = list_grants(pod_sdk, kind, name)
        after = (
            subtract_grants(before, parsed) if remove else merge_grants(before, parsed)
        )
        if show:
            return {"grants": after, "applied": False}
        if after == before:
            console.print(
                f"[dim]no change[/dim] {kind} [bold]{name}[/bold] already matches "
                "the requested grants."
            )
            return {"grants": after, "applied": False}
        replace_grants(pod_sdk, kind, name, after)
        return {"grants": after, "applied": True}

    result = run_with_client(ctx, run)
    if result is None:
        return
    if state.output == "json":
        from ..io import emit

        emit(state, {"grants": result["grants"]})
        return
    if show:
        import json

        console.print_json(json.dumps({"grants": result["grants"]}))
        return
    if result["applied"]:
        verb = "removed from" if remove else "added to"
        console.print(
            f"[green]permissions[/green] {verb} {kind} [bold]{name}[/bold] — now:"
        )
    render_grants(kind, name, result["grants"])
