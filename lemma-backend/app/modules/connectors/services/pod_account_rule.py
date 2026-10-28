"""Which pod account a pod connector runs as, and the one rule on using it.

Every way into a pod connector -- a person or agent acting now
(``resolve``), a binding running with nobody at the keyboard
(``resolve_standing``), the deprecated installation route
(``serve_installation``) -- ends here. They differ only in what they bring:

- **who** a PER_MEMBER call is for (the acting person, a schedule's owner, or
  nobody);
- **how the account half of a SHARED call is checked**: as the actor, or, for
  a standing binding, as the person it runs for;
- **a pinned pod account**, for a standing binding that chose one of its
  owner's accounts (bot B, not their default bot A);
- whether a first use may **add** the person's org account to the pod (only a
  call they make themselves).

The rule itself is the same for all of them, so they cannot drift apart on
it again:

- SHARED: the connector's pod account, which its owner shared (not PERSONAL),
  and whose owner is still a member of the pod -- a departed member's account
  stops at once, not when the cleanup job gets to it. Then the caller's
  account check: POD-visible serves anyone the connector serves; RESTRICTED
  serves only people it was shared with.
- PER_MEMBER: the person's pinned pod account while it is still theirs, else
  their member default, else (on their own call) their org default, added to
  the pod as PERSONAL.
- Either way the account must exist, and not need a reconnect that trying it
  again cannot rule out (``reauth_recovery``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.config import settings
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorEntity,
    PodConnectorMode,
)
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectionRequiredError,
    ConnectionRequiresPersonError,
    ConnectorNeedsAccountError,
    ReauthRequiredError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
)
from app.modules.connectors.services.reauth_recovery import reauth_blocks_use

#: ``(pod_account) -> None``, raising when the caller may not use it.
SharedAccountCheck = Callable[[PodAccountEntity], Awaitable[None]]
#: ``(pod_id, user_id) -> bool``: still a member of the pod right now.
PodMembership = Callable[[UUID, UUID], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ResolvedPodConnector:
    pod_connector: PodConnectorEntity
    pod_account: PodAccountEntity
    account: AccountEntity
    auth_config: AuthConfigEntity


@dataclass(frozen=True, slots=True)
class AccountAsk:
    """Who wants the account behind a pod connector, and what they may do."""

    #: The person whose own account a PER_MEMBER call uses; None for a contact,
    #: an anonymous visitor, another pod or a binding with no person behind it.
    person: UUID | None
    #: The account half of a SHARED call, as this caller.
    shared_check: SharedAccountCheck
    #: A standing binding's own choice among its person's accounts.
    pinned_pod_account_id: UUID | None = None
    #: Whether this use may add the person's org account to the pod: only on
    #: their own call, never a background one.
    auto_add: bool = False
    record_use: bool = True


def connect_url(pod_id: UUID, slug: str) -> str:
    """Where a person connects (or reconnects) the account a connector needs.

    The pod's Connections page, opened on this connector, rather than the
    org-level connect flow: it is where the person was trying to work, and it
    is what returns them there once connected.
    """
    base = settings.frontend_url.rstrip("/")
    return f"{base}/pods/{pod_id}/connections?connector={slug}"


def needs_account(pod_connector: PodConnectorEntity) -> ConnectorNeedsAccountError:
    return ConnectorNeedsAccountError(
        slug=pod_connector.slug,
        connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
    )


class PodAccountPicker:
    """The account a pod connector runs as for one ask, checked."""

    def __init__(
        self,
        *,
        pod_accounts: PodAccountRepository,
        accounts: AccountRepositoryPort,
        is_pod_member: PodMembership,
    ):
        self.pod_accounts = pod_accounts
        self.accounts = accounts
        self.is_pod_member = is_pod_member

    async def pick(
        self,
        pod_connector: PodConnectorEntity,
        auth_config: AuthConfigEntity,
        ask: AccountAsk,
    ) -> ResolvedPodConnector:
        if pod_connector.mode is PodConnectorMode.SHARED:
            pod_account = await self.shared_account(pod_connector)
            await ask.shared_check(pod_account)
        else:
            pod_account = await self._member_account(pod_connector, auth_config, ask)
        account = await self.accounts.get(pod_account.account_id)
        if account is None:
            # Deleted between the two reads; the cascade removes the pod
            # account too, which is the state this already is in.
            raise needs_account(pod_connector)
        if reauth_blocks_use(account, auth_config):
            raise ReauthRequiredError(
                slug=pod_connector.slug,
                connector_id=auth_config.connector_id,
                connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
                acting_person_is_owner=ask.person == pod_account.owner_user_id,
            )
        if ask.record_use:
            await self.pod_accounts.touch(pod_account)
        return ResolvedPodConnector(
            pod_connector=pod_connector,
            pod_account=pod_account,
            account=account,
            auth_config=auth_config,
        )

    async def shared_account(
        self, pod_connector: PodConnectorEntity
    ) -> PodAccountEntity:
        """A SHARED connector's account, while its owner still offers it."""
        pod_account = (
            await self.pod_accounts.get(pod_connector.shared_pod_account_id)
            if pod_connector.shared_pod_account_id is not None
            else None
        )
        if pod_account is None or not pod_account.is_shared:
            raise needs_account(pod_connector)
        if not await self.is_pod_member(
            pod_connector.pod_id, pod_account.owner_user_id
        ):
            # They left; the cleanup job removes the pod account shortly. Until
            # it does, their credential serves nobody.
            raise needs_account(pod_connector)
        return pod_account

    async def _member_account(
        self,
        pod_connector: PodConnectorEntity,
        auth_config: AuthConfigEntity,
        ask: AccountAsk,
    ) -> PodAccountEntity:
        person = ask.person
        if person is None:
            raise ConnectionRequiresPersonError(slug=pod_connector.slug)
        if ask.pinned_pod_account_id is not None:
            pinned = await self.pod_accounts.get(ask.pinned_pod_account_id)
            if (
                pinned is not None
                and pinned.pod_id == pod_connector.pod_id
                and pinned.owner_user_id == person
                and pinned.auth_config_id == pod_connector.auth_config_id
            ):
                return pinned
        existing = await self.pod_accounts.get_member_default(
            pod_id=pod_connector.pod_id,
            owner_user_id=person,
            auth_config_id=pod_connector.auth_config_id,
        )
        if existing is not None:
            return existing
        account = (
            await self.accounts.get_by_user_org_and_auth_config(
                person, auth_config.organization_id, auth_config.id
            )
            if ask.auto_add
            else None
        )
        if account is None:
            raise ConnectionRequiredError(
                slug=pod_connector.slug,
                connector_id=auth_config.connector_id,
                connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
            )
        return await self.add_own(pod_connector.pod_id, account, as_default=True)

    async def add_own(
        self, pod_id: UUID, account: AccountEntity, *, as_default: bool
    ) -> PodAccountEntity:
        """The owner's own org account, in the pod as PERSONAL.

        ``as_default`` makes it the account their per-member calls use, when
        it already was in the pod -- added by hand, or carried over by the
        migration -- just not as that one.
        """
        added = await self.pod_accounts.add(
            PodAccountEntity(
                pod_id=pod_id,
                account_id=account.id,
                auth_config_id=account.auth_config_id,
                owner_user_id=account.user_id,
                visibility=PodAccountVisibility.PERSONAL,
                is_member_default=as_default,
                source=PodAccountSource.AUTO,
            )
        )
        if as_default and not added.is_member_default:
            await self.pod_accounts.make_member_default(added)
            added = added.model_copy(update={"is_member_default": True})
        return added


__all__ = [
    "AccountAsk",
    "PodAccountPicker",
    "PodMembership",
    "ResolvedPodConnector",
    "SharedAccountCheck",
    "connect_url",
    "needs_account",
]
