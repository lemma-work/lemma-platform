from __future__ import annotations

from uuid import UUID

from app.core.crypto import get_secret_cipher
from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountInfo,
    SurfaceAuthConfigInfo,
)
from app.composition.surface_connectors import (
    AccountRepository,
)


class SqlAlchemySurfaceAccountAdapter:
    def __init__(self, uow: IUnitOfWork):
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
            provider=auth_config.provider,
            connector_id=auth_config.connector_id,
            config_source=auth_config.config_source,
        )
