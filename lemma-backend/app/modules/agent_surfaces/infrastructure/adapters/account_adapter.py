from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountInfo,
    SurfaceAccountSummary,
    SurfaceAuthConfigInfo,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.surface_connector import (
    account_for_binding,
    bound_account_id,
)
from app.modules.connectors.contracts.pod_connectors import shared_account_ids
from app.modules.connectors.contracts.surfaces import (
    account_summaries,
    account_with_secrets,
    auth_config,
)


class SqlAlchemySurfaceAccountAdapter:
    def __init__(self, uow: IUnitOfWork):
        self._uow = uow

    async def get_account(self, account_id: UUID) -> SurfaceAccountInfo | None:
        found = await account_with_secrets(self._uow, account_id)
        if found is None:
            return None
        account, credentials = found
        return SurfaceAccountInfo(
            id=account.id,
            user_id=account.user_id,
            organization_id=account.organization_id,
            auth_config_id=account.auth_config_id,
            email=account.email,
            connector_id=account.connector_id,
            credentials=credentials,
        )

    async def list_account_summaries(
        self, account_ids: Sequence[UUID]
    ) -> dict[UUID, SurfaceAccountSummary]:
        """The non-secret half of a page of accounts, in one query."""
        return {
            account_id: SurfaceAccountSummary(
                id=account.id,
                user_id=account.user_id,
                connector_id=account.connector_id,
                display_name=account.display_name,
                email=account.email,
                status=account.status,
            )
            for account_id, account in (
                await account_summaries(self._uow, account_ids)
            ).items()
        }

    async def account_for_binding(
        self, *, pod_id: UUID, slug: str, platform: SurfacePlatform, ctx: object
    ) -> UUID:
        return await account_for_binding(
            self._uow, ctx, pod_id=pod_id, slug=slug, platform=platform
        )

    async def bound_account_id(self, surface: AgentSurfaceEntity) -> UUID | None:
        return await bound_account_id(self._uow, surface)

    async def accounts_behind_connectors(
        self, connectors: Sequence[tuple[UUID, str]]
    ) -> dict[tuple[UUID, str], UUID]:
        return await shared_account_ids(self._uow, list(connectors))


class SqlAlchemySurfaceAuthConfigAdapter:
    def __init__(self, uow: IUnitOfWork):
        self._uow = uow

    async def get_auth_config(
        self, auth_config_id: UUID
    ) -> SurfaceAuthConfigInfo | None:
        found = await auth_config(self._uow, auth_config_id)
        if found is None:
            return None
        return SurfaceAuthConfigInfo(
            id=found.id,
            kind=found.kind,
            connector_id=found.connector_id,
            config_source=found.config_source,
        )
