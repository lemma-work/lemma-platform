"""A GitHub App installation, bound to each Lemma organization that proved it.

Audit H4. GitHub names an installation by a bare number and one App serves
every organization that installs it, so the number alone says nothing about
which Lemma tenant is entitled to it. ``verify_installation`` proves the
claimant's GitHub user can *see* the installation -- which a contractor with
read on one repository can -- and that used to be the whole check. The account
that carried the number then got the App's token on every repository in the
installation, and the installation's webhooks ran its organization's schedules.

So an installation is bound to organizations (``connector_installations``,
with ``connector_id = 'github'``, one row per organization), and everything
that acts on one looks its own organization's row up:

- **claiming** -- the setup callback, reconciling an account, binding one by
  hand -- accepts an installation already bound to the claimant's
  organization, and binds it to one that does not hold it only with proof from
  GitHub that the claimant controls it (``github_installation.administers``):
  the installation is on their own GitHub user, or on a GitHub organization
  they are an admin of, and they may install apps for the Lemma organization.
  Another organization's row is never evidence: each is earned on its own
  claim. The callback's organization and person come from its connect request:
  a 256-bit, single-use ``state`` stored server-side with both, completed only
  in that person's browser (H2). A forged state claims nothing; another
  organization's is refused before anything is exchanged.
- **minting** an App token, and **provisioning** a schedule's routing key, use
  an account's installation only while it is bound to the account's own
  organization (``routable_external_ref``);
- **webhooks** route a delivery only to schedules in the organizations its
  installation is bound to (``bound_organizations``), and drop an unbound one.

Several organizations, not one: GitHub allows one installation of an App per
GitHub account, so a GitHub organization worked on from two Lemma
organizations is one installation, and a rule of one holder let only one of
them connect at all. Each still needs its own GitHub admin's proof.

An organization can let go of an installation it holds
(``release_for_organization``). The row stays, marked released, and stops
counting everywhere a binding counts; it is kept so that the next read of an
account does not quietly bind the installation again on the same person's
proof. Taking it back is an explicit claim -- picking it on an account, or
connecting through it -- which clears the mark.

There is no unbound path. Migration 0063 bound every existing installation
whose organization could be derived and unbound the accounts of the rest. A
derived binding is *unproven* (``proven_at`` NULL): it keeps working for the
organization holding it, but nobody showed GitHub's proof for it, so the bot
(APP mode) never acts on it. The holder's own next proven claim stamps it.
"""

from __future__ import annotations

from uuid import UUID, uuid7

from datetime import UTC, datetime

from sqlalchemy import String, delete, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.connectors.domain.account import AccountEntity
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationNotAdministeredError,
    InstallationClaimNotPermittedError,
)
from app.modules.connectors.infrastructure.adapters.organization_access import (
    SqlAlchemyOrganizationAccessAdapter,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.connector_installation import (
    ConnectorInstallation,
)

logger = get_logger(__name__)

GITHUB = "github"


def is_github(connector_id: str | None) -> bool:
    return (connector_id or "").strip().lower() == GITHUB


def _row(installation_id: str, organization_id: UUID):
    return (
        ConnectorInstallation.connector_id == GITHUB,
        ConnectorInstallation.external_id == str(installation_id),
        ConnectorInstallation.organization_id == organization_id,
    )


#: The key on a row's ``details`` saying its organization let go of it.
RELEASED = "released_at"


def held():
    """A row that stands: not one its organization let go of. Every read of
    who holds an installation filters on this."""
    return ~ConnectorInstallation.details.has_key(RELEASED)


async def is_bound_to(
    session: AsyncSession, installation_id: str, organization_id: UUID
) -> bool:
    """Whether this organization holds the installation, proven or not."""
    return (
        await session.scalar(
            select(ConnectorInstallation.id).where(
                *_row(installation_id, organization_id), held()
            )
        )
        is not None
    )


async def was_released(
    session: AsyncSession, installation_id: str, organization_id: UUID
) -> bool:
    """Whether this organization let go of the installation, and has not
    taken it back."""
    return (
        await session.scalar(
            select(ConnectorInstallation.id).where(
                *_row(installation_id, organization_id), ~held()
            )
        )
        is not None
    )


#: Bindings read per page when routing a delivery.
_ORGANIZATIONS_PAGE = 100


async def bound_organizations(
    session: AsyncSession, installation_id: str
) -> frozenset[UUID]:
    """Every organization this installation is bound to; empty if none.

    Paged, never capped: each row was earned on its own GitHub admin's
    proof, and one left out would silently stop hearing deliveries it is
    entitled to. In practice one page -- a binding per Lemma organization a
    GitHub organization's admins work in.
    """
    found: list[UUID] = []
    after: tuple[object, UUID] | None = None
    while True:
        query = (
            select(
                ConnectorInstallation.organization_id,
                ConnectorInstallation.created_at,
                ConnectorInstallation.id,
            )
            .where(
                ConnectorInstallation.connector_id == GITHUB,
                ConnectorInstallation.external_id == str(installation_id),
                held(),
            )
            .order_by(ConnectorInstallation.created_at, ConnectorInstallation.id)
            .limit(_ORGANIZATIONS_PAGE)
        )
        if after is not None:
            query = query.where(
                tuple_(ConnectorInstallation.created_at, ConnectorInstallation.id)
                > tuple_(literal(after[0]), literal(after[1]))
            )
        rows = (await session.execute(query)).all()
        found.extend(row.organization_id for row in rows)
        if len(rows) < _ORGANIZATIONS_PAGE:
            return frozenset(found)
        after = (rows[-1].created_at, rows[-1].id)


#: Installations one organization is read for. One per GitHub account its
#: people work in -- their own, their GitHub organizations -- which is a
#: handful; the bound keeps a picker from walking past what anyone uses.
_MAX_BOUND_INSTALLATIONS = 50


async def bound_installations(
    session: AsyncSession, organization_id: UUID
) -> tuple[str, ...]:
    """Every installation bound to this organization, oldest binding first.

    What a person in it may open a conversation in is drawn from these: an
    organization connected through more than one GitHub account -- someone's
    own, and the GitHub organization they work in -- reaches the repositories
    of each, and never an installation it has not bound itself.
    """
    rows = await session.scalars(
        select(ConnectorInstallation.external_id)
        .where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.organization_id == organization_id,
            held(),
        )
        .order_by(ConnectorInstallation.created_at, ConnectorInstallation.id)
        .limit(_MAX_BOUND_INSTALLATIONS)
    )
    return tuple(str(one) for one in rows.all())


async def held_proven_by(
    session: AsyncSession, installation_id: str, organization_id: UUID
) -> bool:
    """Whether this organization holds the installation on a proven claim.

    Anything else -- not held, or held unproven -- is a claim that GitHub's
    proof can still change, so a caller about to claim asks for it.
    """
    proven_at = await session.scalar(
        select(ConnectorInstallation.proven_at).where(
            *_row(installation_id, organization_id), held()
        )
    )
    return proven_at is not None


async def _may_install(
    session: AsyncSession, user_id: UUID | None, organization_id: UUID
) -> bool:
    """Whether the claimant may install apps for the organization in Lemma."""
    if user_id is None:
        return False
    access = SqlAlchemyOrganizationAccessAdapter(SqlAlchemyUnitOfWork(session))
    return await access.user_holds_organization_permission(
        user_id, organization_id, Permissions.ORG_INSTALLATION_MANAGE
    )


async def claim_installation(
    session: AsyncSession,
    *,
    installation_id: str,
    organization_id: UUID,
    user_id: UUID | None,
    administered: bool,
) -> None:
    """Hold an installation for this organization, or refuse.

    Already this organization's: nothing to do, and no proof needed -- anyone
    in it may connect through an installation it holds; a proven claim stamps
    a binding the migration derived. Not this organization's -- whether or not
    another holds it -- bound here only if ``administered`` (GitHub said the
    claimant controls it) and the claimant may install apps for the
    organization in Lemma (``org.installation.manage``). What another
    organization holds is neither a reason to refuse nor a reason to accept:
    its row is its own, and this claim neither moves nor reads it.

    One this organization let go of is not held: taking it back needs the
    same proof as binding it the first time, and clears the release.
    """
    row = (
        await session.execute(
            select(
                ConnectorInstallation.proven_at.is_not(None).label("proven"),
                held().label("standing"),
            ).where(*_row(installation_id, organization_id))
        )
    ).first()
    if row is not None and row.standing:
        if (
            not row.proven
            and administered
            and await _may_install(session, user_id, organization_id)
        ):
            await _stamp(session, installation_id, organization_id)
        return
    if not administered:
        logger.warning(
            "connectors.github_bindings.claim_unproven.denied",
            organization_id=str(organization_id),
        )
        raise GithubInstallationNotAdministeredError()
    if not await _may_install(session, user_id, organization_id):
        logger.warning(
            "connectors.github_bindings.claim_not_permitted.denied",
            organization_id=str(organization_id),
        )
        raise InstallationClaimNotPermittedError()
    if row is not None:
        await session.execute(
            update(ConnectorInstallation)
            .where(*_row(installation_id, organization_id))
            .values(
                proven_at=func.now(),
                bound_by_user_id=user_id,
                details=ConnectorInstallation.details.op("-", return_type=JSONB)(
                    literal(RELEASED, String)
                ),
            )
        )
        logger.info(
            "connectors.github_bindings.installation_reclaimed",
            organization_id=str(organization_id),
        )
        return
    # Nothing on conflict: a second claim from this organization racing this
    # one bound the same row, and either way the organization holds it.
    inserted = await session.execute(
        insert(ConnectorInstallation)
        .values(
            id=uuid7(),
            connector_id=GITHUB,
            external_id=str(installation_id),
            organization_id=organization_id,
            bound_by_user_id=user_id,
            proven_at=func.now(),
        )
        .on_conflict_do_nothing(
            index_elements=["connector_id", "external_id", "organization_id"]
        )
        .returning(ConnectorInstallation.id)
    )
    if inserted.scalar() is not None:
        logger.info(
            "connectors.github_bindings.installation_bound",
            organization_id=str(organization_id),
        )


async def _stamp(
    session: AsyncSession, installation_id: str, organization_id: UUID
) -> None:
    """The holder proved a binding the migration derived."""
    await session.execute(
        update(ConnectorInstallation)
        .where(
            *_row(installation_id, organization_id),
            ConnectorInstallation.proven_at.is_(None),
        )
        .values(proven_at=func.now())
    )


async def claim_for_account(
    session: AsyncSession,
    account: AccountEntity,
    installation_id: str | None,
    *,
    administered: bool,
) -> None:
    """``claim_installation`` for the organization and person an account is."""
    if not installation_id or not is_github(account.connector_id):
        return
    await claim_installation(
        session,
        installation_id=installation_id,
        organization_id=account.organization_id,
        user_id=account.user_id,
        administered=administered,
    )


async def release_installation(session: AsyncSession, installation_id: str) -> int:
    """Let go of an installation GitHub says was uninstalled, everywhere.

    Only on GitHub's own signed ``installation.deleted``: the installation no
    longer exists upstream, so whoever installs the App again claims it afresh,
    with proof, organization by organization. A suspended one keeps its
    bindings: suspending is undone by unsuspending it, not by a new claim.
    Returns how many organizations held it.
    """
    released = await session.execute(
        delete(ConnectorInstallation)
        .where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.external_id == str(installation_id),
        )
        .returning(ConnectorInstallation.id)
    )
    return len(released.scalars().all())


async def release_for_organization(
    session: AsyncSession, installation_id: str, organization_id: UUID
) -> tuple[UUID, ...] | None:
    """This organization lets go of an installation; the others keep theirs.

    Its accounts naming the installation forget it, so the next read of each
    asks GitHub again instead of resting on a routing key that no longer acts
    on anything. Its schedules routing on it hear nothing from then on: a
    delivery reaches only organizations that hold the installation. The
    caller has checked the person may manage the organization's installations.

    The row is kept, marked released and unproven, rather than deleted: an
    account in the organization that can still see the installation would
    otherwise bind it straight back on its next read, on the proof of the
    very person who let it go. Only an explicit claim takes it back.

    ``None`` when the organization did not hold it; otherwise the accounts
    that forgot it, for the caller to drop what it remembers of each.
    """
    released = await session.execute(
        update(ConnectorInstallation)
        .where(*_row(installation_id, organization_id), held())
        .values(
            proven_at=None,
            details=ConnectorInstallation.details.op("||", return_type=JSONB)(
                literal({RELEASED: datetime.now(UTC).isoformat()}, JSONB)
            ),
        )
        .returning(ConnectorInstallation.id)
    )
    if released.scalar() is None:
        return None
    forgot = await session.execute(
        update(Account)
        .where(
            Account.organization_id == organization_id,
            Account.connector_id == GITHUB,
            Account.external_ref == str(installation_id),
        )
        .values(external_ref=None)
        .returning(Account.id)
    )
    logger.info(
        "connectors.github_bindings.installation_released",
        organization_id=str(organization_id),
    )
    return tuple(forgot.scalars().all())


class InstallationBindings:
    """The binding read and the claim, over one session.

    What the reconciler is handed rather than reaching for module functions,
    so a test can stand in front of the database without patching the
    reconciler's own names.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def holds(self, organization_id: UUID, installation_id: str) -> bool:
        return await is_bound_to(self._session, installation_id, organization_id)

    async def released(self, organization_id: UUID, installation_id: str) -> bool:
        return await was_released(self._session, installation_id, organization_id)

    async def claim(
        self, account: AccountEntity, installation_id: str, *, administered: bool
    ) -> None:
        await claim_for_account(
            self._session, account, installation_id, administered=administered
        )


async def routable_external_ref(
    session: AsyncSession, account: AccountEntity
) -> str | None:
    """The account's upstream tenant, as far as acting on it is concerned.

    For every connector but GitHub this is ``external_ref`` unchanged. For
    GitHub it is the installation only while that installation is bound to the
    account's own organization: an account naming an installation its
    organization does not hold mints no App token for it and provisions no
    routing key on it, whoever else holds it.
    """
    ref = account.external_ref
    if not ref or not is_github(account.connector_id):
        return ref
    if await is_bound_to(session, ref, account.organization_id):
        return str(ref)
    logger.warning(
        "connectors.github_bindings.unbound_installation_ignored.denied",
        account_id=str(account.id),
    )
    return None


__all__ = [
    "InstallationBindings",
    "bound_installations",
    "bound_organizations",
    "claim_for_account",
    "claim_installation",
    "held_proven_by",
    "is_bound_to",
    "is_github",
    "release_for_organization",
    "release_installation",
    "routable_external_ref",
    "was_released",
]
