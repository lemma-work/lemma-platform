"""Writing down what the bot sent, and reading it back by the platform's id.

Two later moments need it and have only the platform's message id to go on: a
delivery status reporting that a send failed (``delivery_statuses``), and a
person replying to one of the bot's messages by quoting it (``turn_starter``).
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.infrastructure.outbound_models import (
    OUTBOUND_KIND_NOTIFICATION,
    OUTBOUND_KIND_REPLY,
)
from app.modules.agent_surfaces.infrastructure.repositories.outbound_message_repository import (  # noqa: E501
    SurfaceOutboundMessageRepository,
)
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

#: A quote is context, not a document -- the same bound Telegram's parser uses.
_QUOTED_TEXT_LIMIT = 1000


def _notification_id(metadata: dict[str, object]) -> UUID | None:
    raw = metadata.get("notification_id")
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


def _body(envelope: SurfaceEnvelope) -> str | None:
    """The words the envelope put in front of the person, as one string."""
    parts = [(envelope.text or "").strip()]
    parts.extend(plan.to_plain_text() for plan in envelope.resources)
    for prompt in (envelope.choices, envelope.decision):
        if prompt is not None:
            parts.append(prompt.to_plain_text())
    return "\n\n".join(part for part in parts if part) or None


async def record_outbound(
    uow: SqlAlchemyUnitOfWork,
    target: SurfaceEgressTarget,
    *,
    sent_ids: list[str],
    envelope: SurfaceEnvelope,
    metadata: dict[str, object],
    conversation_id: UUID,
) -> None:
    """One row per message id the platform acknowledged for this envelope."""
    if not sent_ids:
        return
    notification_id = _notification_id(metadata)
    await SurfaceOutboundMessageRepository(uow.session).record_sent(
        surface_id=target.surface.id,
        platform=target.surface.surface_type.value,
        external_message_ids=sent_ids,
        kind=OUTBOUND_KIND_NOTIFICATION if notification_id else OUTBOUND_KIND_REPLY,
        conversation_id=conversation_id,
        notification_id=notification_id,
        recipient=target.link.external_user_id,
        body=_body(envelope),
    )


async def quoted_outbound_message(
    uow: SqlAlchemyUnitOfWork, *, platform: str, external_message_id: str
) -> dict[str, object] | None:
    """The bot's own message a person quoted, shaped as ``quoted_message``.

    ``None`` when it is not one of ours on record -- older than the retention
    window, sent before the log existed, or simply somebody else's message.
    """
    sent = await SurfaceOutboundMessageRepository(uow.session).get_by_external_id(
        platform=platform, external_message_id=external_message_id
    )
    if sent is None or not sent.body:
        return None
    return {"author": None, "text": sent.body[:_QUOTED_TEXT_LIMIT], "is_bot": True}


__all__ = ["quoted_outbound_message", "record_outbound"]
