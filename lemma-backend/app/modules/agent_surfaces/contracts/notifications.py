"""Notifications, as an agent's tools use them.

Seven operations, replacing `app/composition/agent_notifications.py`. That file
existed because `agent` importing `agent_surfaces` would have closed a loop: the
eighth function on it, `deliver_replies_if_settled`, ran the *other* way, into
`agent`, to bring an asking conversation back once nothing it asked was
outstanding. The loop is cut by `NotificationSettledEvent` -- `agent_surfaces`
says the conversation is owed nothing and `agent` decides what to do about it --
so this direction is free to be an ordinary contract import.

Each function opens its own unit of work. An agent tool runs inside a live run's
session, and a notification send is not part of that run's transaction: if the
run later fails and rolls back, the message has already left for somebody's
phone and pretending otherwise would leave the pod with no record of it.

A submodule rather than `contracts/__init__`, which is a leaf: these reach the
service layer, and everything importing any surfaces contract would otherwise
pay for it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _service(uow):
    # Straight at this module's own factory. It used to go through
    # `app/composition/surface_agent.py` to pick up a `ConversationService` for
    # the notification service to hold; that shim is gone, and the operations
    # it stood in front of are `agent.contracts.conversations_for_surfaces`,
    # which the notification service reaches per call.
    from app.modules.agent_surfaces.api.dependencies import get_notification_service

    return get_notification_service(uow)


async def resolve_recipient(*, pod_id: UUID, reference: str) -> UUID | None:
    """A pod member id, user id, or email address → a user id in this pod."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await _service(uow).membership.resolve_pod_recipient(
            pod_id=pod_id, reference=reference
        )


async def send_notification(
    *,
    pod_id: UUID,
    recipient_user_id: UUID,
    title: str,
    body: str,
    actor_user_id: UUID | None,
    actor_agent_id: UUID | None,
    agent_name: str | None,
    origin_conversation_id: UUID | None,
    origin_agent_run_id: UUID | None,
    origin_surface_id: UUID | None,
    background_instruction: str | None,
    expects_response: bool,
    expires_in_seconds: int | None,
    idempotency_key: str | None,
    channel: str | None = None,
) -> dict:
    """Create and deliver, returning the flat shape the tool hands the model."""
    from app.modules.agent_surfaces.domain.notification import (
        NotificationOriginKind,
    )

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
        if expires_in_seconds
        else None
    )
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        notification = await _service(uow).notify(
            pod_id=pod_id,
            recipient_user_id=recipient_user_id,
            title=title,
            body=body,
            origin_kind=NotificationOriginKind.AGENT_RUN,
            origin_id=origin_agent_run_id,
            origin_conversation_id=origin_conversation_id,
            origin_surface_id=origin_surface_id,
            channel=channel,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
            agent_name=agent_name,
            background_instruction=background_instruction,
            expects_response=expects_response,
            expires_at=expires_at,
            idempotency_key=idempotency_key,
        )
        await uow.commit()
        return {
            "notification_id": notification.id,
            "delivery_status": notification.delivery_status.value,
            "delivered_via": notification.delivery_platform,
            "undeliverable_reason": notification.delivery_error,
        }


async def reachable_channels(
    *,
    pod_id: UUID,
    recipients: dict[UUID, str | None],
    actor_agent_id: UUID | None,
) -> dict[UUID, list[str]]:
    """``{user_id: channels}`` this agent can reach each person on right now.

    Degrades to "we do not know" rather than failing the lookup it decorates:
    an agent that cannot see the member list because reachability broke is worse
    off than one that sees the list without it, and ``message_user`` still
    routes on its own when no channel is named.

    Infrastructure only, deliberately not bare ``Exception``. This runs in a
    second unit of work after the member list has already come back, so the
    failure worth surviving is a transient one; a bug in our own code should
    still reach the tool result rather than read as "nowhere to reach them".
    """
    try:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            return await _service(uow).reachable_channels(
                pod_id=pod_id, recipients=recipients, actor_agent_id=actor_agent_id
            )
    except SQLAlchemyError, OSError:
        return {}


async def check_notifications(
    *, pod_id: UUID, notification_ids: list[UUID]
) -> list[dict]:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        notifications = await _service(uow).notifications.list_by_ids(
            pod_id=pod_id, notification_ids=notification_ids
        )
        return [
            {
                "notification_id": n.id,
                "status": n.status.value,
                "delivery_status": n.delivery_status.value,
                "recipient_user_id": n.recipient_user_id,
                "title": n.title,
                "response_summary": n.response_summary,
                "response_data": n.response_data,
                "responded_at": n.responded_at.isoformat() if n.responded_at else None,
            }
            for n in notifications
        ]


async def open_notifications_for_conversation(conversation_id: UUID) -> list[dict]:
    """What the recipient's agent needs to know when they reply.

    Includes ``background_instruction``, which is the whole point — it never
    reaches the recipient, only the agent acting on their reply.
    """
    try:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            notifications = await _service(
                uow
            ).notifications.list_open_for_conversation(conversation_id)
    except Exception:  # noqa: BLE001
        # Degrades to "nothing is open". A conversation must still work when
        # this read fails; the cost is a missed answer, not a broken reply --
        # and a missed answer nobody can see is how that stays invisible, so
        # the degradation says so.
        logger.warning(
            "agent_surfaces.notifications.open_lookup_degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return []
    return [
        {
            "notification_id": str(n.id),
            "title": n.title,
            "body": n.body,
            "background_instruction": n.background_instruction,
            "expects_response": n.expects_response,
            "responds_through_action": n.responds_through_action,
            "action": n.action,
        }
        for n in notifications
    ]


async def notification_form_action(
    *, pod_id: UUID, notification_id: UUID
) -> dict | None:
    """``{"run_id", "node_id"}`` when this ask is answered by a workflow form.

    Read from the notification rather than taken as tool arguments on purpose:
    the run and node are the asker's, and letting a recipient's agent name them
    would let it submit against any run whose ids it could guess.
    """
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        notification = await _service(uow).notifications.get(notification_id)
    if notification is None or notification.pod_id != pod_id:
        return None
    if not notification.responds_through_action:
        return None
    action = notification.action or {}
    run_id, node_id = action.get("run_id"), action.get("node_id")
    if not run_id or not node_id:
        return None
    return {"run_id": UUID(str(run_id)), "node_id": str(node_id)}


async def record_notification_response(
    *,
    pod_id: UUID,
    notification_id: UUID,
    responder_user_id: UUID,
    summary: str,
    data: dict | None = None,
) -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await _service(uow).respond(
            pod_id=pod_id,
            notification_id=notification_id,
            responder_user_id=responder_user_id,
            summary=summary,
            data=data,
        )
        await uow.commit()


__all__ = [
    "check_notifications",
    "notification_form_action",
    "open_notifications_for_conversation",
    "reachable_channels",
    "record_notification_response",
    "resolve_recipient",
    "send_notification",
]
