"""A channel's lines -- reading, writing, editing, answering -- and where each
one went outside Lemma. Registered under ``lemma channels``."""

from __future__ import annotations

from typing import Any, Callable

import typer

from ..channel_view import render_lines
from ..confirm import confirm_destructive
from ..io import emit, to_plain
from ..payload import read_json
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx

messages_app = typer.Typer(help="A channel's lines: read, write, edit, answer.")
deliveries_app = typer.Typer(help="Where a line went outside Lemma, and retrying it.")


def _run(
    ctx: typer.Context,
    pod: str | None,
    call: Callable[[Any], Any],
    render: Callable[[Any, Any], None] = emit,
) -> None:
    """Call the pod's SDK and print what comes back."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, s: to_plain(call(pod_client(client, s, pod)))
    )
    if result is not None:
        render(state, result)


# -- lines ---------------------------------------------------------------------------


@messages_app.command("list")
def list_messages(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    limit: int = typer.Option(50, "--limit"),
    before_seq: int | None = typer.Option(
        None, "--before-seq", help="The page before this line (next_before_seq)."
    ),
    after_seq: int | None = typer.Option(
        None, "--after-seq", help="The page after this line (next_after_seq)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """List the timeline, oldest first; a thread's replies are counted on its line."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.messages(
            channel_id, limit=limit, before_seq=before_seq, after_seq=after_seq
        ),
        render_lines,
    )


@messages_app.command("thread")
def list_thread(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="The thread's first line."),
    limit: int = typer.Option(50, "--limit"),
    after_seq: int | None = typer.Option(
        None, "--after-seq", help="Replies after this line (next_after_seq)."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a thread: its first line and its replies, oldest first."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.thread(
            channel_id, message_id, limit=limit, after_seq=after_seq
        ),
        render_lines,
    )


@messages_app.command("post")
def post_message(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    text: str = typer.Argument(..., help="What to say."),
    members_only: bool = typer.Option(
        False, "--members-only", help="A note to members: never sent outside."
    ),
    source: str | None = typer.Option(
        None, "--source", help="Which outside place it goes to (a source id)."
    ),
    thread: str | None = typer.Option(
        None, "--thread", help="Reply in the thread of this line."
    ),
    agent: list[str] = typer.Option(
        [], "--mention-agent", help="An agent id to name; it answers. Repeat for more."
    ),
    member: list[str] = typer.Option(
        [], "--mention", help="A member's user id to name. Repeat for more."
    ),
    client_message_id: str | None = typer.Option(
        None,
        "--client-id",
        help="Your name for this line: sending it again is the same line.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Write in a channel: to the place it last heard from, unless a note."""
    mentions = [{"kind": "AGENT", "id": a} for a in agent] + [
        {"kind": "MEMBER", "id": m} for m in member
    ]
    _run(
        ctx,
        pod,
        lambda p: p.channels.post(
            channel_id,
            text,
            members_only=members_only,
            source_id=source,
            thread_root_id=thread,
            mentions=mentions,
            client_message_id=client_message_id,
        ),
    )


@messages_app.command("edit")
def edit_message(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="One of your own lines."),
    text: str = typer.Argument(..., help="What it says now."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Change what one of your own lines says."""
    _run(ctx, pod, lambda p: p.channels.edit_message(channel_id, message_id, text))


@messages_app.command("delete")
def delete_message(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="One of your own lines."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Delete one of your own lines; it keeps its place, empty."""
    confirm_destructive(f"Delete line {message_id}?", yes)
    _run(
        ctx,
        pod,
        lambda p: (
            p.channels.delete_message(channel_id, message_id) or {"deleted": True}
        ),
    )


@messages_app.command("answer")
def answer_question(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="The line asking you."),
    text: str | None = typer.Option(None, "--text", help="A written answer."),
    choice: str | None = typer.Option(None, "--choice", help="One of its choices."),
    decision: str | None = typer.Option(
        None, "--decision", help="APPROVE_ONCE or DENY, for an approval."
    ),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="The answer as JSON: a form's fields."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Answer what a line asks you."""
    answer: dict[str, Any] = read_json(json_payload, None) if json_payload else {}
    for key, value in (("text", text), ("choice", choice), ("decision", decision)):
        if value is not None:
            answer[key] = value
    if not answer:
        raise typer.BadParameter("Pass --text, --choice, --decision or --data.")
    _run(ctx, pod, lambda p: p.channels.answer(channel_id, message_id, answer))


# -- deliveries ----------------------------------------------------------------------


@deliveries_app.command("list")
def list_deliveries(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="The line."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """List where a line went outside Lemma, and what became of each send."""
    _run(ctx, pod, lambda p: p.channels.deliveries(channel_id, message_id))


@deliveries_app.command("retry")
def retry_delivery(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="The line."),
    delivery_id: str = typer.Argument(..., help="The failed send."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Try a failed or given-up send again, now."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.retry_delivery(channel_id, message_id, delivery_id),
    )
