"""Pod connectors as a pod bundle moves them between organizations.

A bundle carries a pod connector as its catalog connector and its mode
(``connectors/<slug>/<slug>.json``) and nothing of the organization it left:
not the installation it was mapped to, not the account a SHARED one ran as.
Importing one is therefore three questions put to the importer's own org --
which installation, whether one may be made, and (SHARED) which of the
importer's own accounts to share -- and then one declaration through the same
service, and the same checks, as ``POST /pods/{pod}/connectors``.

Everything here answers one of those questions or makes that declaration;
nothing here decides an answer for the importer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.auth_config import (
    AuthConfigSource,
    AuthConfigStatus,
)
from app.modules.connectors.domain.pod_connector import (
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorMode,
    PodConnectorVisibility,
)

if TYPE_CHECKING:
    from app.modules.connectors.services.pod_connector_service import (
        PodConnectorService,
    )


#: The code an import is refused with when it would widen an account the
#: importer shares with only some of the pod.
ACCOUNT_SHARED_WITH_SOME = "POD_BUNDLE_ACCOUNT_SHARED_WITH_SOME"


class AccountSharedWithSomeError(DomainError):
    """An import may not widen a RESTRICTED pod account to the whole pod.

    The importer chose to share, for a bundled SHARED connector, an account
    they already share with only some people here. Widening it hands it to
    everyone else, and putting its list back is not something an import can
    promise -- so it is refused, as lemma-cli's import of the same bundle
    refuses it, and the person widens it themselves if that is what they mean.
    """

    def __init__(self, *, slug: str, account_id: UUID):
        super().__init__(
            f"connector '{slug}': account {account_id} is shared with some "
            "people in this pod. Share it with the whole pod from the "
            "Connections page first, or choose another account.",
            code=ACCOUNT_SHARED_WITH_SOME,
            status_code=409,
            details={"slug": slug, "account_id": str(account_id)},
        )


@dataclass(frozen=True, slots=True)
class InstallationChoice:
    """One active installation of a catalog connector in an organization."""

    auth_config_id: UUID
    name: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class OwnAccountChoice:
    """One of the importer's own accounts they could share for a connector."""

    account_id: UUID
    auth_config_id: UUID
    label: str | None


def _pod_connector_service(uow: SqlAlchemyUnitOfWork) -> PodConnectorService:
    # Imported here: the dependency builders reach the connectors service
    # layer, which a pod bundle importing this contract should not pay for
    # until it declares something.
    from app.modules.connectors.api.dependencies import get_pod_connector_service

    return get_pod_connector_service(uow)


async def exported_pod_connectors(
    uow: SqlAlchemyUnitOfWork, ctx: Context, *, pod_id: UUID
) -> list[dict[str, object]]:
    """The pod's connectors as ``GET /pods/{pod}/connectors`` returns them.

    The response shape, so the bundle normalizer reads the same thing whether
    the CLI (through the SDK) or the backend exporter fed it.
    """
    connectors = await _pod_connector_service(uow).list_connectors(ctx, pod_id)
    return [connector.model_dump(mode="json") for connector in connectors]


@dataclass(frozen=True, slots=True)
class DeclaredPodConnector:
    """What a pod already declares under a slug, as an import compares it."""

    connector_id: str
    mode: str


async def declared_pod_connectors(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> dict[str, DeclaredPodConnector]:
    """Every connector the pod declares: slug -> catalog connector and mode."""
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodConnector,
    )
    from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
        LIST_LIMIT,
    )

    # The same ceiling as every other read of a pod's connectors.
    rows = await uow.session.execute(
        select(PodConnector.slug, AuthConfig.connector_id, PodConnector.mode)
        .join(AuthConfig, AuthConfig.id == PodConnector.auth_config_id)
        .where(PodConnector.pod_id == pod_id)
        .order_by(PodConnector.slug)
        .limit(LIST_LIMIT)
    )
    return {
        slug: DeclaredPodConnector(connector_id=connector_id, mode=str(mode))
        for slug, connector_id, mode in rows.tuples().all()
    }


async def installations_of(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID, connector_ids: Sequence[str]
) -> dict[str, list[InstallationChoice]]:
    """Each catalog connector's active installations in the org, default first."""
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

    if not connector_ids:
        return {}
    rows = await uow.session.execute(
        select(
            AuthConfig.connector_id,
            AuthConfig.id,
            AuthConfig.name,
            AuthConfig.is_default,
        )
        .where(
            AuthConfig.organization_id == organization_id,
            AuthConfig.connector_id.in_(sorted(set(connector_ids))),
            AuthConfig.status == AuthConfigStatus.ACTIVE.value,
        )
        .order_by(
            AuthConfig.connector_id,
            AuthConfig.is_default.desc(),
            AuthConfig.created_at.asc(),
        )
    )
    found: dict[str, list[InstallationChoice]] = {}
    for connector_id, auth_config_id, name, is_default in rows.all():
        found.setdefault(connector_id, []).append(
            InstallationChoice(
                auth_config_id=auth_config_id, name=name, is_default=bool(is_default)
            )
        )
    return found


async def own_accounts_on(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
    auth_config_ids: Sequence[UUID],
) -> list[OwnAccountChoice]:
    """The person's own connected accounts on these installations.

    What an importer may share for a SHARED connector: their own, and only
    their own -- sharing somebody else's credential is that person's act.
    """
    from app.modules.connectors.infrastructure.models.account import Account

    if not auth_config_ids:
        return []
    rows = await uow.session.execute(
        select(Account.id, Account.auth_config_id, Account.display_name, Account.email)
        .where(
            Account.user_id == user_id,
            Account.organization_id == organization_id,
            Account.auth_config_id.in_(list(set(auth_config_ids))),
            Account.status == AccountStatus.CONNECTED.value,
        )
        .order_by(Account.is_default.desc(), Account.created_at.asc())
    )
    return [
        OwnAccountChoice(
            account_id=account_id,
            auth_config_id=auth_config_id,
            label=email or display_name,
        )
        for account_id, auth_config_id, display_name, email in rows.all()
    ]


async def lemma_app_installable(
    uow: SqlAlchemyUnitOfWork, *, connector_id: str
) -> bool:
    """Whether the org could install this connector with Lemma's own app.

    The one installation an import can make on the importer's behalf: it
    needs nothing typed in. A connector whose install needs the org's own
    OAuth client or a server URL is installed on the Connectors page.
    """
    from app.modules.connectors.api.dependencies import get_connector_service
    from app.modules.connectors.services.install_provisioning import (
        resolve_install_kind,
    )

    service = get_connector_service(uow)
    try:
        connector = await service.get_connector(connector_id)
        if not connector.is_active:
            return False
        service._validate_auth_config_request(  # noqa: SLF001 - the same check create runs
            connector=connector,
            kind=resolve_install_kind(connector, None),
            config_source=AuthConfigSource.SYSTEM_DEFAULT,
            provider_config=None,
        )
    except DomainError:
        return False
    return True


async def declare_bundled_pod_connector(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    slug: str,
    auth_config_id: UUID,
    mode: str,
    required: bool,
    purpose: str | None,
    visibility: str,
    share_account_id: UUID | None,
) -> str:
    """Declare one bundled connector in the pod; returns its status.

    ``share_account_id`` is one of the importer's own accounts, shared with the
    pod (POD visibility, source IMPORT) for a SHARED connector to run as.
    ``add_account`` refuses an account that is not the acting person's own, so
    an import can share nobody's credential but its importer's. A SHARED
    connector declared without one is NEEDS_ACCOUNT until somebody shares one.
    """
    from app.core.authorization.context import ResourceRef
    from app.core.authorization.permissions import Permissions
    from app.modules.connectors.api.schemas.pod_connector_schemas import (
        PodConnectorCreateRequest,
    )
    from app.modules.connectors.domain.pod_connector_errors import (
        PodConnectorConflictError,
    )

    service = _pod_connector_service(uow)
    connector_mode = PodConnectorMode(mode)
    # Checked before the account is shared: sharing an account pod-wide for a
    # connector that is then refused would leave it shared for nothing. The
    # step's unit of work rolls back on a later refusal too, but nothing here
    # should rely on reaching that far.
    await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
    if await service.pod_connectors.get_by_slug(pod_id, slug) is not None:
        raise PodConnectorConflictError(slug)
    shared_pod_account_id: UUID | None = None
    if share_account_id is not None and connector_mode is PodConnectorMode.SHARED:
        shared_pod_account_id = await _shared_with_the_pod(
            service, ctx, pod_id=pod_id, slug=slug, account_id=share_account_id
        )
    created = await service.create_connector(
        ctx,
        pod_id,
        PodConnectorCreateRequest(
            slug=slug,
            auth_config_id=auth_config_id,
            mode=connector_mode,
            shared_pod_account_id=shared_pod_account_id,
            required=required,
            purpose=purpose,
            visibility=PodConnectorVisibility(visibility),
        ),
    )
    return created.status.value


async def _shared_with_the_pod(
    service: PodConnectorService,
    ctx: Context,
    *,
    pod_id: UUID,
    slug: str,
    account_id: UUID,
) -> UUID:
    """The importer's own account, shared with the whole pod; its pod account id.

    One already in the pod only for its owner (added on their first use) is
    widened rather than added again, which the pod would refuse as a
    duplicate. One shared with some people is refused
    (:class:`AccountSharedWithSomeError`): the apply request checks it first
    (``restricted_pod_accounts``), and this holds for a share narrowed since.
    ``add_account`` and ``update_account`` both refuse an account that is not
    the acting person's own, so an import shares nobody's credential but its
    importer's.
    """
    existing = await service.pod_accounts.get_by_pod_and_account(
        pod_id=pod_id, account_id=account_id
    )
    if existing is None:
        added = await service.add_account(
            ctx,
            pod_id,
            account_id=account_id,
            visibility=PodAccountVisibility.POD,
            share_with=[],
            source=PodAccountSource.IMPORT,
        )
        return added.id
    if existing.visibility is PodAccountVisibility.POD:
        return existing.id
    if existing.visibility is PodAccountVisibility.RESTRICTED:
        raise AccountSharedWithSomeError(slug=slug, account_id=account_id)
    widened = await service.update_account(
        ctx,
        pod_id,
        existing.id,
        visibility=PodAccountVisibility.POD,
        share_with=None,
        is_member_default=None,
    )
    return widened.id


async def restricted_pod_accounts(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, account_ids: Sequence[UUID]
) -> set[UUID]:
    """Which of these accounts the pod holds shared with only some people."""
    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodAccount,
    )

    if not account_ids:
        return set()
    rows = await uow.session.execute(
        select(PodAccount.account_id).where(
            PodAccount.pod_id == pod_id,
            PodAccount.account_id.in_(sorted(set(account_ids))),
            PodAccount.visibility == PodAccountVisibility.RESTRICTED.value,
        )
    )
    return set(rows.scalars().all())


async def deprecated_account_refusal(
    uow: SqlAlchemyUnitOfWork, ctx: Context, *, pod_id: UUID, account_id: UUID
) -> str | None:
    """Why the importer could not use an account a bundle names, or None.

    For a schedule exported before pod connectors, which names an account
    rather than a pod connector: applying it turns the account into the pod
    connector serving it to the importer (``pod_connector_for_account``),
    which refuses one that is neither theirs nor shared with them here. Asked
    before anything is applied, so that schedule is planned as skipped rather
    than failing the import after earlier steps landed. Reads only: the
    shared case is checked as a standing binding would be, with no use
    recorded.
    """
    from app.modules.connectors.services.pod_connector_composition import (
        build_pod_connector_resolver,
    )
    from app.modules.connectors.services.pod_connector_resolution import (
        acting_person,
    )

    resolver = build_pod_connector_resolver(uow)
    account = await resolver.accounts.get(account_id)
    auth_config = (
        await resolver.auth_configs.get(account.auth_config_id) if account else None
    )
    if (
        account is None
        or auth_config is None
        or (
            ctx.organization_id is not None
            and auth_config.organization_id != ctx.organization_id
        )
    ):
        return "that account does not exist in this organization"
    if auth_config.status is AuthConfigStatus.DISABLED:
        return f"its installation ({auth_config.name}) is disabled"
    person = acting_person(ctx)
    if account.user_id == person:
        return None
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
        if shared is None or shared.account_id != account_id:
            continue
        try:
            await resolver.require_use(ctx, connector, auth_config)
            await resolver.resolve_standing(
                connector, on_behalf_of=person, record_use=False
            )
        except DomainError as refusal:
            return refusal.message
        return None
    return "it is somebody else's account, and it is not shared with you in this pod"


async def ensure_lemma_app_installation(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
    connector_id: str,
) -> UUID:
    """The org's install of Lemma's own app for this connector, made if absent.

    ``own_apps.ensure_system_install``: refused unless the person holds
    ``org.installation.manage``. Commits.
    """
    from app.modules.connectors.contracts.own_apps import ensure_system_install

    return await ensure_system_install(
        uow, user_id=user_id, organization_id=organization_id, connector_id=connector_id
    )


__all__ = [
    "ACCOUNT_SHARED_WITH_SOME",
    "AccountSharedWithSomeError",
    "DeclaredPodConnector",
    "InstallationChoice",
    "OwnAccountChoice",
    "declare_bundled_pod_connector",
    "declared_pod_connectors",
    "deprecated_account_refusal",
    "ensure_lemma_app_installation",
    "exported_pod_connectors",
    "installations_of",
    "lemma_app_installable",
    "own_accounts_on",
    "restricted_pod_accounts",
]
