"""Which pod a link spelled in names means, for whoever opened it.

The rules -- what word a name makes, and who may be told which pod has it --
are in ``domain.pod_addresses``. This reads what they need and applies them,
and answers every name it may not reveal exactly as a name nobody has.
"""

from __future__ import annotations

from typing import Protocol, Sequence
from uuid import UUID

from app.modules.identity.contracts import OrganizationRole
from app.modules.pod.domain.errors import PodNotFoundError
from app.modules.pod.domain.pod_addresses import NamedPod, findable_by_name


class StandingAtHandle(Protocol):
    """Where one person stands with the organization a handle names."""

    @property
    def organization_id(self) -> UUID: ...

    @property
    def member_id(self) -> UUID | None: ...

    @property
    def role(self) -> OrganizationRole | None: ...

    @property
    def may_join(self) -> bool: ...


class OrganizationsByHandlePort(Protocol):
    async def at_handle(
        self, handle: str, user_id: UUID
    ) -> StandingAtHandle | None: ...


class NamedPodsPort(Protocol):
    #: Two at most of the organization's live pods whose names make ``word``.
    async def called(self, organization_id: UUID, word: str) -> Sequence[NamedPod]: ...

    async def has_member(self, pod_id: UUID, organization_member_id: UUID) -> bool: ...


class PodAddressService:
    def __init__(
        self,
        organizations: OrganizationsByHandlePort,
        named_pods: NamedPodsPort,
    ) -> None:
        self._organizations = organizations
        self._named_pods = named_pods

    async def find(self, handle: str, word: str, user_id: UUID) -> UUID:
        """The id of the pod ``/l/{handle}/{word}`` means, to this person.

        Raises ``PodNotFoundError`` -- one answer for an organization that is
        not there, a word no pod has, a word two pods have, and a pod this
        person may not learn of.
        """
        organization = await self._organizations.at_handle(handle, user_id)
        if organization is None or not word:
            raise PodNotFoundError()
        called = await self._named_pods.called(organization.organization_id, word)
        if len(called) != 1:
            raise PodNotFoundError()
        pod = called[0]
        in_pod = (
            organization.member_id is not None
            and await self._named_pods.has_member(pod.id, organization.member_id)
        )
        if not findable_by_name(
            join_policy=pod.join_policy,
            in_pod=in_pod,
            org_role=organization.role,
            may_join_org=organization.may_join,
        ):
            raise PodNotFoundError()
        return pod.id
