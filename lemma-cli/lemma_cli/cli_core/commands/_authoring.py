"""Shared command bodies for the init-first authoring flow.

`grant` (edit a bundle's permissions.grants) and `schema` (print a resource's
JSONC shape) are identical across resource modules; the logic lives here so the
per-resource commands stay thin 3-line wrappers and the output stays uniform.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..io import emit
from ..state import console, state_from_ctx


def grant_resource(
    resource_type: str,
    name: str,
    specs: list[str],
    *,
    root: Path | None,
    show: bool,
) -> None:
    """Add resource grants to an agent/function bundle JSON (zero access by default)."""
    from ...cli_app.scaffold import ScaffoldError, grant_in_bundle, parse_grant_spec

    try:
        if show:
            grants = [parse_grant_spec(spec) for spec in specs]
            console.print_json(json.dumps({"permissions": {"grants": grants}}))
            return
        path, perms = grant_in_bundle(resource_type, name, specs, root=root)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]grant[/green] {resource_type} [bold]{name}[/bold] -> {path}")
    for grant in perms["grants"]:
        console.print(
            f"  {grant['resource_type']} {grant['resource_name']}: {', '.join(grant['permission_ids'])}"
        )
    console.print("[dim]next:[/dim] `lemma pods import .` to apply")


def print_resource_schema(ctx: typer.Context, resource_type: str) -> None:
    """Print the JSONC scaffold/example for a resource type (same shape `init` writes).

    The scaffold is JSON *with comments* — that is the point of it, and it is
    what `init` writes to a file. But it is not parseable JSON, so under
    `--output json` this was handing a `| jq` a syntax error on every one of
    the resource types. In that mode the template travels as a string value
    instead, which keeps the comments and satisfies the promise.

    The context is passed rather than fetched: typer vendors its own click, so
    a top-level `click.get_current_context()` looks at a different context
    stack and answers `None` — the same vendoring that makes a `TyperGroup`
    fail an `isinstance(cmd, click.Group)` check.
    """
    from ...cli_app.scaffold import ScaffoldError, resource_example

    try:
        example = resource_example(resource_type)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc

    state = state_from_ctx(ctx)
    if state.output == "json":
        emit(state, {"resource_type": resource_type, "template": example})
        return
    typer.echo(example)
