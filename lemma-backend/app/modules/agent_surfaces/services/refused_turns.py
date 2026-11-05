"""A turn on a platform refused before its run started, said to the person.

A teammate pinned to a member's own runtime -- their Claude Code, their own
key -- runs only for them. Anybody else addressing it is refused before a run
exists (``runtime_profile_not_shareable``), as is a turn on a model that is
gone; the refusal is the same however often it is asked. On a platform it used
to be retried until the message went dead, and in a group nothing at all was
said: the agent simply never answered, and only a worker log knew why.

Now it is said once, to the member the turn was for, as a notice in their own
channel (``tell_member``: a members-only note, never pushed) -- once per place,
agent and reason, so a group that keeps addressing the agent is one note, not
one per message. Nothing is said in a shared place: everybody there would read
one person's refusal, which names a teammate's own runtime. A direct message
is answered in place too, since the person there is the only reader. Their
message still lands on the place's timeline: it is something they said.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
)
from app.modules.agent_surfaces.services.channel_lines import (
    ChannelLines,
    ChannelLinesFor,
)
from app.modules.agent_surfaces.services.direct_messages import tell_member

logger = get_logger(__name__)

_PLATFORM_NAMES = {"WHATSAPP": "WhatsApp"}

#: What was left unanswered, where "your message on <Platform>" would not read
#: as the person knows it. An email surface is RESEND, the provider's name --
#: not a word anybody who sent an email would recognise.
_UNANSWERED = {"RESEND": "your email"}

#: How the member is told: ``tell_member`` (looked up per call), unless a caller
#: passes another.
TellMember = Callable[..., Awaitable[object]]


class _Starter(Protocol):
    """What of ``SurfaceTurnStarter`` saying a refusal takes."""

    write_scope: Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]
    channel_lines: ChannelLinesFor

    async def tell_sender(self, context: AgentSurfaceContext, message: str) -> None: ...


async def say_turn_refused(
    starter: _Starter,
    context: SurfaceChatContext,
    message_text: str,
    refused: DomainError,
    *,
    tell: TellMember | None = None,
) -> None:
    """A refusal retrying cannot change, said instead of retried: written in
    the ingest's own completion, so the inbound row is done with it."""
    async with starter.write_scope() as uow:
        await note_refused_turn(
            uow,
            context,
            message_text=message_text,
            refused=refused,
            lines=starter.channel_lines(uow),
            tell=tell,
        )
    if says_in_place(context):
        await starter.tell_sender(context, refusal_text(context, refused))


def says_in_place(context: SurfaceChatContext) -> bool:
    """Whether the refusal is said where it was asked as well: a member's own
    direct message, read by nobody else."""
    return context.event.is_dm and not context.audience.answers_outsiders


def refusal_text(context: SurfaceChatContext, refused: DomainError) -> str:
    agent = context.agent_display_name or "The agent"
    platform = str(context.platform.value)
    where = _PLATFORM_NAMES.get(platform, platform.capitalize())
    what = _UNANSWERED.get(platform, f"your message on {where}")
    return f"{agent} didn't answer {what}. {refused.message}"


def refusal_key(context: SurfaceChatContext, refused: DomainError) -> str:
    """Once per place, agent and reason: the place's source, else the
    conversation (one per person, agent and place)."""
    place = context.source_id or context.conversation_id
    raw = f"{place}:{context.agent_name or '-'}:{refused.code}"
    return "refused-turn:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


async def note_refused_turn(
    uow: SqlAlchemyUnitOfWork,
    context: SurfaceChatContext,
    *,
    message_text: str,
    refused: DomainError,
    lines: ChannelLines,
    tell: TellMember | None = None,
) -> None:
    """The message as its line, and the member told why it was not answered."""
    assert context.pod_id is not None  # a chat context that reached a turn
    logger.warning(
        "agent_surfaces.turn_starter.turn_refused.observed",
        platform=str(context.platform),
        conversation_id=str(context.conversation_id),
        reason=refused.code,
    )
    if message_text.strip():
        await lines.heard(context, text=message_text)
    await (tell or tell_member)(
        uow,
        pod_id=context.pod_id,
        user_id=context.user_id,
        text=refusal_text(context, refused),
        key=refusal_key(context, refused),
    )


__all__ = [
    "note_refused_turn",
    "refusal_key",
    "refusal_text",
    "say_turn_refused",
    "says_in_place",
]
