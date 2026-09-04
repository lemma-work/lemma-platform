from __future__ import annotations

from typing import Any

import typer

from ..chat import (
    emit_stream_events,
    iter_sse_events,
    read_chat_prompt,
    render_chat_header,
    render_chat_help,
    render_chat_stream,
    render_user_message,
)
from lemma_sdk.errors import LemmaAPIError
from lemma_sdk.openapi_client.models.resolve_user_approval_request import (
    ResolveUserApprovalRequest,
)

from ..confirm import confirm_destructive
from ..context import remember_conversation, selected_conversation, selected_pod
from ..io import emit, list_items, render_transcript, to_plain
from ..payload import build_request
from ..sdk import pod_client
from ..select import select_from_items
from ..state import (
    CliState,
    console,
    err_console,
    fail,
    run_with_client,
    state_from_ctx,
)

app = typer.Typer(help="Agent conversation commands.")


def _resolve_conversation_arg(state: CliState, explicit: str | None) -> str:
    """A conversation id from the argument, else the active conversation
    (``LEMMA_CONVERSATION_ID`` — set by the workspace in agent sandboxes), so an
    agent can act on its current conversation with no id."""
    value = explicit or selected_conversation(state, required=False)
    if not value:
        fail(
            "No conversation. Pass the id or set LEMMA_CONVERSATION_ID "
            "(set automatically inside an agent sandbox)."
        )
    return str(value)


def _emit_stream(state: CliState, response: Any) -> None:
    emit_stream_events(state, response)


def _ensure_conversation(
    ctx: typer.Context,
    *,
    pod: str | None,
    agent: str | None,
    conversation: str | None,
    title: str | None,
) -> str | None:
    if conversation:
        return conversation

    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).conversations.create_for_agent(
            agent or "", title=title
        ),
    )
    payload = to_plain(result)
    if isinstance(payload, dict) and payload.get("id"):
        return str(payload["id"])
    return None


def chat_once(
    ctx: typer.Context,
    *,
    agent: str | None,
    message: str,
    pod: str | None,
    conversation: str | None,
    title: str | None,
    show_header: bool = False,
    show_user_message: bool = False,
    verbose: bool = False,
) -> None:
    state = state_from_ctx(ctx)
    pod_id = selected_pod(state, pod) or ""
    conversation_id = _ensure_conversation(
        ctx,
        pod=pod,
        agent=agent,
        conversation=conversation,
        title=title,
    )
    if conversation_id is None:
        return
    if show_header and state.output != "json":
        render_chat_header(
            pod=pod_id,
            agent=agent,
            conversation_id=conversation_id,
            interactive=False,
        )
    if show_user_message and state.output != "json":
        render_user_message(message)

    def stream(client, s):  # type: ignore[no-untyped-def]
        # Render inside the client session: the SSE response is only readable
        # while the underlying httpx client is open.
        response = pod_client(client, s, pod).conversations.send_stream(
            conversation_id, message
        )
        render_chat_stream(state=s, response=response, agent=agent, verbose=verbose)
        return True

    run_with_client(ctx, stream)
    # Surface the conversation id on a one-shot chat so it's discoverable and
    # resumable (interactive mode shows it in the header / `/id` instead).
    if show_header and state.output != "json":
        console.print(
            f"[dim]conversation {conversation_id} · resume: "
            f'lemma conversation send {conversation_id} "…"[/dim]'
        )


def send_once(
    ctx: typer.Context,
    *,
    agent: str | None,
    message: str,
    pod: str | None,
    conversation: str | None,
    title: str | None,
) -> None:
    state = state_from_ctx(ctx)
    pod_id = selected_pod(state, pod) or ""
    conversation_id = _ensure_conversation(
        ctx,
        pod=pod,
        agent=agent,
        conversation=conversation,
        title=title,
    )
    if conversation_id is None:
        return

    def send_and_peek(client, s):  # type: ignore[no-untyped-def]
        # Read the first event inside the client session: the SSE response is
        # only readable while the underlying httpx client is open.
        response = pod_client(client, s, pod).conversations.send_stream(
            conversation_id, message
        )
        try:
            return next(iter(iter_sse_events(response)), None)
        finally:
            response.close()

    first_event = run_with_client(ctx, send_and_peek)
    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "pod_id": pod_id,
        "status": "started",
    }
    if first_event is not None:
        payload["agent_run_id"] = first_event.agent_run_id
        payload["first_event"] = {
            "type": first_event.type,
            "data": first_event.data,
        }
    emit(state, payload)


def interactive_chat(
    ctx: typer.Context,
    *,
    agent: str | None,
    pod: str | None,
    conversation: str | None,
    title: str | None,
) -> None:
    state = state_from_ctx(ctx)
    pod_id = selected_pod(state, pod) or ""
    conversation_id = _ensure_conversation(
        ctx,
        pod=pod,
        agent=agent,
        conversation=conversation,
        title=title,
    )
    if conversation_id is None:
        return
    if state.output != "json":
        render_chat_header(
            pod=pod_id,
            agent=agent,
            conversation_id=conversation_id,
            interactive=True,
        )
    while True:
        try:
            message = read_chat_prompt()
        except EOFError, KeyboardInterrupt:
            console.print()
            return
        command = message.strip()
        if not command:
            continue
        if command in {"/q", "/quit", "/exit", "exit"}:
            return
        if command == "/help":
            render_chat_help()
            continue
        if command == "/id":
            console.print(conversation_id)
            continue
        if command == "/clear":
            console.clear()
            render_chat_header(
                pod=pod_id,
                agent=agent,
                conversation_id=conversation_id,
                interactive=True,
            )
            continue
        if command == "/stop":
            result = run_with_client(
                ctx,
                lambda client, s: pod_client(client, s, pod).conversations.stop(
                    conversation_id
                ),
            )
            if result is not None:
                console.print("[yellow]Stop requested.[/yellow]")
            continue
        render_user_message(message)
        chat_once(
            ctx,
            agent=agent,
            message=message,
            pod=pod,
            conversation=conversation_id,
            title=title,
            show_header=False,
            show_user_message=False,
        )


@app.command("list")
def list_conversations(
    ctx: typer.Context,
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Agent name. Omit to list default pod assistant conversations.",
    ),
    parent_id: str | None = typer.Option(
        None,
        "--parent-id",
        help=(
            "List children of this conversation (a PROJECT's pinned conversations "
            "or an agent's sub-agents). Omit for root conversations only."
        ),
    ),
    conversation_type: str | None = typer.Option(
        None, "--type", help="Filter by type: CHAT, TASK, or PROJECT."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List conversations (root by default; --parent-id for children)."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).conversations.list(
            agent_name=agent,
            parent_id=parent_id,
            type=conversation_type,
            limit=limit,
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("select")
def select_conversation(
    ctx: typer.Context,
    agent: str | None = typer.Option(None, "--agent"),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Select the default conversation for the active server."""
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        items = list_items(
            pod_client(client, s, pod).conversations.list(agent_name=agent, limit=limit)
        )
        selected = select_from_items(
            items,
            label="conversation",
            current_id=selected_conversation(s, required=False),
        )
        remember_conversation(s, str(selected.get("id")))
        return {"selected_conversation": selected}

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


@app.command("get")
def get_conversation(
    ctx: typer.Context,
    conversation: str | None = typer.Argument(
        None, help="Conversation id (default: $LEMMA_CONVERSATION_ID)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a conversation."""
    state = state_from_ctx(ctx)
    conversation_id = _resolve_conversation_arg(state, conversation)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).conversations.get(conversation_id),
    )
    if result is not None:
        emit(state, result)


@app.command("messages")
def messages(
    ctx: typer.Context,
    conversation: str | None = typer.Argument(
        None, help="Conversation id (default: $LEMMA_CONVERSATION_ID)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List messages in a conversation as a transcript."""
    state = state_from_ctx(ctx)
    conversation_id = _resolve_conversation_arg(state, conversation)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).conversations.messages(
            conversation_id, limit=limit
        ),
    )
    if result is not None:
        render_transcript(state, result)


@app.command("send")
def send(
    ctx: typer.Context,
    conversation: str = typer.Argument(...),
    message: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Send a message to a conversation and stream the reply."""
    chat_once(
        ctx, agent=None, message=message, pod=pod, conversation=conversation, title=None
    )


@app.command("stream")
def stream(
    ctx: typer.Context,
    conversation: str | None = typer.Argument(
        None, help="Conversation id (default: $LEMMA_CONVERSATION_ID)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    agent_run_id: str | None = typer.Option(None, "--agent-run-id"),
) -> None:
    """Reattach to the active run of a conversation."""
    state = state_from_ctx(ctx)
    conversation_id = _resolve_conversation_arg(state, conversation)

    def reattach(client, s):  # type: ignore[no-untyped-def]
        # Render inside the client session: the SSE response is only readable
        # while the underlying httpx client is open.
        response = pod_client(client, s, pod).conversations.stream(
            conversation_id, agent_run_id=agent_run_id
        )
        render_chat_stream(state=s, response=response, agent=None)
        return True

    run_with_client(ctx, reattach)


@app.command("stop")
def stop(
    ctx: typer.Context,
    conversation: str | None = typer.Argument(
        None, help="Conversation id (default: $LEMMA_CONVERSATION_ID)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Stop the active run of a conversation."""
    state = state_from_ctx(ctx)
    conversation_id = _resolve_conversation_arg(state, conversation)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).conversations.stop(
            conversation_id
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("approvals")
def list_approvals(
    ctx: typer.Context,
    conversation: str | None = typer.Argument(
        None, help="Conversation id (default: $LEMMA_CONVERSATION_ID)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """List pending approval requests (gated tool calls / ask_user) awaiting a decision."""
    state = state_from_ctx(ctx)
    conversation_id = _resolve_conversation_arg(state, conversation)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).conversations.approvals(
            conversation_id
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("approve")
def approve(
    ctx: typer.Context,
    approval: str | None = typer.Argument(
        None,
        help="Approval id (from `conversation approvals`). Omit to act on ALL pending.",
    ),
    conversation: str | None = typer.Option(
        None,
        "--conversation",
        "-c",
        help="Conversation id (default: $LEMMA_CONVERSATION_ID).",
    ),
    deny: bool = typer.Option(False, "--deny", help="Reject instead of approving."),
    session: bool = typer.Option(
        False, "--session", help="Approve for the whole session (not just once)."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation when resolving all pending."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Resolve a pending approval so a gated agent action proceeds from the CLI.

    Approves once by default (`--session` to approve for the run, `--deny` to
    reject). With no approval id, lists every pending approval in the
    conversation and confirms before resolving them all — handy for driving a
    file-using agent or an AGENT-node workflow to completion without the
    frontend.
    """
    state = state_from_ctx(ctx)
    conversation_id = _resolve_conversation_arg(state, conversation)
    decision = (
        "DENY" if deny else ("APPROVE_FOR_SESSION" if session else "APPROVE_ONCE")
    )

    def run(client, s):  # type: ignore[no-untyped-def]
        pc = pod_client(client, s, pod)
        if approval:
            ids = [approval]
        else:
            # A bare `lemma conversation approve` authorises every gated call
            # queued on the conversation — the ones gated precisely because they
            # send mail, write files or run connector operations. That is
            # irreversible and wider than most deletes, so it shows what it is
            # about to do and goes through the same confirmation every delete
            # does (CONVENTIONS.md, "Destructive operations").
            pending = [
                item
                for item in list_items(pc.conversations.approvals(conversation_id))
                if item.get("id")
            ]
            if not pending:
                return {
                    "conversation_id": conversation_id,
                    "decision": decision,
                    "resolved": [],
                    "failed": [],
                }
            if not yes:
                _render_pending_approvals(pending, decision)
            confirm_destructive(
                f"{decision} all {len(pending)} pending approval(s) "
                f"on conversation {conversation_id}?",
                yes,
            )
            ids = [str(item["id"]) for item in pending]

        resolved: list[str] = []
        failed: list[dict[str, str]] = []
        for approval_id in ids:
            try:
                pc.conversations.resolve_approval(
                    conversation_id,
                    approval_id,
                    build_request(
                        ResolveUserApprovalRequest,
                        {"decision": decision},
                        context="approval",
                    ),
                )
            except LemmaAPIError as exc:
                # Report per approval rather than aborting: without this the
                # command failed on the third of five and never said which two
                # had already gone through.
                failed.append({"id": approval_id, "error": str(exc)})
            else:
                resolved.append(approval_id)
        return {
            "conversation_id": conversation_id,
            "decision": decision,
            "resolved": resolved,
            "failed": failed,
        }

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)
        if result.get("failed"):
            raise typer.Exit(code=1)


def _render_pending_approvals(pending: list[dict[str, Any]], decision: str) -> None:
    """List what a bulk approve is about to authorise, on stderr.

    Diagnostics, not the command's result: the resolved ids are what `emit`
    prints, and `--json` must stay parseable.
    """
    err_console.print(f"[bold]{len(pending)} pending approval(s)[/bold] to {decision}:")
    for item in pending:
        name = item.get("tool_name") or item.get("kind") or item.get("type") or "?"
        detail = item.get("tool_args") or item.get("arguments") or item.get("message")
        # One line per approval: the point is to recognise them, and a tool call
        # with a large argument payload would otherwise bury the list it is in.
        summary = str(detail)[:120] if detail else ""
        suffix = f" {summary}" if summary else ""
        err_console.print(f"  [dim]{item['id']}[/dim] {name}{suffix}", highlight=False)
