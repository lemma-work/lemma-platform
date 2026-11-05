"""A GitHub App installation, bound to the one Lemma organization it serves.

Audit H4. GitHub names an installation by a bare number and one App serves
every organization that installs it, so the number alone says nothing about
which Lemma tenant is entitled to it. ``verify_installation`` proves the
claimant's GitHub user can *see* the installation -- which a contractor with
read on one repository can -- and that used to be the whole check. The account
that carried the number then got the App's token on every repository in the
installation, and the installation's webhooks ran its organization's schedules.

So an installation is bound to an organization (``connector_installations``,
with ``connector_id = 'github'``), and everything that acts on one looks the
binding up:

- **claiming** -- the setup callback, reconciling an account, binding one by
  hand -- accepts an installation already bound to the claimant's
  organization, refuses one bound anywhere else, and binds an unbound one only
  with proof from GitHub that the claimant controls it
  (``github_installation.administers``): the installation is on their own
  GitHub user, or on a GitHub organization they are an admin of. The callback's
  organization and person come from its connect request: a 256-bit, single-use
  ``state`` stored server-side with both, completed only in that person's
  browser (H2). A forged state claims nothing; another organization's is
  refused before anything is exchanged.
- **minting** an App token, and **provisioning** a schedule's routing key, use
  an account's installation only while it is bound to the account's own
  organization (``routable_external_ref``);
- **webhooks** route a delivery only to schedules in the organization its
  installation is bound to (``bound_organization``), and drop an unbound one.

There is no unbound path. Migration 0063 binds every existing installation
whose organization can be derived and unbinds the accounts of the rest. A
derived binding is *unproven* (``proven_at`` NULL): it keeps working for the
organization holding it, but nobody showed GitHub's proof for it, so it may be
an installation somebody squatted on before H4. A proven claim from another
organization -- a GitHub admin of the installation who may install apps for
their Lemma organization -- takes such a binding over, and the holder's own
next proven claim stamps it, after which it is as final as any other.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID, uuid7

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.connectors.domain.account import AccountEntity
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationBoundElsewhereError,
    GithubInstallationNotAdministeredError,
    InstallationClaimNotPermittedError,
)
from app.modules.connectors.infrastructure.adapters.organization_access import (
    SqlAlchemyOrganizationAccessAdapter,
)
from app.modules.connectors.infrastructure.models.connector_installation import (
    ConnectorInstallation,
)

logger = get_logger(__name__)

GITHUB = "github"


def is_github(connector_id: str | None) -> bool:
    return (connector_id or "").strip().lower() == GITHUB


@dataclass(frozen=True, slots=True)
class _Binding:
    id: UUID
    organization_id: UUID
    proven: bool


async def _binding(session: AsyncSession, installation_id: str) -> _Binding | None:
    row = (
        await session.execute(
            select(
                ConnectorInstallation.id,
                ConnectorInstallation.organization_id,
                ConnectorInstallation.proven_at,
            ).where(
                ConnectorInstallation.connector_id == GITHUB,
                ConnectorInstallation.external_id == str(installation_id),
            )
        )
    ).first()
    if row is None:
        return None
    return _Binding(row.id, row.organization_id, row.proven_at is not None)


async def bound_organization(
    session: AsyncSession, installation_id: str
) -> UUID | None:
    """The organization this installation is bound to, if any."""
    return await session.scalar(
        select(ConnectorInstallation.organization_id).where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.external_id == str(installation_id),
        )
    )


async def held_proven_by(
    session: AsyncSession, installation_id: str, organization_id: UUID
) -> bool:
    """Whether this organization holds the installation on a proven claim.

    Anything else -- unbound, held unproven, held elsewhere -- is a claim that
    GitHub's proof can still change, so a caller about to claim asks for it.
    """
    binding = await _binding(session, installation_id)
    return (
        binding is not None
        and binding.organization_id == organization_id
        and binding.proven
    )


async def claimable_by(
    session: AsyncSession, organization_id: UUID, installation_ids: Iterable[str]
) -> set[str]:
    """Which of these an organization may bind: its own, unbound ones, and
    ones another organization holds unproven (a proven claim takes those)."""
    wanted = {str(one) for one in installation_ids}
    if not wanted:
        return set()
    rows = await session.execute(
        select(
            ConnectorInstallation.external_id,
            ConnectorInstallation.organization_id,
            ConnectorInstallation.proven_at,
        ).where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.external_id.in_(wanted),
        )
    )
    elsewhere = {
        installation_id
        for installation_id, owner, proven_at in rows.all()
        if owner != organization_id and proven_at is not None
    }
    return wanted - elsewhere


def _refused_elsewhere(organization_id: UUID) -> GithubInstallationBoundElsewhereError:
    logger.warning(
        "connectors.github_bindings.claim_refused.denied",
        organization_id=str(organization_id),
    )
    return GithubInstallationBoundElsewhereError()


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
    a binding the migration derived. Another's on a proven claim: refused.
    Nobody's, or another's on a derived (unproven) binding: bound here only if
    ``administered`` -- GitHub said the claimant controls it -- and the
    claimant may install apps for the organization in Lemma
    (``org.installation.manage``). A new binding is an insert that does
    nothing on conflict followed by a read of the winner, and a takeover an
    update conditional on the binding still being the unproven one read, so
    two organizations racing for it cannot both believe they hold it.
    """
    binding = await _binding(session, installation_id)
    if binding is not None and binding.organization_id == organization_id:
        if not binding.proven and await _proves(
            session, administered, user_id, organization_id
        ):
            await _stamp(session, binding.id)
        return
    if binding is not None and binding.proven:
        raise _refused_elsewhere(organization_id)
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
    if binding is not None:
        await _take_over(session, binding, organization_id, user_id)
        return
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
        .on_conflict_do_nothing(index_elements=["connector_id", "external_id"])
        .returning(ConnectorInstallation.id)
    )
    if inserted.scalar() is not None:
        logger.info(
            "connectors.github_bindings.installation_bound",
            organization_id=str(organization_id),
        )
        return
    if await bound_organization(session, installation_id) != organization_id:
        raise _refused_elsewhere(organization_id)


async def _proves(
    session: AsyncSession,
    administered: bool,
    user_id: UUID | None,
    organization_id: UUID,
) -> bool:
    return administered and await _may_install(session, user_id, organization_id)


async def _stamp(session: AsyncSession, binding_id: UUID) -> None:
    """The holder proved a binding the migration derived: it is final now."""
    await session.execute(
        update(ConnectorInstallation)
        .where(
            ConnectorInstallation.id == binding_id,
            ConnectorInstallation.proven_at.is_(None),
        )
        .values(proven_at=func.now())
    )


async def _take_over(
    session: AsyncSession,
    binding: _Binding,
    organization_id: UUID,
    user_id: UUID | None,
) -> None:
    """Move an unproven binding to the organization that proved it.

    Conditional on the row still being the unproven binding read, so a holder
    stamping it, or a third organization taking it, in between wins and this
    claim is refused. The old holder's accounts keep naming the installation
    but stop acting on it: ``routable_external_ref`` and webhook routing both
    read the binding, which is no longer theirs.
    """
    moved = await session.execute(
        update(ConnectorInstallation)
        .where(
            ConnectorInstallation.id == binding.id,
            ConnectorInstallation.organization_id == binding.organization_id,
            ConnectorInstallation.proven_at.is_(None),
        )
        .values(
            organization_id=organization_id,
            bound_by_user_id=user_id,
            proven_at=func.now(),
        )
        .returning(ConnectorInstallation.id)
    )
    if moved.scalar() is None:
        raise _refused_elsewhere(organization_id)
    logger.warning(
        "connectors.github_bindings.unproven_binding_taken_over.observed",
        organization_id=str(organization_id),
        previous_organization_id=str(binding.organization_id),
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


async def release_installation(session: AsyncSession, installation_id: str) -> bool:
    """Let go of an installation GitHub says was uninstalled.

    Only on GitHub's own signed ``installation.deleted``: the installation no
    longer exists upstream, so whoever installs the App again -- the same
    organization or another -- claims it afresh, with proof. A suspended one
    keeps its binding: suspending is undone by unsuspending it, not by a new
    claim.
    """
    released = await session.execute(
        delete(ConnectorInstallation)
        .where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.external_id == str(installation_id),
        )
        .returning(ConnectorInstallation.id)
    )
    return released.scalar() is not None


class InstallationBindings:
    """The binding reads and the claim, over one session.

    What the reconciler is handed rather than reaching for module functions,
    so a test can stand in front of the database without patching the
    reconciler's own names.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owner(self, installation_id: str) -> UUID | None:
        return await bound_organization(self._session, installation_id)

    async def claimable_by(
        self, organization_id: UUID, installation_ids: Iterable[str]
    ) -> set[str]:
        return await claimable_by(self._session, organization_id, installation_ids)

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
    account's own organization: an account still naming another organization's
    installation mints no App token for it and provisions no routing key on it.
    """
    ref = account.external_ref
    if not ref or not is_github(account.connector_id):
        return ref
    if await bound_organization(session, ref) == account.organization_id:
        return str(ref)
    logger.warning(
        "connectors.github_bindings.unbound_installation_ignored.denied",
        account_id=str(account.id),
    )
    return None


__all__ = [
    "InstallationBindings",
    "bound_organization",
    "claim_for_account",
    "claim_installation",
    "claimable_by",
    "held_proven_by",
    "is_github",
    "release_installation",
    "routable_external_ref",
]
