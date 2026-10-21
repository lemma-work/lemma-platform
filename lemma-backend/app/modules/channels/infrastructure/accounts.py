"""External accounts: what a place belongs to, and how a surface reaches it.

An account is named by its workspace and, where one workspace holds several
bots, by the bot. A surface that reaches an account before it knows its bot's
id has one with no bot; once it learns the id, that same account is given it
-- never a second account, which would split the places, channels and history
heard before from those heard after.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import ColumnElement, Select, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import AccountKey, ExternalAccount
from app.modules.channels.infrastructure.models import (
    ChannelSourceModel,
    ExternalAccountModel,
)
from app.modules.channels.infrastructure.rows import account_entity, now


def accounts_of_surfaces(surface_ids: Collection[UUID]) -> Select[tuple[UUID]]:
    """The accounts these surfaces reach, as a subquery."""
    return select(ExternalAccountModel.id).where(
        ExternalAccountModel.surface_id.in_(sorted(surface_ids))
    )


def _named(pod_id: UUID, platform: str, workspace: str) -> list[ColumnElement[bool]]:
    account = ExternalAccountModel
    return [
        account.pod_id == pod_id,
        account.platform == platform,
        account.workspace_key == workspace,
    ]


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def account_for(
        self,
        *,
        pod_id: UUID,
        platform: str,
        key: AccountKey,
        surface_id: UUID | None,
    ) -> ExternalAccount:
        """The pod's account, made the first time; the surface that reaches it
        is whichever reported it last (a reinstalled bot takes it over).

        A bot named for the first time is given to the account this surface
        reached without one, in this workspace: two different bots in one
        workspace stay two accounts, but one bot is not split in two by
        when its id became known.
        """
        row = await self._found(pod_id, platform, key)
        if row is None and key.bot is not None and surface_id is not None:
            row = await self._given_its_bot(pod_id, platform, key, surface_id)
        if row is None:
            await self.session.execute(
                insert(ExternalAccountModel)
                .values(
                    pod_id=pod_id,
                    platform=platform,
                    workspace_key=key.workspace,
                    bot_key=key.bot,
                    surface_id=surface_id,
                )
                .on_conflict_do_nothing()
            )
            row = await self._found(pod_id, platform, key)
        assert row is not None  # inserted or there already
        if surface_id is not None and row.surface_id != surface_id:
            await self.session.execute(
                update(ExternalAccountModel)
                .where(ExternalAccountModel.id == row.id)
                .values(surface_id=surface_id, updated_at=now())
            )
            await self.session.refresh(row)
        return account_entity(row)

    async def _found(
        self, pod_id: UUID, platform: str, key: AccountKey
    ) -> ExternalAccountModel | None:
        bot = ExternalAccountModel.bot_key
        return await self.session.scalar(
            select(ExternalAccountModel).where(
                *_named(pod_id, platform, key.workspace),
                bot.is_(None) if key.bot is None else bot == key.bot,
            )
        )

    async def _given_its_bot(
        self, pod_id: UUID, platform: str, key: AccountKey, surface_id: UUID
    ) -> ExternalAccountModel | None:
        """This surface's bot-less account in the workspace, given its bot --
        unless another writer just made the account with that bot."""
        try:
            async with self.session.begin_nested():
                given = await self.session.scalar(
                    update(ExternalAccountModel)
                    .where(
                        *_named(pod_id, platform, key.workspace),
                        ExternalAccountModel.bot_key.is_(None),
                        ExternalAccountModel.surface_id == surface_id,
                    )
                    .values(bot_key=key.bot, updated_at=now())
                    .returning(ExternalAccountModel.id)
                )
        except IntegrityError:
            return None
        if given is None:
            return None
        return await self.session.get(
            ExternalAccountModel, given, populate_existing=True
        )

    async def get(self, account_id: UUID) -> ExternalAccount | None:
        row = await self.session.get(ExternalAccountModel, account_id)
        return account_entity(row) if row else None

    async def surfaces_of(
        self, account_ids: Collection[UUID]
    ) -> dict[UUID, UUID | None]:
        if not account_ids:
            return {}
        rows = await self.session.execute(
            select(ExternalAccountModel.id, ExternalAccountModel.surface_id).where(
                ExternalAccountModel.id.in_(sorted(account_ids))
            )
        )
        return {row[0]: row[1] for row in rows}

    async def surface_heard_from(self, surface_id: UUID) -> bool:
        """Whether anybody has written to this surface's bot at any place."""
        source = ChannelSourceModel
        found = await self.session.scalar(
            select(source.id)
            .join(ExternalAccountModel, ExternalAccountModel.id == source.account_id)
            .where(
                ExternalAccountModel.surface_id == surface_id,
                source.last_inbound_at.is_not(None),
            )
            .limit(1)
        )
        return found is not None
