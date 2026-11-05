"""Which pooled WhatsApp number a Graph phone number id names, if any."""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (  # noqa: E501
    WhatsAppNumberRepository,
)


async def pooled_number(
    uow: SqlAlchemyUnitOfWork, phone_number_id: str
) -> WhatsAppNumberEntity | None:
    """The pool row, or None -- ordinary on a deployment with one number."""
    return await WhatsAppNumberRepository(uow).get_by_phone_number_id(phone_number_id)
