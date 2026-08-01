"""The ``message_person`` tool — an agent reaching a colleague.

Distinct from ``surface_send_message``, which only ever reaches the person whose
conversation the agent is already in. This one crosses an identity boundary, so
it carries the constraints that boundary implies: the recipient must be a pod
member, the message is attributed to both the agent and the human whose
authority it runs under, and there is a ceiling on how often one agent can reach
one person.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    MessagePersonRequest,
    MessagePersonResponse,
)

logger = get_logger(__name__)


async def _conversation_origin(conversation_id: UUID | None) -> str | None:
    """What started this conversation, or None if a person did.

    ``SCHEDULE_RUN`` / ``WORKFLOW_RUN`` mean nobody is sitting in front of it —
    the run's final answer lands somewhere no one opens. That is exactly when an
    agent needs to be able to tell its owner something.
    """
    if conversation_id is None:
        return None
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
    from app.modules.agent.infrastructure.repositories import ConversationRepository

    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            conversation = await ConversationRepository(uow).get_conversation(
                conversation_id
            )
        return getattr(conversation, "origin_type", None) if conversation else None
    except Exception:
        logger.debug("agent.messaging.conversation_origin_failed", exc_info=True)
        return None


async def _pod_members(pod_id: UUID) -> list:
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
    from app.modules.pod.infrastructure.pod_repositories import PodRepository

    async with create_uow_from_session_maker(async_session_maker) as uow:
        members, _ = await PodRepository(uow).list_pod_members(pod_id, limit=500)
    return [m for m in members if m.user_id is not None]


def _resolve_pod_member(
    members: list, person: str
) -> tuple[UUID | None, str | None]:
    """Find the one pod member ``person`` names.

    Returns ``(user_id, error)``. An ambiguous name is an error rather than a
    best guess: messaging the wrong colleague is not a mistake the agent can see
    or undo, so it has to come back and ask.
    """
    needle = str(person or "").strip().lower()
    if not needle:
        return None, "No person given."

    by_email = [
        m for m in members if str(m.user_email or "").strip().lower() == needle
    ]
    if len(by_email) == 1:
        return by_email[0].user_id, None

    by_name = [m for m in members if str(m.user_name or "").strip().lower() == needle]
    if len(by_name) == 1:
        return by_name[0].user_id, None
    if len(by_name) > 1:
        return None, (
            f"More than one pod member is called {person!r}. "
            "Use their email address instead."
        )
    return None, (
        f"No pod member matches {person!r}. They must be a member of this pod "
        "to be reachable."
    )


def _attribution(members: list, requester_user_id: UUID | None, agent_label: str) -> str:
    """Name the agent AND the person it is acting for.

    Without this the recipient sees the pod's bot and reads the message as
    coming from Lemma itself, which is exactly the trust a proactive message
    must not borrow.
    """
    requester = next(
        (
            m.user_name or m.user_email
            for m in members
            if m.user_id == requester_user_id
        ),
        None,
    )
    return f"_{agent_label}, working for {requester}_" if requester else f"_{agent_label}_"


async def message_person(
    ctx: RunContext[BaseAgentContext], request: MessagePersonRequest
) -> MessagePersonResponse:
    """Send a message to another member of this pod.

    USE WHEN you need something from a specific person — a decision, a missing
    value, a confirmation — and waiting for them to come to you is not going to
    work. Also use it to tell someone about work that concerns them.

    They receive ONLY your `message` text, shown as coming from you (an agent),
    with the person whose authority you are running under named alongside it.
    They do not see this conversation, your task, or your reasoning — so write a
    message that stands on its own: who you are, what you need, and why.

    Delivery lands in their Lemma inbox always, and additionally on whichever
    chat platform they most recently used to talk to this pod, if any. Their
    reply starts a conversation of THEIR own, under THEIR permissions — you will
    not see it in this conversation, so do not wait on it here.

    Do NOT use this to reach someone who is reading this conversation — reply to
    them normally instead. The exception is a run nobody is watching (a schedule,
    or a workflow step): there is no "normally" there, so telling the run's owner
    what happened is exactly what this is for, and it links back to this run.
    """
    from app.modules.agent_surfaces.domain.entities import NotificationOrigin
    from app.modules.agent_surfaces.services.surface_display_delivery import (
        notify_member,
    )

    deps = ctx.deps
    pod_id = getattr(deps, "pod_id", None)
    if pod_id is None:
        return MessagePersonResponse(
            success=False, error="This conversation is not inside a pod."
        )

    members = await _pod_members(pod_id)
    recipient_id, error = _resolve_pod_member(members, request.person)
    if recipient_id is None:
        return MessagePersonResponse(success=False, error=error)

    # Reaching the person the run belongs to is normally the wrong tool — just
    # reply. But a schedule- or workflow-born run has nobody reading its
    # conversation, so "reply to them directly" is advice with no destination.
    # That run telling its owner something is the whole point of notifications.
    conversation_id = getattr(deps, "conversation_id", None)
    notify_conversation_id: UUID | None = None
    if recipient_id == getattr(deps, "user_id", None):
        origin = await _conversation_origin(conversation_id)
        if origin is None:
            return MessagePersonResponse(
                success=False,
                error=(
                    "That is the person you are already working for, and they are "
                    "reading this conversation — reply to them here instead."
                ),
            )
        # Hand the run's own conversation to notify so the inbox entry opens the
        # work that produced it, rather than a bare message with no trail.
        notify_conversation_id = conversation_id

    agent_label = getattr(deps, "agent_display_name", None) or getattr(
        deps, "agent_name", None
    )
    try:
        outcome = await notify_member(
            pod_id=pod_id,
            recipient_user_id=recipient_id,
            body=request.message,
            title=request.title,
            agent_name=getattr(deps, "agent_name", None),
            origin_type=NotificationOrigin.AGENT_RUN,
            origin_id=getattr(deps, "agent_run_id", None),
            conversation_id=notify_conversation_id,
            attribution=_attribution(
                members, getattr(deps, "user_id", None), agent_label or "An agent"
            ),
        )
    except Exception:
        logger.debug("agent.messaging.message_person_failed", exc_info=True)
        return MessagePersonResponse(
            success=False, error="Could not deliver the message."
        )

    if outcome is None:
        return MessagePersonResponse(
            success=False,
            error=f"{request.person} is not a member of this pod.",
        )

    delivered = outcome.delivered_via.value if outcome.delivered_via else None
    return MessagePersonResponse(
        success=True,
        delivered_via=delivered,
        conversation_id=(
            str(outcome.conversation_id) if outcome.conversation_id else None
        ),
        message=(
            f"Delivered to {request.person} in Lemma."
            if not outcome.reached_a_chat_surface
            else f"Delivered to {request.person} on {delivered} and in Lemma."
        ),
    )


messaging_toolset = FunctionToolset[BaseAgentContext](tools=[message_person])
