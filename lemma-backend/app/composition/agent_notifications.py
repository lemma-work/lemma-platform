"""Notification access for agent tools.

The agent module must not import ``agent_surfaces`` — the dependency runs the
other way (surfaces reach into agents for conversations and runs), and reversing
it here would close the loop. Same pattern, and the same reason, as
``agent_snooze_scheduler.py``.

Each function opens its own unit of work. An agent tool runs inside a live run's
session, and a notification send is not part of that run's transaction: if the
run later fails and rolls back, the message has already left for somebody's
phone and pretending otherwise would leave the pod with no record of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory


def _service(uow):
    from app.modules.agent_surfaces.api.dependencies import get_notification_service
    from app.modules.agent.api.dependencies import get_conversation_service

    return get_notification_service(uow, get_conversation_service(uow))


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
    background_instruction: str | None,
    expects_response: bool,
    expires_in_seconds: int | None,
    idempotency_key: str | None,
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
        # this read fails; the cost is a missed answer, not a broken reply.
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
