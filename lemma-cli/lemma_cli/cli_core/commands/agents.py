from __future__ import annotations

from pathlib import Path

import typer
from lemma_sdk.openapi_client.models.create_agent_request import CreateAgentRequest
from lemma_sdk.openapi_client.models.update_agent_request import UpdateAgentRequest

from ..confirm import confirm_destructive
from ..io import emit, to_plain
from ..payload import build_request, read_json
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx
from ._grants import (
    apply_inline_permissions,
    change_live_grants,
    emit_grants,
    grants_from_options,
    replace_grants,
    report_inline_permissions,
    split_inline_permissions,
)
from .conversations import chat_once, interactive_chat, send_once

app = typer.Typer(help="Agent commands.")
permissions_app = typer.Typer(help="Agent resource permission commands.")
app.add_typer(permissions_app, name="permissions")


@app.command("init")
def init_agent(
    name: str = typer.Argument(..., help="Agent name (slug)."),
    root: Path | None = typer.Option(
        None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."
    ),
    runtime: str | None = typer.Option(
        None,
        "--runtime",
        help="Pin a runtime profile id (omit to use the system default). See `lemma runtime profiles list`.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Scaffold an agent bundle (JSON + instruction.md). Edit, then `lemma pods import`."""
    from ...cli_app.scaffold import ScaffoldError, init_resource, report

    try:
        result = init_resource("agent", name, root=root, force=force, runtime=runtime)
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report(
        result, next_hint="write instruction.md + grants, then `lemma pods import .`"
    )


@app.command("list")
def list_agents(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List agents in the pod."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).agents.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@app.command("get")
def get_agent(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show an agent."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, s: pod_client(client, s, pod).agents.get(agent)
    )
    if result is not None:
        emit(state, result)


@app.command("create")
def create_agent(
    ctx: typer.Context,
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create an agent from a JSON payload.

    Required: name, instruction. Optional: toolsets, visibility, agent_runtime,
    permissions.grants. Prefer `lemma agent init <name>`; run `lemma agent schema`
    (or `lemma schema agent`) for the full shape and valid enums.

    An agent starts with ZERO access. `permissions.grants` is applied in a
    follow-up call rather than inline, so the payload behaves identically for
    agents, functions, and `lemma pods import`, and so the applied grant count is
    reported instead of being invisible.
    """
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    body, permissions = split_inline_permissions(payload)

    def run(client, s):  # type: ignore[no-untyped-def]
        pod_sdk = pod_client(client, s, pod)
        created = pod_sdk.agents.create(
            build_request(CreateAgentRequest, body, context="agent")
        )
        name = str(to_plain(created).get("name") or body.get("name") or "")
        applied = apply_inline_permissions(pod_sdk, "agent", name, permissions)
        report_inline_permissions("agent", name, applied, state=state)
        return created

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


@app.command("update")
def update_agent(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update an agent from a JSON payload.

    A `permissions.grants` block REPLACES the agent's grants; omitting the key
    leaves them alone.
    """
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    body, permissions = split_inline_permissions(payload)

    def run(client, s):  # type: ignore[no-untyped-def]
        pod_sdk = pod_client(client, s, pod)
        updated = pod_sdk.agents.update(
            agent,
            build_request(UpdateAgentRequest, body, context=f"agent {agent}"),
        )
        applied = apply_inline_permissions(pod_sdk, "agent", agent, permissions)
        report_inline_permissions("agent", agent, applied, state=state)
        return updated

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


@permissions_app.command("get")
def get_agent_permissions(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show an agent's resource grants, with their permission ids."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).agents.permissions(agent),
    )
    if result is not None:
        emit_grants(state, "agent", agent, result)


@permissions_app.command("replace")
def replace_agent_permissions(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload (`-` reads stdin)."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    from_bundle: Path | None = typer.Option(
        None,
        "--from-bundle",
        exists=True,
        file_okay=False,
        readable=True,
        help="Bundle root or resource dir to lift permissions.grants from.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Replace resource permissions for an agent (the whole list, not a merge).

    Use `permissions add`/`remove` to change one grant. `--from-bundle <dir>`
    pushes the grants a bundle already declares for this agent.
    """
    grants = grants_from_options("agent", agent, json_payload, file, from_bundle)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: replace_grants(
            pod_client(client, s, pod), "agent", agent, grants
        ),
    )
    if result is not None:
        emit(state, result)


@permissions_app.command("add")
def add_agent_permissions(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    specs: list[str] = typer.Argument(
        ...,
        metavar="GRANT...",
        help="name:perms or type:name:perms, e.g. tickets:read,write /knowledge:read connector:gmail:use",
    ),
    pod: str | None = typer.Option(None, "--pod"),
    show: bool = typer.Option(
        False, "--print", help="Print the merged grant list instead of applying it."
    ),
) -> None:
    """Add grants to a LIVE agent, merging with the ones it already holds.

    The API only replaces the whole list, so this reads, merges, and writes back
    — no hand-editing JSON. Same spec grammar as `lemma agents grant`, which
    edits a bundle file instead.
    """
    change_live_grants(
        ctx, kind="agent", name=agent, specs=specs, pod=pod, show=show, remove=False
    )


@permissions_app.command("remove")
def remove_agent_permissions(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    specs: list[str] = typer.Argument(
        ..., metavar="GRANT...", help="Grants to drop, same syntax as `add`."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    show: bool = typer.Option(
        False, "--print", help="Print the resulting grant list instead of applying it."
    ),
) -> None:
    """Remove grants from a LIVE agent (a grant with no permissions left is dropped)."""
    change_live_grants(
        ctx, kind="agent", name=agent, specs=specs, pod=pod, show=show, remove=True
    )


@app.command("grant")
def grant_agent(
    name: str = typer.Argument(..., help="Agent name (matches the bundle folder)."),
    specs: list[str] = typer.Argument(
        ...,
        metavar="GRANT...",
        help="name:perms or type:name:perms, e.g. tickets:read,write /knowledge:read app:gmail:use",
    ),
    root: Path | None = typer.Option(
        None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."
    ),
    show: bool = typer.Option(
        False, "--print", help="Print grant JSON instead of editing the bundle file."
    ),
) -> None:
    """Add resource grants to an agent's bundle JSON (agents have zero access by default)."""
    from ._authoring import grant_resource

    grant_resource("agent", name, specs, root=root, show=show)


@app.command("schema")
def schema_agent(ctx: typer.Context) -> None:
    """Print the JSONC example/shape for an agent bundle file."""
    from ._authoring import print_resource_schema

    print_resource_schema(ctx, "agent")


@app.command("delete")
def delete_agent(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete an agent."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete agent {agent}?", yes)
    result = run_with_client(
        ctx, lambda client, s: pod_client(client, s, pod).agents.delete(agent)
    )
    if result is None:
        emit(state, {"ok": True})


@app.command("chat")
def chat_agent(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    message: str | None = typer.Argument(None),
    pod: str | None = typer.Option(None, "--pod"),
    conversation: str | None = typer.Option(None, "--conversation"),
    title: str | None = typer.Option(None, "--title"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show the agent's whole stream, not just its answer.",
    ),
) -> None:
    """Chat with an agent (interactive without a message)."""
    if message is None:
        interactive_chat(
            ctx, agent=agent, pod=pod, conversation=conversation, title=title
        )
        return
    chat_once(
        ctx,
        agent=agent,
        message=message,
        pod=pod,
        conversation=conversation,
        title=title,
        show_header=True,
        show_user_message=True,
        verbose=verbose,
    )


@app.command("run")
def run_agent(
    ctx: typer.Context,
    agent: str = typer.Argument(...),
    message: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help=(
            "Wait for the agent to finish and stream the result (default). "
            "With --no-wait, start the run and return its conversation id to "
            "follow with `lemma conversations stream/get`."
        ),
    ),
    conversation: str | None = typer.Option(None, "--conversation"),
    title: str | None = typer.Option(None, "--title"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show the agent's whole stream (reasoning, tool calls, usage), not just its answer.",
    ),
) -> None:
    """Run an agent with a message and print its ANSWER.

    Each run is a conversation; --no-wait returns the conversation id, which the
    `conversations` commands operate on. Pass --verbose to watch the run's
    interior, or --output json for {status, output, conversation_id}-shaped
    events you can parse.
    """
    if wait:
        chat_once(
            ctx,
            agent=agent,
            message=message,
            pod=pod,
            conversation=conversation,
            title=title,
            show_header=True,
            show_user_message=True,
            verbose=verbose,
        )
        return

    # Detached start: create (or reuse) the conversation, post the message,
    # and return its id (status "started") so the caller can follow it via the
    # `conversations` commands. send_once handles the client + output.
    send_once(
        ctx,
        agent=agent,
        message=message,
        pod=pod,
        conversation=conversation,
        title=title,
    )
