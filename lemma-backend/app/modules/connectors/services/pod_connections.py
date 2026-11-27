"""One read: the apps a pod uses, and where the caller stands on each.

The Connections page used to put two lists side by side -- the pod's connectors
and the accounts in it -- and its banner read only the first, so a person who
had just connected GitHub was still offered "Connect GitHub". This answers the
page's one question per connector, for whoever asks, from what already exists:
the connector, its status (``connector_status``), whether the caller may use it
(``connector.use``, and for SHARED the account half), their own accounts, and
for an editor how many members have connected (``pod_connector_coverage``).

It declares nothing and adds nothing; "use it here" and connecting do that,
through their own routes. For GitHub it asks where the account in use stands
with the App's installations, the same reconcile the account page runs (cached,
and free for an account already bound): that reconcile binds an installation
this person has just proved they administer, as it does there.
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
    ConnectionGithub,
    ConnectionMe,
    ConnectionRunsAs,
    ConnectionStanding,
    ExistingAccount,
    GithubBot,
    GithubBotInstallation,
    GithubInstallationOffer,
    GithubWait,
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
from app.modules.connectors.services.auth.github_bindings import is_github
from app.modules.connectors.services.auth.github_installation import (
    InstallationOutcome,
    InstallState,
    WaitingOn,
    install_url,
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
#: ``(account) -> outcome``: where a GitHub account stands with the App's
#: installations (``GithubInstallationReconciler.outcome``, cached).
#: Where a GitHub account stands, or ``None`` when GitHub did not answer in
#: time to say.
GithubReader = Callable[[AccountEntity], Awaitable[InstallationOutcome | None]]

_WAITS = {
    WaitingOn.GITHUB_ADMIN: GithubWait.GITHUB_ADMIN,
    WaitingOn.GITHUB_PERMISSION: GithubWait.GITHUB_PERMISSION,
    WaitingOn.LEMMA_ADMIN: GithubWait.LEMMA_ADMIN,
}


def github_said(outcome: InstallationOutcome) -> ConnectionGithub | None:
    """Why a GitHub account is not usable here, or ``None`` when it is."""
    if outcome.is_ready:
        return None
    offers = [
        GithubInstallationOffer(
            installation_id=one.installation_id,
            login=one.account_login,
            manage_url=one.manage_url,
        )
        for one in outcome.choices
    ]
    if outcome.state is InstallState.CHOOSE_INSTALL and offers:
        return ConnectionGithub(wait=GithubWait.CHOOSE, choices=offers)
    if outcome.state is InstallState.PENDING_APPROVAL:
        only = offers[0] if len(offers) == 1 else None
        return ConnectionGithub(
            wait=_WAITS.get(outcome.waiting_on, GithubWait.GITHUB_ADMIN)
            if outcome.waiting_on
            else GithubWait.GITHUB_ADMIN,
            login=only.login if only else None,
            manage_url=only.manage_url if only else None,
            choices=offers,
        )
    return ConnectionGithub(wait=GithubWait.INSTALL, install_url=install_url())


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
        github: GithubReader | None = None,
    ):
        self.parts = parts
        self.catalog = catalog
        self._coverage = coverage
        self._names = names
        self._github = github

    async def _with_github(
        self, me: ConnectionMe, account: AccountEntity | None, github: bool
    ) -> ConnectionMe:
        """``me``, unless the GitHub account it would run as reaches nothing
        here: a connected account is not a usable one until an installation
        this organization holds is behind it, and saying ``ready`` before
        then sends people to a page that reaches no repository."""
        if (
            not github
            or self._github is None
            or me.standing is not ConnectionStanding.READY
            or account is None
        ):
            return me
        outcome = await self._github(account)
        said = (
            ConnectionGithub(wait=GithubWait.CHECK)
            if outcome is None
            else github_said(outcome)
        )
        if said is None:
            return me
        return me.model_copy(
            update={"standing": ConnectionStanding.INSTALL, "github": said}
        )

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
        github = is_github(auth_config.connector_id if auth_config else None)
        if connector.mode is PodConnectorMode.APP:
            runs_as, me = self._app(status, may_use, bot)
        elif connector.mode is PodConnectorMode.SHARED:
            runs_as, me = await self._shared(ctx, status, may_use, shared, github)
        else:
            runs_as, me = await self._per_member(
                ctx, connector.pod_id, status, may_use, auth_config, github
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
        github: bool,
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
        return runs_as, await self._with_github(
            ConnectionMe(standing=standing, account=mine), account, github
        )

    async def _per_member(
        self,
        ctx: Context,
        pod_id: UUID,
        status: PodConnectorStatus,
        may_use: bool,
        auth_config: AuthConfigEntity | None,
        github: bool,
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
            return runs_as, await self._with_github(
                ConnectionMe(
                    standing=ConnectionStanding.READY
                    if account.status is AccountStatus.CONNECTED
                    else ConnectionStanding.RECONNECT,
                    account=mine,
                ),
                account,
                github,
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


__all__ = [
    "CoverageReader",
    "GithubReader",
    "NamesReader",
    "PodConnectionsReader",
    "github_said",
]
