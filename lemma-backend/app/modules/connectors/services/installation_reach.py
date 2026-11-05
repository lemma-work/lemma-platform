"""The installations a person can reach in a pod that declares none for them.

On main the agent's connector tools listed and ran every installation in the
organization, on the person's own account. Pod connectors narrowed that to
what the pod declares -- and the migration declared connectors only where a
workload already held a grant, so an ordinary pod's assistant could no longer
reach the person's own Gmail. The decision (``06-review-fixes-plan.md``): the
tools offer the pod's connectors **plus** each installation the acting person
has an account on, and the first use of one declares it as a PER_MEMBER pod
connector (``ensure_for_installation``) that runs on their own account. For a
named agent the declaration is the same, and ``connector.use`` is then asked
as for any connector -- a refusal it can turn into a request for the grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.permissions import Permissions
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.pod_connector import (
    PodConnectorEntity,
    PodConnectorMode,
)
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectionRequiredError,
    PodConnectorNotFoundError,
)
from app.modules.connectors.domain.ports import (
    ConnectorRepositoryPort,
    OrganizationAccessPort,
)
from app.modules.connectors.services.pod_connector_resolution import (
    PodConnectorResolver,
    acting_person,
    connect_url,
)

#: A person connects a handful of accounts; this bounds one listing of them.
_MAX_ACCOUNTS = 100


@dataclass(frozen=True, slots=True)
class InstallationOffer:
    """An installation the acting person has an account on, not yet declared
    in the pod as a connector of their own account."""

    name: str
    connector_id: str
    kind: str
    auth_config_id: UUID

    @classmethod
    def of(cls, auth_config: AuthConfigEntity) -> InstallationOffer:
        return cls(
            name=auth_config.name,
            connector_id=auth_config.connector_id,
            kind=auth_config.kind.value,
            auth_config_id=auth_config.id,
        )


@dataclass(frozen=True, slots=True)
class Unconnected:
    """Something a tool may name that the pod has no connector for and the
    person no account on: an organization installation by name, or a catalog
    connector. Browsable as it is; running it needs a sign-in first."""

    connector_id: str
    #: The organization's install, when there is one; None for a bare catalog
    #: entry nobody installed.
    auth_config_id: UUID | None
    kind: str | None


class InstallationReach:
    def __init__(
        self,
        resolver: PodConnectorResolver,
        catalog: ConnectorRepositoryPort | None = None,
        organization_access: OrganizationAccessPort | None = None,
    ):
        self.resolver = resolver
        self.catalog = catalog
        self.organization_access = organization_access

    async def account_standing(self, ctx: Context) -> dict[UUID, str]:
        """The acting person's account status on each installation they have
        one on, by installation: what tells "connected" from "connect first"."""
        person = acting_person(ctx)
        if person is None or ctx.organization_id is None:
            return {}
        accounts, _ = await self.resolver.accounts.list_by_user_and_org(
            person, ctx.organization_id, limit=_MAX_ACCOUNTS
        )
        standing: dict[UUID, str] = {}
        for account in accounts:
            # A connected account wins over a stale one on the same install.
            if standing.get(account.auth_config_id) != "CONNECTED":
                standing[account.auth_config_id] = account.status.value
        return standing

    async def may_install(self, ctx: Context) -> bool:
        """Whether the person behind ``ctx`` may install a connector for the
        organization -- what a first sign-in on an uninstalled one needs."""
        person = acting_person(ctx)
        if person is None or ctx.organization_id is None:
            return False
        if self.organization_access is None:
            return False
        return await self.organization_access.user_holds_organization_permission(
            person, ctx.organization_id, Permissions.ORG_INSTALLATION_MANAGE
        )

    @staticmethod
    def connect_url(pod_id: UUID, name: str) -> str:
        """The pod's Connections page, opened on ``name``: where the person
        signs in, on Lemma's own address, and comes back to."""
        return connect_url(pod_id, name)

    async def unconnected(
        self, ctx: Context, pod_id: UUID, name: str
    ) -> Unconnected | None:
        """What ``name`` means when it is no pod connector's slug and none of
        the person's own installations; None when it means nothing at all."""
        if ctx.organization_id is None:
            return None
        if await self.resolver.pod_connectors.get_by_slug(pod_id, name) is not None:
            return None
        install = await self.resolver.auth_configs.get_active_by_org_and_name(
            ctx.organization_id, name
        )
        if install is not None:
            return Unconnected(
                connector_id=install.connector_id,
                auth_config_id=install.id,
                kind=install.kind.value,
            )
        if self.catalog is None or await self.catalog.get(name) is None:
            return None
        return Unconnected(connector_id=name, auth_config_id=None, kind=None)

    async def offered(self, ctx: Context, pod_id: UUID) -> list[InstallationOffer]:
        """The acting person's own installations the pod has no PER_MEMBER
        connector for, by name. Empty for anyone with no person behind them."""
        person = acting_person(ctx)
        if person is None or ctx.organization_id is None:
            return []
        accounts, _ = await self.resolver.accounts.list_by_user_and_org(
            person, ctx.organization_id, limit=_MAX_ACCOUNTS
        )
        connectors = await self.resolver.pod_connectors.list_by_pod(pod_id)
        declared = {
            connector.auth_config_id
            for connector in connectors
            if connector.mode is PodConnectorMode.PER_MEMBER
        }
        slugs = {connector.slug for connector in connectors}
        offers: dict[UUID, InstallationOffer] = {}
        for account in accounts:
            if account.auth_config_id in declared or account.auth_config_id in offers:
                continue
            auth_config = await self.resolver.auth_configs.get(account.auth_config_id)
            if (
                auth_config is None
                or auth_config.status is not AuthConfigStatus.ACTIVE
                # Its name is a connector's slug here, which is what a tool
                # naming it reaches.
                or auth_config.name in slugs
            ):
                continue
            offers[auth_config.id] = InstallationOffer.of(auth_config)
        return sorted(offers.values(), key=lambda offer: offer.name)

    async def installation(
        self, ctx: Context, pod_id: UUID, name: str
    ) -> InstallationOffer | None:
        """The installation ``name`` means here, when it is no pod connector's
        slug and the acting person has an account on it; else ``None``."""
        if ctx.organization_id is None:
            return None
        if await self.resolver.pod_connectors.get_by_slug(pod_id, name) is not None:
            return None
        auth_config = await self.resolver.auth_configs.get_active_by_org_and_name(
            ctx.organization_id, name
        )
        person = acting_person(ctx)
        if auth_config is None or person is None:
            return None
        account = await self.resolver.accounts.get_by_user_org_and_auth_config(
            person, auth_config.organization_id, auth_config.id
        )
        return InstallationOffer.of(auth_config) if account is not None else None

    async def slug_for(self, ctx: Context, pod_id: UUID, name: str) -> str:
        """The pod connector a tool's ``connector`` names, declaring it on this
        first use when ``name`` is one of the person's own installations.

        A name that is neither stays as it is, and fails as an unknown slug.
        The declaration is written in the caller's transaction and never
        committed here: a call refused after it rolls it back, so a refusal
        leaves no connector behind in the pod.
        """
        offer = await self.installation(ctx, pod_id, name)
        if offer is None:
            return name
        return await self.declare(ctx, pod_id, offer)

    async def declare(
        self, ctx: Context, pod_id: UUID, offer: InstallationOffer
    ) -> str:
        """Declare ``offer`` as the pod's PER_MEMBER connector; its slug."""
        auth_config = await self.resolver.auth_configs.get(offer.auth_config_id)
        if auth_config is None:
            return offer.name
        declared = await self.resolver.ensure_for_installation(
            ctx, pod_id=pod_id, auth_config=auth_config
        )
        return declared.slug


async def browsable(
    resolver: PodConnectorResolver,
    catalog: ConnectorRepositoryPort,
    ctx: Context,
    pod_id: UUID,
    name: str,
) -> InstallationOffer | Unconnected | None:
    """What ``name`` means when the pod has no connector by that slug: one of
    the caller's own installations, else an installation or catalog entry
    nobody here has connected. Reading its operations declares nothing."""
    reach = InstallationReach(resolver, catalog=catalog)
    return await reach.installation(ctx, pod_id, name) or await reach.unconnected(
        ctx, pod_id, name
    )


async def connector_for_call(
    resolver: PodConnectorResolver, ctx: Context, pod_id: UUID, name: str
) -> PodConnectorEntity:
    """The pod connector a call through the API names: its slug, or one of
    the caller's own installations by name, declared on this first use.

    What the agent tools always did, now for the pod execute route too, so a
    script or the CLI reaches the person's own Gmail in one call instead of
    falling back to the deprecated org-level route. Declared in the caller's
    transaction: a call refused after it rolls the declaration back. An
    installation the caller has no account on answers CONNECTION_REQUIRED
    with the link to connect it, rather than "no connector named".
    """
    found = await resolver.pod_connectors.get_by_slug(pod_id, name)
    if found is not None:
        return found
    reach = InstallationReach(resolver)
    offer = await reach.installation(ctx, pod_id, name)
    if offer is not None:
        slug = await reach.declare(ctx, pod_id, offer)
        return await resolver.connector(pod_id, slug)
    unconnected = await reach.unconnected(ctx, pod_id, name)
    if unconnected is not None and acting_person(ctx) is not None:
        raise ConnectionRequiredError(
            slug=name,
            connector_id=unconnected.connector_id,
            connect_url=connect_url(pod_id, name),
        )
    raise PodConnectorNotFoundError(name)


__all__ = [
    "browsable",
    "InstallationOffer",
    "InstallationReach",
    "Unconnected",
    "connector_for_call",
]
