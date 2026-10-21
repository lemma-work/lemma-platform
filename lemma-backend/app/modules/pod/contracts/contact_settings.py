"""What a pod says to the people outside it on its own (``config.contacts``)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: The longest acknowledgement a pod may set; ``PodContactSettings`` holds it.
MAX_ACKNOWLEDGEMENT = 500


async def pod_auto_acknowledgement(session: AsyncSession, pod_id: UUID) -> str | None:
    """The line a contact is told while nobody answers them automatically,
    with ``{pod}`` read as the pod's name; ``None`` when the pod says nothing."""
    from app.modules.pod.infrastructure.models import Pod

    row = (
        await session.execute(
            select(Pod.name, Pod.config["contacts"]["auto_acknowledge"].astext).where(
                Pod.id == pod_id
            )
        )
    ).first()
    if row is None:
        return None
    name, text = row
    text = (text or "").strip()[:MAX_ACKNOWLEDGEMENT]
    if not text:
        return None
    return text.replace("{pod}", name or "the team")


__all__ = ["pod_auto_acknowledgement"]
