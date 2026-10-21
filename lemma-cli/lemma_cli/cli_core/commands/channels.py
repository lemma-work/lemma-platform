from __future__ import annotations

from typing import Any, Callable

import typer

from ..channel_view import render_inbox, render_search
from ..confirm import confirm_destructive
from ..io import emit, to_plain
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx
from .channel_lines import deliveries_app, messages_app

app = typer.Typer(
    help="Channel commands: where a pod's members, contacts and agents talk."
)
members_app = typer.Typer(help="Who is in a group: people and agents.")
source_app = typer.Typer(
    help="A group source's settings, and bringing the bot into a group."
)
app.add_typer(members_app, name="members")
app.add_typer(members_app, name="member", hidden=True)
app.add_typer(messages_app, name="messages")
app.add_typer(messages_app, name="message", hidden=True)
app.add_typer(deliveries_app, name="deliveries")
app.add_typer(source_app, name="source")
app.add_typer(source_app, name="sources", hidden=True)

_KINDS = ("DIRECT", "CONTACT", "GROUP")
_MEMBER_KINDS = ("MEMBER", "CONTACT", "AGENT")
_NOTIFY = ("ALL", "MENTIONS", "NONE")
_ANSWERS = ("MENTIONED", "EVERY_LINE")
_ROLES = ("owner", "member")
_STATUSES = ("open", "waiting_on_contact", "closed")
_VISIBILITIES = ("PUBLIC", "PRIVATE")


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


def _pick(value: str, allowed: tuple[str, ...], flag: str) -> str:
    for option in allowed:
        if value.lower() == option.lower():
            return option
    raise typer.BadParameter(f"{flag} is one of {', '.join(allowed)}.")


def _choice(value: str | None, allowed: tuple[str, ...], flag: str) -> str | None:
    return None if value is None else _pick(value, allowed, flag)


def _member_ref(value: str) -> tuple[str, str]:
    """`KIND:ID` -- `MEMBER:<user id>`, `CONTACT:<contact id>`, `AGENT:<agent id>`."""
    kind, sep, member_id = value.partition(":")
    if not sep or not member_id:
        raise typer.BadParameter(
            "MEMBER is KIND:ID -- MEMBER:<user id>, CONTACT:<contact id> or AGENT:<agent id>."
        )
    return _pick(kind, _MEMBER_KINDS, "KIND"), member_id


def _agent_seat(value: str) -> dict[str, str]:
    """`ID` (answers when named) or `ID:EVERY_LINE`."""
    agent_id, sep, answers = value.partition(":")
    if not sep:
        return {"id": agent_id, "answers": "MENTIONED"}
    return {"id": agent_id, "answers": _pick(answers, _ANSWERS, "--agent ID:ANSWERS")}


# -- channels ----------------------------------------------------------------------


@app.command("list")
def list_channels(
    ctx: typer.Context,
    kind: str | None = typer.Option(None, "--kind", help="DIRECT, CONTACT or GROUP."),
    archived: bool = typer.Option(False, "--archived", help="Archived ones instead."),
    contact: str | None = typer.Option(None, "--contact", help="Only this contact's."),
    assignee: str | None = typer.Option(
        None, "--assignee", help="Contacts' channels assigned to `me` or a user id."
    ),
    unassigned: bool = typer.Option(
        False, "--unassigned", help="Contacts' channels nobody is assigned."
    ),
    status: str | None = typer.Option(
        None, "--status", help="open, waiting_on_contact or closed."
    ),
    joined: bool = typer.Option(False, "--joined", help="Only channels you are in."),
    limit: int = typer.Option(50, "--limit"),
    cursor: str | None = typer.Option(
        None, "--cursor", help="The previous page's next_cursor."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """List the channels you can see, most recently active first."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.list(
            kind=_choice(kind, _KINDS, "--kind"),
            archived=archived,
            contact_id=contact,
            assignee=assignee,
            unassigned=unassigned,
            status=_choice(status, _STATUSES, "--status"),
            joined=joined,
            limit=limit,
            cursor=cursor,
        ),
    )


@app.command("get")
def get_channel(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show one channel: its sources, members, agents and your settings."""
    _run(ctx, pod, lambda p: p.channels.get(channel_id))


@app.command("mine")
def my_channel(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show your own channel with the pod, making it if it is not there yet."""
    _run(ctx, pod, lambda p: p.channels.open_mine())


@app.command("create")
def create_channel(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="What to call it."),
    topic: str | None = typer.Option(None, "--topic"),
    private: bool = typer.Option(
        False, "--private", help="Only its members see it (public by default)."
    ),
    member: list[str] = typer.Option(
        [], "--member", help="A member's user id to add. Repeat for more."
    ),
    agent: list[str] = typer.Option(
        [],
        "--agent",
        help="An agent id to seat (answers when named), or ID:EVERY_LINE. Repeat for more.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Make a group in Lemma, with you as its owner."""
    seats = [_agent_seat(value) for value in agent]
    _run(
        ctx,
        pod,
        lambda p: p.channels.create(
            title,
            topic=topic,
            visibility="PRIVATE" if private else "PUBLIC",
            members=member,
            agents=seats,
        ),
    )


@app.command("update")
def update_channel(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    title: str | None = typer.Option(None, "--title"),
    topic: str | None = typer.Option(None, "--topic"),
    clear_topic: bool = typer.Option(False, "--clear-topic"),
    visibility: str | None = typer.Option(
        None, "--visibility", help="public or private."
    ),
    archived: bool | None = typer.Option(
        None, "--archive/--unarchive", help="Archive the group, or bring it back."
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help="A contact's channel: open, waiting_on_contact or closed.",
    ),
    assignee: str | None = typer.Option(
        None,
        "--assignee",
        help="A contact's channel: `me`, a member's user id, or `none`.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update a channel: only the fields given change."""
    changes: dict[str, Any] = {}
    if title is not None:
        changes["title"] = title
    if clear_topic:
        changes["topic"] = None
    elif topic is not None:
        changes["topic"] = topic
    if visibility is not None:
        changes["visibility"] = _choice(visibility, _VISIBILITIES, "--visibility")
    if archived is not None:
        changes["archived"] = archived
    if status is not None:
        changes["status"] = _choice(status, _STATUSES, "--status")
    if assignee is not None:
        changes["assignee"] = None if assignee.lower() == "none" else assignee
    if not changes:
        raise typer.BadParameter("Name something to change.")
    _run(ctx, pod, lambda p: p.channels.update(channel_id, **changes))


@app.command("delete")
def delete_channel(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Delete a group made in Lemma and its timeline (one an outside place feeds is archived)."""
    confirm_destructive(f"Delete channel {channel_id}?", yes)
    _run(ctx, pod, lambda p: p.channels.delete(channel_id) or {"deleted": True})


@app.command("agent")
def switch_agent(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="A contact's channel id."),
    state: str = typer.Argument(..., help="on or off."),
    hand_back_after: int | None = typer.Option(
        None,
        "--hand-back-after",
        help="Hours a member who wrote to them holds them before the agent answers again.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Switch whether a contact's agent answers them (on hands them back at once)."""
    on = {"on": True, "off": False}.get(state.lower())
    if on is None:
        raise typer.BadParameter("STATE is on or off.")
    extra: dict[str, Any] = {}
    if hand_back_after is not None:
        extra["hand_back_after_hours"] = hand_back_after
    _run(ctx, pod, lambda p: p.channels.set_agent_answers(channel_id, on, **extra))


# -- your membership -----------------------------------------------------------------


@app.command("join")
def join_channel(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Join a public group, or a contact's channel; an admin or editor may also
    join a private group (it is recorded)."""
    _run(ctx, pod, lambda p: p.channels.join(channel_id))


@app.command("leave")
def leave_channel(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Leave a channel you are in."""
    _run(ctx, pod, lambda p: p.channels.leave(channel_id) or {"left": True})


@app.command("notify")
def set_notify(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    level: str | None = typer.Argument(None, help="ALL, MENTIONS or NONE."),
    via: list[str] = typer.Option(
        [], "--via", help="A platform to hear of it on. Repeat for more."
    ),
    pod_rule: bool = typer.Option(
        False, "--pod-rule", help="Hear of it wherever the pod's rule says."
    ),
    mute_until: str | None = typer.Option(
        None, "--mute-until", help="An ISO time to stay quiet until."
    ),
    unmute: bool = typer.Option(False, "--unmute"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Set how you hear of this channel's lines outside the app."""
    changes: dict[str, Any] = {}
    if level is not None:
        changes["notify"] = _choice(level, _NOTIFY, "LEVEL")
    if pod_rule:
        changes["via"] = None
    elif via:
        changes["via"] = list(via)
    if unmute:
        changes["muted_until"] = None
    elif mute_until is not None:
        changes["muted_until"] = mute_until
    if not changes:
        raise typer.BadParameter(
            "Pass LEVEL, --via, --pod-rule, --mute-until or --unmute."
        )
    _run(ctx, pod, lambda p: p.channels.set_notify(channel_id, **changes))


@app.command("read")
def mark_read(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    seq: int | None = typer.Option(
        None, "--seq", help="The line read up to; the newest line by default."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Mark the channel read, up to its newest line or --seq."""

    def call(p: Any) -> Any:
        upto = seq if seq is not None else p.channels.get(channel_id).last_seq
        return p.channels.mark_read(channel_id, upto)

    _run(ctx, pod, call)


# -- members -------------------------------------------------------------------------


@members_app.command("add")
def add_members(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="A group's id."),
    person: list[str] = typer.Option(
        [], "--person", help="A member's user id. Repeat for more."
    ),
    agent: list[str] = typer.Option(
        [],
        "--agent",
        help="An agent id (answers when named), or ID:EVERY_LINE. Repeat for more.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Add pod members and agents to a group."""
    if not person and not agent:
        raise typer.BadParameter("Pass --person or --agent.")
    seats = [_agent_seat(value) for value in agent]
    _run(ctx, pod, lambda p: p.channels.add_members(channel_id, person, agents=seats))


@members_app.command("update")
def update_member(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="A group's id."),
    member: str = typer.Argument(..., help="KIND:ID, e.g. MEMBER:<user id>."),
    role: str | None = typer.Option(None, "--role", help="A person: owner or member."),
    answers: str | None = typer.Option(
        None, "--answers", help="An agent: MENTIONED or EVERY_LINE."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Change a person's role in a group, or when an agent in it answers."""
    kind, member_id = _member_ref(member)
    chosen_role = _choice(role, _ROLES, "--role")
    chosen_answers = _choice(answers, _ANSWERS, "--answers")
    if chosen_role is None and chosen_answers is None:
        raise typer.BadParameter("Pass --role or --answers.")
    _run(
        ctx,
        pod,
        lambda p: p.channels.update_member(
            channel_id, kind, member_id, role=chosen_role, answers=chosen_answers
        ),
    )


@members_app.command("remove")
def remove_member(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="A group's id."),
    member: str = typer.Argument(..., help="KIND:ID, e.g. AGENT:<agent id>."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Take somebody out of a group."""
    kind, member_id = _member_ref(member)
    confirm_destructive(f"Remove {kind}:{member_id} from channel {channel_id}?", yes)
    _run(
        ctx,
        pod,
        lambda p: (
            p.channels.remove_member(channel_id, kind, member_id) or {"removed": True}
        ),
    )


# -- across the pod ------------------------------------------------------------------


@app.command("inbox")
def inbox(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show what is new for you across the pod, and what is waiting on you."""
    _run(ctx, pod, lambda p: p.channels.inbox(), render_inbox)


@app.command("search")
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Every word must match."),
    channel: str | None = typer.Option(None, "--channel", help="Only this channel."),
    limit: int = typer.Option(30, "--limit"),
    cursor: str | None = typer.Option(
        None, "--cursor", help="The previous page's next_cursor."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Search lines matching every word, newest first, in the channels you see."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.search(
            query, channel_id=channel, cursor=cursor, limit=limit
        ),
        render_search,
    )


@app.command("tell")
def tell(
    ctx: typer.Context,
    to: str = typer.Argument(..., help="A pod member id, user id or email."),
    text: str = typer.Argument(..., help="What to tell them."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Tell another member something, in their own channel."""
    _run(ctx, pod, lambda p: p.channels.notify(to, text))


@app.command("outreach")
def outreach(
    ctx: typer.Context,
    contact_id: str = typer.Argument(..., help="The contact's id."),
    handle: str = typer.Argument(
        ..., help="One of their handles: an email or a number."
    ),
    text: str = typer.Argument(..., help="What to write to them."),
    kind: str | None = typer.Option(
        None, "--kind", help="EMAIL, PHONE, ... when the handle could be two."
    ),
    client_message_id: str | None = typer.Option(
        None, "--client-message-id", help="The same id twice is one line."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Write to a contact first, in their channel: email any time, WhatsApp
    within 24 hours of their last message, never a handle that said stop."""
    _run(
        ctx,
        pod,
        lambda p: p.contacts.outreach(
            contact_id,
            handle=handle,
            text=text,
            kind=kind,
            client_message_id=client_message_id,
        ),
    )


# -- sources -------------------------------------------------------------------------


@source_app.command("list")
def list_sources(
    ctx: typer.Context,
    surface: str = typer.Argument(..., help="The bot's surface name."),
    limit: int = typer.Option(50, "--limit"),
    cursor: str | None = typer.Option(
        None, "--cursor", help="The previous page's next_cursor."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """The groups one bot is in, as channels, each with its source settings."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.sources.list(surface, limit=limit, cursor=cursor),
    )


@source_app.command("get")
def get_source(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="The group's channel id."),
    source_id: str = typer.Argument(..., help="Source id, from the channel."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """A group source's settings: who answers its outsiders, and from what."""
    _run(ctx, pod, lambda p: p.channels.sources.get(channel_id, source_id))


@source_app.command("set")
def set_source(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="The group's channel id."),
    source_id: str = typer.Argument(..., help="Source id, from the channel."),
    answers_outsiders: bool | None = typer.Option(
        None,
        "--answers-outsiders/--no-answers-outsiders",
        help="Answer people outside the pod there, or stop.",
    ),
    take_over: bool = typer.Option(
        False, "--take-over", help="Answer for its outsiders yourself."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Switch outsiders on or off in one group, or take it over."""
    changes: dict[str, Any] = {"take_over": take_over}
    if answers_outsiders is not None:
        changes["answers_outsiders"] = answers_outsiders
    if answers_outsiders is None and not take_over:
        raise typer.BadParameter("Name something to change.")
    _run(
        ctx,
        pod,
        lambda p: p.channels.sources.update(channel_id, source_id, **changes),
    )


@source_app.command("whatsapp-group")
def start_whatsapp_group(
    ctx: typer.Context,
    surface: str = typer.Argument(..., help="The WhatsApp surface's name."),
    title: str = typer.Argument(..., help="The group's subject."),
    no_outsiders: bool = typer.Option(
        False, "--no-outsiders", help="Do not answer people outside the pod."
    ),
    idempotency_key: str | None = typer.Option(
        None,
        "--idempotency-key",
        help="The same key again answers with the group it started.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Start a WhatsApp group with the pod's bot in it: a new group channel."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.sources.start_whatsapp_group(
            surface,
            title,
            answers_outsiders=not no_outsiders,
            idempotency_key=idempotency_key,
        ),
    )


@source_app.command("telegram-link")
def telegram_link(
    ctx: typer.Context,
    surface: str = typer.Argument(..., help="The Telegram surface's name."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """A one-use, hour-long link that adds the pod's Telegram bot to a group."""
    _run(ctx, pod, lambda p: p.channels.sources.telegram_link(surface))
