"""A person deletes their own account: they go, and the work they shared stays.

Deleting an account unlinks the person from what they made rather than wiping
it. Their row becomes a tombstone (``UserEntity.erase``) that keeps its id, so
every message, record, file and pod that names them still resolves -- to
"Deleted user". What they held privately goes: each organization membership,
through the same removal a departing member gets, which takes them out of every
pod and disconnects the connector accounts they own there; and every
organization nobody else is in, with its pods.

Everything is decided before anything is written. An account whose deletion
would strand an organization with no owner, or leave a subscription charging
nobody, is refused whole, with nothing changed.

Signing the person out everywhere and removing their sign-in is not done here:
those are calls to SuperTokens, made by work this transaction commits
(``supertokens_auth/account_removal.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.identity.contracts.account_deletion import AccountDeletionGuard
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.errors import (
    AccountDeletionBlockedByBillingError,
    AccountDeletionConfirmationError,
    AccountDeletionSoleOwnerError,
    AccountDeletionUnavailableError,
    OrganizationNotFoundError,
    UserNotFoundError,
)
from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationStatus,
    OrganizationJoinPolicy,
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.identity.domain.ports import (
    OrganizationRepositoryPort,
    PodMembershipPort,
    UserRepositoryPort,
)
from app.modules.identity.services.pod_departure import leave_every_pod

logger = get_logger(__name__)

#: What an organization that went with its only member is called afterwards.
DELETED_ORGANIZATION_NAME = "Deleted organization"

_INVITATION_PAGE = 100


def _confirms(typed: str, email: str) -> bool:
    """Whether what the person typed is this account's address.

    Anything that is not an address at all -- a "yes", an empty box -- is a
    mismatch like any other, not a validation failure of its own.
    """
    try:
        return normalize_identity_email(typed) == normalize_identity_email(email)
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class AccountDeletionOutcome:
    """What the deletion did, for the response and the audit line."""

    organizations_left: tuple[UUID, ...] = ()
    organizations_deleted: tuple[UUID, ...] = ()
    pods_deleted: int = 0
    already_deleted: bool = False


@dataclass(frozen=True, slots=True)
class _Plan:
    leave: tuple[OrganizationMemberEntity, ...]
    delete: tuple[OrganizationMemberEntity, ...]
    sole_owner_of: tuple[UUID, ...]


class AccountDeletionService:
    def __init__(
        self,
        *,
        user_repository: UserRepositoryPort,
        organization_repository: OrganizationRepositoryPort,
        pod_membership_port: PodMembershipPort,
        billing_guard: AccountDeletionGuard | None,
        deployment_bills: bool,
    ) -> None:
        self.user_repository = user_repository
        self.organization_repository = organization_repository
        self.pod_membership_port = pod_membership_port
        self.billing_guard = billing_guard
        self.deployment_bills = deployment_bills

    async def delete_account(
        self, user_id: UUID, *, confirm_email: str
    ) -> AccountDeletionOutcome:
        user = await self.user_repository.get(user_id)
        if user is None:
            raise UserNotFoundError()
        # Repeating a deletion that already happened is its own answer. Not
        # over HTTP, where the deleted account is refused before the route,
        # but for any caller inside the process. Checked before the
        # confirmation, which a tombstone's address can no longer match.
        if user.is_deleted:
            return AccountDeletionOutcome(already_deleted=True)
        if not _confirms(confirm_email, str(user.email)):
            raise AccountDeletionConfirmationError()

        plan = await self._plan(user_id)
        if plan.sole_owner_of:
            raise AccountDeletionSoleOwnerError(await self._names(plan.sole_owner_of))
        await self._refuse_if_billed(
            user_id, [member.organization_id for member in plan.delete]
        )

        pods_deleted = 0
        for member in plan.delete:
            pods_deleted += await self._delete_organization(member)
        for member in plan.leave:
            await self._leave(member)

        previous_email = str(user.email)
        user.erase()
        await self.user_repository.save_erased(user, previous_email=previous_email)

        outcome = AccountDeletionOutcome(
            organizations_left=tuple(member.organization_id for member in plan.leave),
            organizations_deleted=tuple(
                member.organization_id for member in plan.delete
            ),
            pods_deleted=pods_deleted,
        )
        logger.info(
            "identity.account.deleted",
            user_id=str(user_id),
            organizations_left=len(outcome.organizations_left),
            organizations_deleted=len(outcome.organizations_deleted),
            pods_deleted=pods_deleted,
        )
        return outcome

    async def _plan(self, user_id: UUID) -> _Plan:
        """Sort each membership into leave, delete with the account, or refuse.

        Each organization's seats are locked before its members are counted,
        in organization order so two deletions cannot wait on each other: an
        invitation accepted in between would otherwise turn an organization
        counted as empty into one somebody had just joined.
        """
        leave: list[OrganizationMemberEntity] = []
        delete: list[OrganizationMemberEntity] = []
        sole_owner_of: list[UUID] = []
        for member in await self.organization_repository.list_user_memberships(user_id):
            organization_id = member.organization_id
            await self.organization_repository.lock_seats(organization_id)
            if await self.organization_repository.count_members(organization_id) <= 1:
                delete.append(member)
                continue
            if member.role == OrganizationRole.ORG_OWNER and (
                await self.organization_repository.count_members_with_role_for_update(
                    organization_id, OrganizationRole.ORG_OWNER
                )
                <= 1
            ):
                sole_owner_of.append(organization_id)
                continue
            leave.append(member)
        return _Plan(
            leave=tuple(leave), delete=tuple(delete), sole_owner_of=tuple(sole_owner_of)
        )

    async def _names(self, organization_ids: Sequence[UUID]) -> list[tuple[UUID, str]]:
        found = await self.organization_repository.get_many(organization_ids)
        return [
            (organization_id, found[organization_id].name)
            for organization_id in organization_ids
            if organization_id in found
        ]

    async def _refuse_if_billed(
        self, user_id: UUID, deleted_organization_ids: Sequence[UUID]
    ) -> None:
        if self.billing_guard is None:
            if self.deployment_bills:
                raise AccountDeletionUnavailableError()
            return
        refusal = await self.billing_guard.refusal(
            user_id=user_id, deleted_organization_ids=deleted_organization_ids
        )
        if refusal is not None:
            raise AccountDeletionBlockedByBillingError(
                code=refusal.code, message=refusal.message
            )

    async def _leave(self, member: OrganizationMemberEntity) -> None:
        """The removal any departing member gets: out of each pod, then out."""
        await leave_every_pod(self.pod_membership_port, member)
        await self.organization_repository.delete_member(member.id)

    async def _delete_organization(self, member: OrganizationMemberEntity) -> int:
        """Delete an organization the person is the only member of.

        Its pods go through the pod's own deletion, so their schedules,
        addresses and outside resources are torn down as when anyone deletes a
        pod. The organization row stays, memberless and renamed, because a
        cascade from it would take rows that the pods' cleanup has yet to
        read. Nothing can reach it afterwards: nobody is in it, nobody can join
        it, and nothing it was called survives.
        """
        organization_id = member.organization_id
        organization = await self.organization_repository.get(organization_id)
        if organization is None:
            raise OrganizationNotFoundError()
        if member.role != OrganizationRole.ORG_OWNER:
            # Only reachable for an organization that already lost its owner.
            # Deleting a pod is an owner's to do, and this person is all that
            # is left of the organization.
            member.update_role(OrganizationRole.ORG_OWNER)
            member = await self.organization_repository.update_member(member)
        pods = await self.pod_membership_port.delete_organization_pods(
            organization_id=organization_id, requester_user_id=member.user_id
        )
        await self._revoke_pending_invitations(organization_id)
        await self._leave(member)

        organization.name = DELETED_ORGANIZATION_NAME
        organization.slug = f"deleted-{organization_id.hex}"
        organization.email_domain = None
        organization.join_policy = OrganizationJoinPolicy.INVITE_ONLY
        await self.organization_repository.update(organization)
        return len(pods)

    async def _revoke_pending_invitations(self, organization_id: UUID) -> None:
        # Revoking takes an invitation out of the pending set, so each pass
        # reads the first page again until it comes back empty.
        while True:
            (
                invitations,
                _,
            ) = await self.organization_repository.list_organization_invitations(
                organization_id,
                OrganizationInvitationStatus.PENDING,
                _INVITATION_PAGE,
                None,
            )
            if not invitations:
                return
            for invitation in invitations:
                invitation.mark_revoked()
                await self.organization_repository.update_invitation(invitation)
