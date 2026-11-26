"""One read: the apps a pod uses, and where the caller stands on each.

The Connections page used to put two lists side by side -- the pod's connectors
and the accounts in it -- and its banner read only the first, so a person who
had just connected GitHub was still offered "Connect GitHub". This answers the
page's one question per connector, for whoever asks, from what already exists:
the connector, its status (``connector_status``), whether the caller may use it
(``connector.use``, and for SHARED the account half), their own accounts, and
for an editor how many members have connected (``pod_connector_coverage``).

It is read-only. It declares nothing and adds nothing; "use it here" and
connecting do that, through their own routes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.connectors.api.schemas.pod_connection_schemas import (
    ConnectionAccount,
    ConnectionCoverage,
    ConnectionMe,
    ConnectionRunsAs,
    ConnectionStanding,
    ExistingAccount,
    GithubBot,
    GithubBotInstallation,
    PodConnectionItem,
    PodConnectionsResponse,
    RunsAsKind,
)
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    PodConnectorCoverage,
)
from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.app_installation import AppInstallation
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorStatus,
)
from app.modules.connectors.domain.ports import ConnectorRepositoryPort
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodConnectorRepository,
)
from app.modules.connectors.services.auth.github_app_installations import (
    bound_app_installations,
)
from app.modules.connectors.services.pod_connector_presenting import (
    PresentingParts,
    status_of,
)
from app.modules.connectors.services.pod_connector_resolution import acting_person
from app.modules.connectors.services.shared_account_use import (
    may_use_shared_account,
)

#: ``(ctx, pod_id, slug) -> coverage``: who of a PER_MEMBER connector's
#: members have connected (``PodConnectorCoverageService.coverage``).
CoverageReader = Callable[[Context, UUID, str], Awaitable[PodConnectorCoverage]]
#: ``(user_ids) -> {user_id: name}``: the names the pod shows for people.
NamesReader = Callable[[UUID, set[UUID]], Awaitable[dict[UUID, str | None]]]


class ConnectionsParts(PresentingParts, Protocol):
    """``PodConnectorService``'s reads, the connector list included."""

    pod_connectors: PodConnectorRepository


def _label(account: AccountEntity | None) -> str | None:
    if account is None:
        return None
    return account.display_name or account.email


class PodConnectionsReader:
    def __init__(
        self,
        *,
        parts: ConnectionsParts,
        catalog: ConnectorRepositoryPort,
        coverage: CoverageReader,
        names: NamesReader,
    ):
        self.parts = parts
        self.catalog = catalog
        self._coverage = coverage
        self._names = names

    async def read(self, ctx: Context, pod_id: UUID) -> PodConnectionsResponse:
        await ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))
        may_manage = await ctx.can(
            Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id)
        )
        installations: tuple[AppInstallation, ...] = ()
        if ctx.organization_id is not None:
            installations = await bound_app_installations(
                self.parts.uow.session, ctx.organization_id
            )
        counting = [one for one in installations if one.counts]
        connectors = await self.parts.pod_connectors.list_by_pod(pod_id)
        items = [
            await self._item(ctx, connector, may_manage=may_manage, bot=counting)
            for connector in connectors
        ]
        return PodConnectionsResponse(
            may_manage=may_manage,
            github_bot=GithubBot(
                installations=[
                    GithubBotInstallation(
                        login=one.account_login,
                        repository_selection=one.repository_selection,
                    )
                    for one in counting
                ]
            )
            if counting
            else None,
            items=items,
        )

    async def _item(
        self,
        ctx: Context,
        connector: PodConnectorEntity,
        *,
        may_manage: bool,
        bot: list[AppInstallation],
    ) -> PodConnectionItem:
        parts = self.parts
        auth_config = await parts.auth_configs.get(connector.auth_config_id)
        shared = (
            await parts.pod_accounts.get(connector.shared_pod_account_id)
            if connector.shared_pod_account_id is not None
            else None
        )
        status = await status_of(parts, connector, auth_config, shared)
        catalog = (
            await self.catalog.get(auth_config.connector_id) if auth_config else None
        )
        may_use = await ctx.can(
            Permissions.CONNECTOR_USE,
            ResourceRef.connector(connector.pod_id, connector.id),
        )
        if connector.mode is PodConnectorMode.APP:
            runs_as, me = self._app(status, may_use, bot)
        elif connector.mode is PodConnectorMode.SHARED:
            runs_as, me = await self._shared(ctx, status, may_use, shared)
        else:
            runs_as, me = await self._per_member(
                ctx, connector.pod_id, status, may_use, auth_config
            )
        coverage = None
        if may_manage and connector.mode is PodConnectorMode.PER_MEMBER:
            counted = await self._coverage(ctx, connector.pod_id, connector.slug)
            coverage = ConnectionCoverage(
                connected=counted.connected, total=counted.eligible
            )
        return PodConnectionItem(
            slug=connector.slug,
            auth_config_id=connector.auth_config_id,
            installation_name=auth_config.name if auth_config else None,
            connector_id=auth_config.connector_id if auth_config else None,
            title=catalog.title if catalog else None,
            icon=catalog.icon if catalog else None,
            mode=connector.mode,
            purpose=connector.purpose,
            required=connector.required,
            status=status,
            runs_as=runs_as,
            me=me,
            coverage=coverage,
        )

    @staticmethod
    def _app(
        status: PodConnectorStatus, may_use: bool, bot: list[AppInstallation]
    ) -> tuple[ConnectionRunsAs, ConnectionMe]:
        logins = [one.account_login for one in bot if one.account_login]
        runs_as = ConnectionRunsAs(kind=RunsAsKind.APP, label=", ".join(logins) or None)
        return runs_as, ConnectionMe(standing=_standing(status, may_use))

    async def _shared(
        self,
        ctx: Context,
        status: PodConnectorStatus,
        may_use: bool,
        shared: PodAccountEntity | None,
    ) -> tuple[ConnectionRunsAs, ConnectionMe]:
        account = await self.parts.accounts.get(shared.account_id) if shared else None
        if shared is None or not shared.is_shared:
            runs_as = ConnectionRunsAs(kind=RunsAsKind.NOBODY)
        else:
            names = await self._names(shared.pod_id, {shared.owner_user_id})
            runs_as = ConnectionRunsAs(
                kind=RunsAsKind.PERSON,
                label=_label(account),
                owner_user_id=shared.owner_user_id,
                owner_name=names.get(shared.owner_user_id),
            )
        standing = _standing(status, may_use)
        if standing is ConnectionStanding.READY and not (
            shared is not None and await may_use_shared_account(ctx, shared)
        ):
            standing = ConnectionStanding.NOT_ALLOWED
        mine = (
            ConnectionAccount(
                id=shared.account_id, label=_label(account), pod_account_id=shared.id
            )
            if shared is not None and shared.owner_user_id == acting_person(ctx)
            else None
        )
        return runs_as, ConnectionMe(standing=standing, account=mine)

    async def _per_member(
        self,
        ctx: Context,
        pod_id: UUID,
        status: PodConnectorStatus,
        may_use: bool,
        auth_config: AuthConfigEntity | None,
    ) -> tuple[ConnectionRunsAs, ConnectionMe]:
        person = acting_person(ctx)
        if person is None or auth_config is None:
            return ConnectionRunsAs(kind=RunsAsKind.ME), ConnectionMe(
                standing=ConnectionStanding.NOT_ALLOWED
            )
        accounts = self.parts.accounts
        pod_account = await self.parts.pod_accounts.get_member_default(
            pod_id=pod_id, owner_user_id=person, auth_config_id=auth_config.id
        )
        account = await accounts.get(pod_account.account_id) if pod_account else None
        runs_as = ConnectionRunsAs(kind=RunsAsKind.ME, label=_label(account))
        mine = (
            ConnectionAccount(
                id=account.id, label=_label(account), pod_account_id=pod_account.id
            )
            if account is not None and pod_account is not None
            else None
        )
        standing = _standing(status, may_use)
        if standing is not ConnectionStanding.READY:
            return runs_as, ConnectionMe(standing=standing, account=mine)
        if account is not None and mine is not None:
            return runs_as, ConnectionMe(
                standing=ConnectionStanding.READY
                if account.status is AccountStatus.CONNECTED
                else ConnectionStanding.RECONNECT,
                account=mine,
            )
        existing = await accounts.get_by_user_org_and_auth_config(
            person, auth_config.organization_id, auth_config.id
        )
        if existing is None:
            return runs_as, ConnectionMe(standing=ConnectionStanding.CONNECT)
        return runs_as, ConnectionMe(
            standing=ConnectionStanding.USE_EXISTING,
            existing=ExistingAccount(account_id=existing.id, label=_label(existing)),
        )


def _standing(status: PodConnectorStatus, may_use: bool) -> ConnectionStanding:
    """The part of a standing every mode shares: allowed at all, and able to
    run at all. ``ready`` here means "nothing in the way but the account"."""
    if not may_use:
        return ConnectionStanding.NOT_ALLOWED
    if status is PodConnectorStatus.READY:
        return ConnectionStanding.READY
    return ConnectionStanding.WAITING


__all__ = ["CoverageReader", "NamesReader", "PodConnectionsReader"]
