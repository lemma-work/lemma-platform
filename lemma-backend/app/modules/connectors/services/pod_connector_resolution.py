"""The one way an operation picks the account it runs as.

``resolve(ctx, pod_connector)`` is design §3.3. Every caller names a pod
connector and nothing else; the account follows from the connector's mode and
from who is acting:

- **SHARED**: the pod account the connector names, whoever acts. Two checks,
  ``connector.use`` on the connector and ``connector_account.use`` on the pod
  account -- the second is what a POD_VIEWER, or anyone a RESTRICTED account
  was not shared with, fails.
- **PER_MEMBER**: the acting person's own pod account for that installation.
  On their first call the pod picks up their default org account as a PERSONAL
  pod account, so the pod records which accounts it uses (R5).

The three entry points -- ``resolve`` (somebody acting now),
``resolve_standing`` (a binding running with nobody at the keyboard) and
``serve_installation`` (the deprecated installation route) -- are thin: each
says who is asking and how its account half is checked, and the account and
the rule on it are ``PodAccountPicker``'s (``pod_account_rule.py``), once.

It replaced ``AccountResolutionService``, whose account was an ``account_id``
argument any caller -- the model included -- could type, with authorization on
it inferred per calling pod from whether some grant happened to exist.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from functools import partial
from uuid import UUID

from app.core.authorization.context import (
    Context,
    Person,
    ResourceRef,
    Workload,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.workload_authority import invoking_person_context
from app.core.config import settings
from app.core.domain.errors import DomainError
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.errors import ConnectorAccessDeniedError
from app.modules.connectors.domain.pod_connector import (
    SLUG_PATTERN,
    PodAccountEntity,
    PodAccountVisibility,
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorSource,
)
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectorNeedsAccountError,
    ConnectorUnavailableError,
    ConnectorUseRefusedError,
    PodConnectorConflictError,
    PodConnectorNotFoundError,
    ReauthRequiredError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
    PodConnectorRepository,
)
from app.modules.connectors.services.pod_account_rule import (
    AccountAsk,
    PodAccountPicker,
    PodMembership,
    ResolvedPodConnector,
    connect_url,
    needs_account,
)
from app.modules.connectors.services.reauth_recovery import reauth_blocks_use

#: ``(user_id, pod_id) -> Context``: a person, as themselves, in a pod. For the
#: account half of a standing binding, whose person is not the one asking.
PersonContext = Callable[[UUID, UUID], Awaitable[Context]]

#: What a declared SHARED connector answers when it cannot serve this caller --
#: may not use it, or it has no account to offer -- and the deprecated route
#: may try the next one. By type: a reauth, an outage or a bug is not "try
#: another identity".
_UNSERVED = (
    ConnectorUseRefusedError,
    ConnectorAccessDeniedError,
    ConnectorNeedsAccountError,
)
#: Authorization answers that are not a refusal of this connector.
_NOT_A_REFUSAL = frozenset({"AUTH_REQUIRED", "NOT_FOUND"})


def acting_person(ctx: Context) -> UUID | None:
    """The person whose own account a PER_MEMBER call uses, if there is one.

    Themselves for a person; the person mirrored by the assistant or invoking
    a named agent or function. Nobody for a contact, an anonymous visitor,
    another pod or a run with no person behind it.
    """
    if isinstance(ctx.actor, Person):
        return ctx.actor.user_id
    return ctx.delegating_user_id


class PodConnectorResolver:
    def __init__(
        self,
        *,
        pod_connectors: PodConnectorRepository,
        pod_accounts: PodAccountRepository,
        auth_configs: AuthConfigRepository,
        accounts: AccountRepositoryPort,
        is_pod_member: PodMembership,
        person_context: PersonContext,
    ):
        self.pod_connectors = pod_connectors
        self.pod_accounts = pod_accounts
        self.auth_configs = auth_configs
        self.accounts = accounts
        self.person_context = person_context
        self.picker = PodAccountPicker(
            pod_accounts=pod_accounts, accounts=accounts, is_pod_member=is_pod_member
        )

    async def connector(self, pod_id: UUID, slug: str) -> PodConnectorEntity:
        found = await self.pod_connectors.get_by_slug(pod_id, slug)
        if found is None:
            raise PodConnectorNotFoundError(slug)
        return found

    # -- the three ways in ----------------------------------------------------

    async def resolve(
        self,
        ctx: Context,
        pod_connector: PodConnectorEntity,
        *,
        pod_account_id: UUID | None = None,
    ) -> ResolvedPodConnector:
        """The account ``pod_connector`` runs as for ``ctx``, authorized.

        ``pod_account_id`` pins one of the acting person's own accounts on a
        PER_MEMBER connector -- what a binding they are making will keep.
        """
        auth_config = await self._installation(pod_connector)
        await self.require_use(ctx, pod_connector, auth_config)
        return await self.picker.pick(
            pod_connector,
            auth_config,
            AccountAsk(
                person=acting_person(ctx),
                shared_check=partial(_require_account_use, ctx),
                pinned_pod_account_id=pod_account_id,
                auto_add=True,
            ),
        )

    async def resolve_standing(
        self,
        pod_connector: PodConnectorEntity,
        *,
        on_behalf_of: UUID | None,
        pod_account_id: UUID | None = None,
        record_use: bool = True,
    ) -> ResolvedPodConnector:
        """The account behind a binding that runs with nobody at the keyboard.

        A surface answering a message and a schedule's trigger subscription
        were bound by a person, through ``resolve`` with that person's own
        authority; what keeps them running afterwards is the standing of what
        they were bound to, re-read on every use. So this fails the same typed
        way ``resolve`` does the moment it changes: the owner withdraws a
        shared account or leaves the pod (NEEDS_ACCOUNT), the person of a
        per-member binding has no account in the pod any more
        (CONNECTION_REQUIRED), the installation is disabled, the account needs
        reauth -- or an editor repoints the connector at a RESTRICTED account
        this binding's person was never given.

        ``on_behalf_of`` is the person a binding runs for -- a schedule's
        owner. ``pod_account_id`` is the account it pinned, used while it is
        still theirs. Nothing is auto-added here: a pod account appears on a
        person's own call, never on a background one.
        """
        auth_config = await self._installation(pod_connector)
        return await self.picker.pick(
            pod_connector,
            auth_config,
            AccountAsk(
                person=on_behalf_of,
                shared_check=partial(
                    self._standing_account_use, pod_connector, on_behalf_of
                ),
                pinned_pod_account_id=pod_account_id,
                record_use=record_use,
            ),
        )

    async def serve_installation(
        self,
        ctx: Context,
        *,
        pod_id: UUID,
        auth_config: AuthConfigEntity,
        account_id: UUID | None,
    ) -> ResolvedPodConnector:
        """The deprecated way in: an installation and, maybe, an account.

        Served through the pod's connectors for that installation, with the
        same checks as ``resolve``, so a deprecated call can never reach an
        account the new one could not -- and never switches whose account a
        call runs as:

        - the caller's own account (named, or by default) goes through a
          PER_MEMBER connector, declared on this first use when the pod has
          none (``ensure_for_installation``). If that fails, its error is the
          answer; no shared account steps in;
        - a shared account is used when ``account_id`` names it, or when the
          pod declares no PER_MEMBER connector for the installation at all.
        """
        declared = await self.pod_connectors.list_for_installation(
            pod_id, auth_config.id
        )
        shared = [c for c in declared if c.mode is PodConnectorMode.SHARED]
        per_member = [c for c in declared if c.mode is PodConnectorMode.PER_MEMBER]
        own: AccountEntity | None = None
        if account_id is not None:
            account = await self.accounts.get(account_id)
            if account is not None and account.user_id == acting_person(ctx):
                own = account
            else:
                return await self._shared_running_as(ctx, shared, account_id)
        if own is None and shared and not per_member:
            return await self._first_shared(ctx, shared)
        pod_connector = (
            per_member[0]
            if per_member
            else await self.ensure_for_installation(
                ctx, pod_id=pod_id, auth_config=auth_config
            )
        )
        pinned = None
        if own is not None and own.auth_config_id == auth_config.id:
            pinned = (await self.picker.add_own(pod_id, own, as_default=False)).id
        elif own is not None:
            raise _not_served(declared)
        return await self.resolve(ctx, pod_connector, pod_account_id=pinned)

    # -- declaring ------------------------------------------------------------

    async def ensure_for_installation(
        self,
        ctx: Context,
        *,
        pod_id: UUID,
        auth_config: AuthConfigEntity,
        source: PodConnectorSource = PodConnectorSource.AUTO,
    ) -> PodConnectorEntity:
        """The pod's PER_MEMBER connector for an installation, declared if none.

        Declared only for someone with a reason to: the acting person has an
        account on the installation (their first use of it here), or the
        caller may declare connectors anyway (an editor importing a bundle).
        Otherwise there is nothing to serve and the answer is
        POD_CONNECTOR_NOT_FOUND. Declaring grants nothing: a named agent still
        needs ``connector.use`` on it, which ``resolve`` asks.
        """
        found = await self._per_member_for(pod_id, auth_config.id)
        if found is not None:
            return found
        if (
            ctx.organization_id is not None
            and auth_config.organization_id != ctx.organization_id
        ):
            raise PodConnectorNotFoundError(auth_config.name)
        if auth_config.status is AuthConfigStatus.DISABLED:
            raise ConnectorUnavailableError(slug=auth_config.name)
        person = acting_person(ctx)
        has_account = (
            person is not None
            and await self.accounts.get_by_user_org_and_auth_config(
                person, auth_config.organization_id, auth_config.id
            )
            is not None
        )
        if not has_account and not await ctx.can(
            Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id)
        ):
            raise PodConnectorNotFoundError(auth_config.name)
        for _ in range(_DECLARE_ATTEMPTS):
            taken = {c.slug for c in await self.pod_connectors.list_by_pod(pod_id)}
            try:
                return await self.pod_connectors.create(
                    PodConnectorEntity(
                        pod_id=pod_id,
                        slug=free_slug(auth_config, taken),
                        auth_config_id=auth_config.id,
                        mode=PodConnectorMode.PER_MEMBER,
                        source=source,
                        created_by_user_id=person,
                    )
                )
            except PodConnectorConflictError:
                # A concurrent first use declared it, or took the slug.
                found = await self._per_member_for(pod_id, auth_config.id)
                if found is not None:
                    return found
        raise PodConnectorConflictError(auth_config.name)

    # -- outside any pod ------------------------------------------------------

    async def own_account(
        self, ctx: Context, auth_config: AuthConfigEntity, account_id: UUID | None
    ) -> AccountEntity:
        """A person's own account, outside any pod: main's behaviour.

        Only for a person acting as themselves with no pod (the CLI with none
        selected). Their own account was always theirs to use; nothing else is
        reachable this way.
        """
        if not isinstance(ctx.actor, Person):
            raise PodConnectorNotFoundError(auth_config.name)
        person = ctx.actor.user_id
        account = (
            await self.accounts.get(account_id)
            if account_id is not None
            else await self.accounts.get_by_user_org_and_auth_config(
                person, auth_config.organization_id, auth_config.id
            )
        )
        if (
            account is None
            or account.user_id != person
            or account.auth_config_id != auth_config.id
        ):
            raise ConnectorAccessDeniedError(
                "You can only use your own account for this installation.",
                details={"reason": "not_your_account"},
            )
        if reauth_blocks_use(account, auth_config):
            raise ReauthRequiredError(
                slug=auth_config.name,
                connector_id=auth_config.connector_id,
                connect_url=settings.frontend_url.rstrip("/"),
                acting_person_is_owner=True,
            )
        return account

    # -- the checks -----------------------------------------------------------

    async def require_use(
        self,
        ctx: Context,
        pod_connector: PodConnectorEntity,
        auth_config: AuthConfigEntity | None = None,
    ) -> None:
        """``connector.use``, refused as a typed error naming the connector.

        The refusal keeps the authorization error's code and
        ``permission_ids`` (approvals key on them) and adds what a card
        offering the grant needs: the connector and the workload asking.
        """
        try:
            await ctx.require(
                Permissions.CONNECTOR_USE,
                ResourceRef.connector(pod_connector.pod_id, pod_connector.id),
            )
        except DomainError as refusal:
            if refusal.code in _NOT_A_REFUSAL:
                raise
            workload = ctx.workload
            raise ConnectorUseRefusedError(
                refusal,
                slug=pod_connector.slug,
                pod_connector_id=str(pod_connector.id),
                connector_id=auth_config.connector_id if auth_config else None,
                workload_id=str(workload.workload_id) if workload else None,
                workload_kind=workload.kind.value if workload else None,
            ) from refusal

    async def _standing_account_use(
        self,
        pod_connector: PodConnectorEntity,
        on_behalf_of: UUID | None,
        pod_account: PodAccountEntity,
    ) -> None:
        """The account half of a SHARED standing binding.

        POD-visible serves any binding the connector serves. A RESTRICTED one
        serves a binding only while the person it runs for holds
        ``connector_account.use`` on it, exactly as their own call would need
        -- so repointing a connector at a narrower account cannot hand it to
        every binding already on the connector. A binding with no person
        behind it (a surface) gets POD-visible accounts only.
        """
        if pod_account.visibility is PodAccountVisibility.POD:
            return
        if on_behalf_of is not None and (
            on_behalf_of == pod_account.owner_user_id
            or await (await self.person_context(on_behalf_of, pod_account.pod_id)).can(
                Permissions.CONNECTOR_ACCOUNT_USE,
                ResourceRef.connector_account(pod_account.pod_id, pod_account.id),
            )
        ):
            return
        raise needs_account(pod_connector)

    # -- helpers --------------------------------------------------------------

    async def _installation(
        self, pod_connector: PodConnectorEntity
    ) -> AuthConfigEntity:
        auth_config = await self.auth_configs.get(pod_connector.auth_config_id)
        if auth_config is None or auth_config.status is AuthConfigStatus.DISABLED:
            raise ConnectorUnavailableError(slug=pod_connector.slug)
        return auth_config

    async def _per_member_for(
        self, pod_id: UUID, auth_config_id: UUID
    ) -> PodConnectorEntity | None:
        for found in await self.pod_connectors.list_for_installation(
            pod_id, auth_config_id
        ):
            if found.mode is PodConnectorMode.PER_MEMBER:
                return found
        return None

    async def _shared_running_as(
        self, ctx: Context, shared: list[PodConnectorEntity], account_id: UUID
    ) -> ResolvedPodConnector:
        """The SHARED connector running as the account somebody else named."""
        for pod_connector in shared:
            if pod_connector.shared_pod_account_id is None:
                continue
            pod_account = await self.pod_accounts.get(
                pod_connector.shared_pod_account_id
            )
            if pod_account is not None and pod_account.account_id == account_id:
                return await self.resolve(ctx, pod_connector)
        raise _not_served(shared)

    async def _first_shared(
        self, ctx: Context, shared: list[PodConnectorEntity]
    ) -> ResolvedPodConnector:
        """The first SHARED connector this caller may use, by slug."""
        first_refusal: DomainError | None = None
        for pod_connector in shared:
            try:
                return await self.resolve(ctx, pod_connector)
            except _UNSERVED as refusal:
                first_refusal = first_refusal or refusal
        if first_refusal is None:
            raise _not_served(shared)
        raise first_refusal


#: Slug collisions survived before a declaration gives up with the conflict.
_DECLARE_ATTEMPTS = 3


def free_slug(auth_config: AuthConfigEntity, taken: set[str]) -> str:
    """A slug for a connector on ``auth_config``: its name, made slug-shaped,
    then ``-2``, ``-3`` while taken."""
    base = re.sub(r"[^a-z0-9_-]+", "-", auth_config.name.lower()).strip("-_")[:50]
    if not SLUG_PATTERN.match(base):
        base = (
            re.sub(r"[^a-z0-9_-]+", "-", auth_config.connector_id.lower()).strip("-_")[
                :50
            ]
            or "connector"
        )
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _not_served(declared: list[PodConnectorEntity]) -> ConnectorAccessDeniedError:
    slugs = [pc.slug for pc in declared]
    return ConnectorAccessDeniedError(
        "That account is not one this pod's connectors let you use. "
        f"Call the pod connector instead: {', '.join(slugs) or 'none declared'}.",
        details={
            "reason": "account_not_served_by_pod_connector",
            "pod_connectors": slugs,
        },
    )


async def _require_account_use(ctx: Context, pod_account: PodAccountEntity) -> None:
    """The account half of a SHARED call made now.

    A named agent or function is checked as the person behind it, not as the
    intersection: its grant is on the pod connector, never on an account (an
    agent is granted ``support-inbox``, and which account that means was
    decided by its owner). Asking the workload for a grant on the account too
    would bring back exactly the pinning this replaced.

    A run with no person behind it may use what its owner shared with the
    whole pod, and nothing narrower: there is nobody to check a RESTRICTED
    grant against.
    """
    ref = ResourceRef.connector_account(pod_account.pod_id, pod_account.id)
    asking = ctx
    if ctx.is_named_workload:
        asking = invoking_person_context(ctx.authorizer, ctx)
        if asking is None:
            raise _restricted_without_person()
    elif isinstance(ctx.actor, Workload):
        if pod_account.visibility is PodAccountVisibility.POD:
            return
        raise _restricted_without_person()
    try:
        await asking.require(Permissions.CONNECTOR_ACCOUNT_USE, ref)
    except DomainError as refusal:
        if refusal.code in _NOT_A_REFUSAL:
            raise
        # Typed, so the deprecated route can step past it by kind; the
        # permission it names is kept for whoever renders the refusal.
        details = refusal.details if isinstance(refusal.details, dict) else {}
        raise ConnectorAccessDeniedError(
            refusal.message,
            details={**details, "reason": "account_not_shared_with_you"},
        ) from refusal


async def may_use_shared_account(ctx: Context, pod_account: PodAccountEntity) -> bool:
    """Whether a call made now by ``ctx`` could run as this shared account --
    the same question ``_require_account_use`` asks, answered as a bool."""
    try:
        await _require_account_use(ctx, pod_account)
    except ConnectorAccessDeniedError:
        return False
    return True


def reauth_required_for(
    ctx: Context, pod_connector: PodConnectorEntity, resolved: ResolvedPodConnector
) -> ReauthRequiredError:
    """A marked account resolution let through, whose refresh then confirmed
    the grant is gone: the answer resolution gives an account it refuses
    outright, with the pod's reconnect link rather than the bare account one."""
    return ReauthRequiredError(
        slug=pod_connector.slug,
        connector_id=resolved.auth_config.connector_id,
        connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
        acting_person_is_owner=(
            acting_person(ctx) == resolved.pod_account.owner_user_id
        ),
    )


def _restricted_without_person() -> ConnectorAccessDeniedError:
    return ConnectorAccessDeniedError(
        "This shared account is restricted to some members, and this run "
        "has no member behind it.",
        details={"reason": "restricted_account_without_person"},
    )


__all__ = [
    "PersonContext",
    "PodConnectorResolver",
    "ResolvedPodConnector",
    "acting_person",
    "connect_url",
    "free_slug",
    "may_use_shared_account",
    "needs_account",
    "reauth_required_for",
]
