from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.crypto import get_secret_cipher
from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountInfo,
    SurfaceAccountSummary,
    SurfaceAuthConfigInfo,
)
from app.composition.surface_connectors import (
    Account,
    AccountRepository,
)


class SqlAlchemySurfaceAccountAdapter:
    def __init__(self, uow: IUnitOfWork):
        self._session = uow.session
        self._account_repository = AccountRepository(
            uow,
            encryption=get_secret_cipher(),
        )

    async def get_account(self, account_id: UUID) -> SurfaceAccountInfo | None:
        account = await self._account_repository.get(account_id)
        if account is None:
            return None
        credentials = account.credentials or {}
        if hasattr(credentials, "model_dump"):
            credentials = credentials.model_dump(exclude_none=True)
        return SurfaceAccountInfo(
            id=account.id,
            user_id=account.user_id,
            organization_id=account.organization_id,
            auth_config_id=account.auth_config_id,
            email=account.email,
            connector_id=account.connector_id or "",
            credentials=credentials,
        )

    async def list_account_summaries(
        self, account_ids: Sequence[UUID]
    ) -> dict[UUID, SurfaceAccountSummary]:
        """One query for a page of surfaces' accounts, selecting columns rather
        than entities: the read path needs no credentials, and loading whole
        accounts would decrypt one secret blob per surface for nothing."""
        ids = {account_id for account_id in account_ids if account_id is not None}
        if not ids:
            return {}
        rows = await self._session.execute(
            select(
                Account.id,
                Account.user_id,
                Account.connector_id,
                Account.display_name,
                Account.email,
                Account.status,
            ).where(Account.id.in_(ids))
        )
        return {
            row.id: SurfaceAccountSummary(
                id=row.id,
                user_id=row.user_id,
                connector_id=row.connector_id or "",
                display_name=row.display_name,
                email=row.email,
                status=row.status,
            )
            for row in rows
        }


class SqlAlchemySurfaceAuthConfigAdapter:
    def __init__(self, uow: IUnitOfWork):
        self._uow = uow

    async def get_auth_config(self, auth_config_id: UUID) -> SurfaceAuthConfigInfo | None:
        from app.composition.surface_connectors import AuthConfig

        auth_config = await self._uow.session.get(AuthConfig, auth_config_id)
        if auth_config is None:
            return None
        return SurfaceAuthConfigInfo(
            id=auth_config.id,
            kind=auth_config.kind,
            connector_id=auth_config.connector_id,
            config_source=auth_config.config_source,
        )
