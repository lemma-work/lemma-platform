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
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.pod_connector import PodConnectorMode
from app.modules.connectors.services.pod_connector_resolution import (
    PodConnectorResolver,
    acting_person,
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


class InstallationReach:
    def __init__(self, resolver: PodConnectorResolver):
        self.resolver = resolver

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
        """
        offer = await self.installation(ctx, pod_id, name)
        auth_config = (
            await self.resolver.auth_configs.get(offer.auth_config_id)
            if offer is not None
            else None
        )
        if auth_config is None:
            return name
        declared = await self.resolver.ensure_for_installation(
            ctx, pod_id=pod_id, auth_config=auth_config
        )
        return declared.slug


__all__ = ["InstallationOffer", "InstallationReach"]
