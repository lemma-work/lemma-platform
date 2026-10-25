"""Pod connectors, for the modules that run things through them.

Surfaces, schedules, the sandbox's git bridge and pod bundle publishing each
name a pod connector by its slug and ask here for the account behind it. None
of them names an account: which account a pod connector means is decided by
its mode and by the account owner's sharing, in
`services/pod_connector_resolution.py`, and nowhere else.

Two ways to ask:

- ``resolve_pod_connector(uow, ctx, ...)`` -- somebody is acting now. The same
  checks as the execute route: ``connector.use`` on the connector, the account
  half for a SHARED one, the acting person's own account for a PER_MEMBER one.
- ``resolve_standing_pod_connector(uow, ...)`` -- a binding a person made
  earlier, running with nobody at the keyboard (a surface answering a message,
  a trigger subscription renewing). It re-reads the binding's standing on
  every use, so withdrawing a shared account stops it at once.

Both raise the typed errors in `domain/pod_connector_errors.py`; callers pass
them on as data rather than translating them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorSource,
)
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectionRequiredError,
    ConnectionRequiresPersonError,
    ConnectorNeedsAccountError,
    ConnectorUnavailableError,
    ConnectorUseRefusedError,
    PodConnectorNotFoundError,
    ReauthRequiredError,
)
from app.modules.connectors.domain.errors import (
    AccountNotFoundError,
    ConnectorAccessDeniedError,
    ConnectorDomainError,
)
from app.modules.connectors.services.pod_connector_resolution import (
    PodConnectorResolver,
    ResolvedPodConnector,
    acting_person,
    free_slug,
)


@dataclass(frozen=True, slots=True)
class PodConnectorAccount:
    """A pod connector and the account it resolved to, for one use."""

    pod_id: UUID
    slug: str
    mode: PodConnectorMode
    pod_account_id: UUID
    owner_user_id: UUID
    auth_config_id: UUID
    connector_id: str
    #: The account itself, credentials included: the caller is about to use
    #: them. Secrets on it stay ``SecretStr`` until the point of use.
    account: AccountEntity
    #: The pod account's owner allows its credential inside sandboxes.
    allow_sandbox_use: bool = False

    @property
    def account_id(self) -> UUID:
        return self.account.id

    @property
    def pinned_pod_account_id(self) -> UUID | None:
        """What a standing binding made on this resolution should pin.

        The pod account for a PER_MEMBER connector -- that one of its person's
        accounts, not whichever is their default later. Nothing for a SHARED
        one: a binding to it follows the connector, re-checked on every use.
        """
        return self.pod_account_id if self.mode is PodConnectorMode.PER_MEMBER else None


@dataclass(frozen=True, slots=True)
class PodConnectorBinding:
    """What a standing binding (a schedule, a surface) stores for a connector."""

    slug: str
    #: The pod account it pins: a PER_MEMBER binding on one of its person's
    #: accounts. ``None`` for SHARED, which follows its connector.
    pod_account_id: UUID | None = None


def _resolver(uow: SqlAlchemyUnitOfWork) -> PodConnectorResolver:
    # Imported here, not at module scope: building it reaches the connectors
    # repositories, which a contract importer should not pay for until it
    # resolves something.
    from app.modules.connectors.services.pod_connector_composition import (
        build_pod_connector_resolver,
    )

    return build_pod_connector_resolver(uow)


def _present(resolved: ResolvedPodConnector) -> PodConnectorAccount:
    return PodConnectorAccount(
        pod_id=resolved.pod_connector.pod_id,
        slug=resolved.pod_connector.slug,
        mode=resolved.pod_connector.mode,
        pod_account_id=resolved.pod_account.id,
        owner_user_id=resolved.pod_account.owner_user_id,
        auth_config_id=resolved.auth_config.id,
        connector_id=resolved.auth_config.connector_id,
        allow_sandbox_use=resolved.pod_account.allow_sandbox_use,
        account=resolved.account,
    )


async def resolve_pod_connector(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    slug: str,
    pod_account_id: UUID | None = None,
) -> PodConnectorAccount:
    """The account ``slug`` runs as for whoever ``ctx`` is, authorized.

    ``pod_account_id`` is a binding's pinned account on a PER_MEMBER
    connector, used while it is still the acting person's own.
    """
    resolver = _resolver(uow)
    connector = await resolver.connector(pod_id, slug)
    return _present(
        await resolver.resolve(ctx, connector, pod_account_id=pod_account_id)
    )


async def ensure_pod_connector_for_installation(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    auth_config_id: UUID,
    source: PodConnectorSource = PodConnectorSource.AUTO,
) -> PodConnectorEntity:
    """The pod's PER_MEMBER connector for an installation, declared if none.

    The one place a connector is declared on first use: the agent tools, the
    deprecated execute, deprecated ``account_id`` fields, bundle import and
    the CLI fallback all come here. Declared (slug from the installation name,
    ``source`` AUTO) only when the acting person has an account on the
    installation, or the caller may declare connectors (an editor importing a
    bundle); otherwise ``PodConnectorNotFoundError``. Declaring grants
    nothing: a named agent still needs ``connector.use`` on it.
    """
    resolver = _resolver(uow)
    auth_config = await resolver.auth_configs.get(auth_config_id)
    if auth_config is None:
        raise PodConnectorNotFoundError(str(auth_config_id))
    return await resolver.ensure_for_installation(
        ctx, pod_id=pod_id, auth_config=auth_config, source=source
    )


async def pod_connector_for_account(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    account_id: UUID,
    mode: PodConnectorMode | None = None,
) -> PodConnectorBinding:
    """The pod connector, and the account to pin, a deprecated ``account_id``
    means.

    For request fields that used to take an account: still accepted, and
    translated here into the connector that serves that account to this
    caller. The caller's own account goes through a PER_MEMBER connector
    (declared on this first use when the pod has none) and is pinned, so a
    schedule on bot B stays on bot B; somebody else's only through the SHARED
    connector its owner shared it as. Nothing a slug could not reach is
    reachable this way.

    ``mode=SHARED`` is for a binding that runs with nobody behind it (a
    surface): the caller's own account is then shared with the pod as a
    SHARED connector, as binding it did before pod connectors -- their act of
    binding their own bot is the consent.
    """
    resolver = _resolver(uow)
    account = await resolver.accounts.get(account_id)
    auth_config = (
        await resolver.auth_configs.get(account.auth_config_id) if account else None
    )
    if account is None or auth_config is None:
        raise AccountNotFoundError(str(account_id))
    if mode is not PodConnectorMode.SHARED:
        resolved = _present(
            await resolver.serve_installation(
                ctx, pod_id=pod_id, auth_config=auth_config, account_id=account_id
            )
        )
        return PodConnectorBinding(
            slug=resolved.slug, pod_account_id=resolved.pinned_pod_account_id
        )
    for connector in await resolver.pod_connectors.list_for_installation(
        pod_id, auth_config.id
    ):
        if connector.mode is not PodConnectorMode.SHARED:
            continue
        shared = (
            await resolver.pod_accounts.get(connector.shared_pod_account_id)
            if connector.shared_pod_account_id is not None
            else None
        )
        if shared is not None and shared.account_id == account_id:
            await resolver.resolve(ctx, connector)
            return PodConnectorBinding(slug=connector.slug)
    if account.user_id != acting_person(ctx):
        raise ConnectorAccessDeniedError(
            "That account is not shared with this pod. Its owner has to share "
            "it, or bind it themselves.",
            details={"reason": "account_not_served_by_pod_connector"},
        )
    slug = await share_own_account_as_pod_connector(
        uow,
        pod_id=pod_id,
        account_id=account_id,
        owner_user_id=account.user_id,
        slug_base=free_slug(auth_config, set()),
    )
    await resolver.resolve(ctx, await resolver.connector(pod_id, slug))
    return PodConnectorBinding(slug=slug)


async def shared_connector_for(
    uow: SqlAlchemyUnitOfWork, ctx: Context, *, pod_id: UUID, account_id: UUID
) -> str:
    """The slug of the SHARED connector a surface's deprecated ``account_id``
    means: a surface runs with nobody behind it (``pod_connector_for_account``
    with ``mode=SHARED``)."""
    binding = await pod_connector_for_account(
        uow, ctx, pod_id=pod_id, account_id=account_id, mode=PodConnectorMode.SHARED
    )
    return binding.slug


#: Standing resolutions already made in this scope, and their refusals.
_STANDING_ONCE: ContextVar[
    dict[tuple[UUID, str, UUID | None, UUID | None], object] | None
] = ContextVar("standing_pod_connector_resolutions", default=None)


@contextmanager
def standing_resolutions_once() -> Iterator[None]:
    """Resolve each standing binding once for the length of this block.

    For one inbound message or one webhook delivery, whose steps -- verifying
    it, finding credentials, the identity it binds to, each matched schedule
    -- each ask what the same connector runs as. The answer cannot change in
    the milliseconds a delivery takes, and asking it again cost four or five
    queries each time. A refusal is remembered too. Nothing outlives the
    block: it is not a cache, and the next delivery asks afresh.
    """
    token = _STANDING_ONCE.set(_STANDING_ONCE.get() or {})
    try:
        yield
    finally:
        _STANDING_ONCE.reset(token)


async def resolve_standing_pod_connector(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    slug: str,
    on_behalf_of: UUID | None = None,
    pod_account_id: UUID | None = None,
    record_use: bool = True,
) -> PodConnectorAccount:
    """The account a standing binding to ``slug`` runs as right now.

    ``on_behalf_of`` is the person the binding runs for (a schedule's owner);
    ``pod_account_id`` the account it pinned. ``record_use=False`` reads it
    with no write: for a caller on a request path that releases its
    connection between reads (a surface answering a webhook inside a
    provider's deadline), where stamping ``last_used_at`` would hold the
    connection across the provider call. Inside
    ``standing_resolutions_once`` each binding is resolved once.
    """
    memo = _STANDING_ONCE.get()
    key = (pod_id, slug, on_behalf_of, pod_account_id)
    known = memo.get(key) if memo is not None else None
    if isinstance(known, ConnectorDomainError):
        raise known
    if isinstance(known, PodConnectorAccount):
        return known
    resolver = _resolver(uow)
    try:
        connector = await resolver.connector(pod_id, slug)
        resolved = _present(
            await resolver.resolve_standing(
                connector,
                on_behalf_of=on_behalf_of,
                pod_account_id=pod_account_id,
                record_use=record_use,
            )
        )
    except ConnectorDomainError as refusal:
        if memo is not None:
            memo[key] = refusal
        raise
    if memo is not None:
        memo[key] = resolved
    return resolved


async def get_pod_connector(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, slug: str
) -> PodConnectorEntity | None:
    """The declaration alone, with no account resolved and nothing checked."""
    from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
        PodConnectorRepository,
    )

    return await PodConnectorRepository(uow).get_by_slug(pod_id, slug)


async def share_own_account_as_pod_connector(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    account_id: UUID,
    owner_user_id: UUID,
    slug_base: str,
    purpose: str | None = None,
) -> str:
    """Share an account its owner just connected *for this pod*, as a connector.

    For the flows where the owner's own action in the pod is what made the
    account -- Lemma creating a bot for a surface the person is setting up.
    That action is the consent sharing needs; nothing else may call this on
    somebody else's account, which is why it takes the owner and refuses an
    account that is not theirs.

    Returns the slug of the SHARED connector that runs as the account: the one
    already pointing at it, or a new ``slug_base`` (``-2``, ``-3`` when taken).
    """
    resolver = _resolver(uow)
    account = await resolver.accounts.get(account_id)
    if account is None or account.user_id != owner_user_id:
        raise AccountNotFoundError(str(account_id))
    pod_accounts = resolver.pod_accounts
    pod_connectors = resolver.pod_connectors
    pod_account = await pod_accounts.add(
        PodAccountEntity(
            pod_id=pod_id,
            account_id=account.id,
            auth_config_id=account.auth_config_id,
            owner_user_id=owner_user_id,
            visibility=PodAccountVisibility.POD,
            source=PodAccountSource.MANUAL,
        )
    )
    if not pod_account.is_shared:
        pod_account = await pod_accounts.set_visibility(
            pod_account.id, PodAccountVisibility.POD
        )
    for existing in await pod_connectors.list_by_shared_accounts([pod_account.id]):
        return existing.slug
    taken = {c.slug for c in await pod_connectors.list_by_pod(pod_id)}
    slug = slug_base
    n = 2
    while slug in taken:
        slug = f"{slug_base}-{n}"
        n += 1
    created = await pod_connectors.create(
        PodConnectorEntity(
            pod_id=pod_id,
            slug=slug,
            auth_config_id=account.auth_config_id,
            mode=PodConnectorMode.SHARED,
            shared_pod_account_id=pod_account.id,
            purpose=purpose,
            created_by_user_id=owner_user_id,
        )
    )
    return created.slug


async def shared_connectors_running_as(
    uow: SqlAlchemyUnitOfWork, account_ids: list[UUID]
) -> dict[UUID, list[tuple[UUID, str]]]:
    """``(pod_id, slug)`` of every SHARED connector currently running as each
    account, keyed by account id.

    For the questions a binding by slug asks about an account rather than a
    connector: is this bot already bound somewhere, does anything still use
    this grant. Only connectors whose account is shared right now count; one
    whose owner withdrew it runs as nothing.
    """
    from sqlalchemy import select

    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodAccount,
        PodConnector,
    )

    if not account_ids:
        return {}
    rows = await uow.session.execute(
        select(PodAccount.account_id, PodConnector.pod_id, PodConnector.slug)
        .join(PodAccount, PodAccount.id == PodConnector.shared_pod_account_id)
        .where(
            PodAccount.account_id.in_(account_ids),
            PodAccount.visibility != PodAccountVisibility.PERSONAL.value,
            PodConnector.mode == PodConnectorMode.SHARED.value,
        )
    )
    found: dict[UUID, list[tuple[UUID, str]]] = {}
    for account_id, pod_id, slug in rows.all():
        found.setdefault(account_id, []).append((pod_id, slug))
    return found


async def shared_account_ids(
    uow: SqlAlchemyUnitOfWork, refs: list[tuple[UUID, str]]
) -> dict[tuple[UUID, str], UUID]:
    """The account each ``(pod_id, slug)`` SHARED connector runs as right now.

    A read for listings and routing narrowing, with no side effect and no
    error: a reference that resolves to nothing (no such connector, not
    SHARED, account withdrawn) is simply absent. Anything about to *use* the
    account goes through ``resolve_standing_pod_connector`` instead, which
    says why it cannot.
    """
    from sqlalchemy import select, tuple_

    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodAccount,
        PodConnector,
    )

    if not refs:
        return {}
    rows = await uow.session.execute(
        select(PodConnector.pod_id, PodConnector.slug, PodAccount.account_id)
        .join(PodAccount, PodAccount.id == PodConnector.shared_pod_account_id)
        .where(
            tuple_(PodConnector.pod_id, PodConnector.slug).in_(list(set(refs))),
            PodAccount.visibility != PodAccountVisibility.PERSONAL.value,
            PodConnector.mode == PodConnectorMode.SHARED.value,
        )
    )
    return {(pod_id, slug): account_id for pod_id, slug, account_id in rows.all()}


#: What every refusal above is an instance of, for a caller that treats "this
#: connector runs as nothing right now" as one case.
PodConnectorRefusal = ConnectorDomainError

__all__ = [
    "ConnectorDomainError",
    "ConnectorUseRefusedError",
    "PodConnectorBinding",
    "PodConnectorSource",
    "ensure_pod_connector_for_installation",
    "pod_connector_for_account",
    "standing_resolutions_once",
    "ConnectionRequiredError",
    "ConnectionRequiresPersonError",
    "ConnectorNeedsAccountError",
    "ConnectorUnavailableError",
    "PodConnectorAccount",
    "PodConnectorMode",
    "PodConnectorNotFoundError",
    "PodConnectorRefusal",
    "ReauthRequiredError",
    "acting_person",
    "get_pod_connector",
    "resolve_pod_connector",
    "resolve_standing_pod_connector",
    "shared_connector_for",
    "share_own_account_as_pod_connector",
    "shared_account_ids",
    "shared_connectors_running_as",
]
