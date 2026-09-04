"""What the install helpers need from the service they are handed.

Several modules were split out of `ConnectorService` to keep it under its size
ceiling, and each took `service: Any` and reached back into the object -- three
of them into private methods. `Any` means the type checker sees none of that,
so the split moved lines without moving the coupling, and did it in a way
`typecheck-critical` cannot police. This branch added that gate precisely to
catch a port drifting from its implementation; leaving a hole of exactly that
shape behind is not a trade worth making.

Declaring the surface here does three things. It says out loud how much of the
service these helpers actually use, which is more than the split suggests. It
lets basedpyright check the call sites -- so removing or renaming one of these
fails at the seam rather than at run time. And it makes the private reaches
visible: `_resolve_auth_config`, `_resolve_auth_install` and
`_require_org_member` are named below because they are genuinely part of this
contract, not because a leading underscore should be taken as permission.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.modules.connectors.domain.account import AccountEntity, OAuthCredentials
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import ConnectorEntity
from app.modules.connectors.domain.ports import (
    AccountRepositoryPort,
    OAuthRedirectUriBuilderPort,
)


class InstallServiceSeam(Protocol):
    """The slice of `ConnectorService` the install helpers depend on."""

    account_repository: AccountRepositoryPort
    auth_config_repository: Any
    auth_config_operation_repository: Any
    redirect_uri_builder: OAuthRedirectUriBuilderPort
    uow: Any

    async def get_connector(self, connector_id: str) -> ConnectorEntity: ...

    async def get_auth_config_by_name(
        self, *, user_id: UUID, organization_id: UUID, auth_config_name: str
    ) -> AuthConfigEntity: ...

    async def get_account(
        self,
        account_id: UUID,
        user_id: UUID,
        organization_id: UUID | None = None,
    ) -> AccountEntity: ...

    async def get_account_credentials(
        self,
        account_id: UUID,
        user_id: UUID,
        organization_id: UUID | None = None,
        force_refresh: bool = False,
    ) -> OAuthCredentials: ...

    # Private on the service, and reached across this seam anyway. Named here
    # so that is a stated part of the contract rather than something a reader
    # has to discover from a call site.
    async def _resolve_auth_config(
        self,
        *,
        organization_id: UUID,
        connector_id: str | None = None,
        auth_config_id: UUID | None = None,
        auth_config_name: str | None = None,
    ) -> AuthConfigEntity: ...

    def _resolve_auth_install(
        self, connector: ConnectorEntity, auth_config: AuthConfigEntity
    ) -> ResolvedAuthInstall: ...

    async def _require_org_member(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        allowed_roles: list[str] | None = None,
    ) -> None: ...
