"""Every shared place some surfaces' bots are in, walked a page at a time.

Beside ``source_repository`` rather than in it: its lookups each answer one
question about a place, freshest first, and stop at a page; this one has to
reach the last place, so it is ordered by id and resumed from the last one seen.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Source, SourceKind
from app.modules.channels.infrastructure.models import (
    ChannelSourceModel,
    ExternalAccountModel,
)
from app.modules.channels.infrastructure.rows import source_entity

#: The most places one page returns.
MAX_PAGE = 200


async def shared_places_page(
    session: AsyncSession,
    surface_ids: Collection[UUID],
    *,
    after: UUID | None,
    limit: int,
) -> list[Source]:
    """These surfaces' shared places with an id after ``after``, in id order.

    Through ``external_accounts.surface_id`` (indexed), the installation that
    reaches each account now -- never a workspace, which every app installed
    in it shares.
    """
    if not surface_ids:
        return []
    statement = (
        select(ChannelSourceModel, ExternalAccountModel.surface_id)
        .join(
            ExternalAccountModel,
            ExternalAccountModel.id == ChannelSourceModel.account_id,
        )
        .where(
            ExternalAccountModel.surface_id.in_(sorted(surface_ids)),
            ChannelSourceModel.kind == SourceKind.CHAT.value,
        )
    )
    if after is not None:
        statement = statement.where(ChannelSourceModel.id > after)
    rows = await session.execute(
        statement.order_by(ChannelSourceModel.id).limit(max(1, min(limit, MAX_PAGE)))
    )
    return [source_entity(row[0], surface_id=row[1]) for row in rows]
