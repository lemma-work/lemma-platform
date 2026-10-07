"""Connecting pods: one pod letting another ask it, with nobody present.

The storage is grants (``infrastructure/pod_link_queries``); this is who may
make one and what it may share.

* **Who connects.** An admin of the pod being asked, who is also in the pod that
  will ask. The route checks the first (``pod.member.manage`` on the asked pod:
  letting another pod in is the same call as letting a person in); this checks
  the second, so nobody links a pod they cannot see into one they run.
* **What it shares.** Tables and folders, read-only. A link run reaches this
  pod through the outsider machinery, which writes nothing, so a write grant
  would promise what no run could use.
* **Who looks after it.** Whoever connected it. They own the conversations the
  asks open, get the questions the run passes on, and the link stops working if
  they leave the pod.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import ResourceType
from app.core.authorization.grants import (
    ResourceGrantInputProtocol,
    normalize_pod_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import BadRequestError, DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.pod_link_queries import (
    LinkRow,
    PodLinkQueries,
    SharedResource,
)
from app.modules.identity.contracts.profiles import UserProfileRef, user_profile
from app.modules.pod.contracts.agent_access import pod_organization_id
from app.modules.pod.contracts.members import pod_member_id
from app.modules.pod.contracts.user_pods import AttachablePod, pods_by_ids

#: What a link may share, per kind of resource: reading, and nothing else.
SHAREABLE: dict[ResourceType, frozenset[str]] = {
    ResourceType.DATASTORE_TABLE: frozenset(
        {Permissions.DATASTORE_TABLE_READ, Permissions.DATASTORE_RECORD_READ}
    ),
    ResourceType.FOLDER: frozenset({Permissions.FOLDER_READ}),
    ResourceType.DOCUMENT: frozenset({Permissions.FOLDER_READ}),
}


class LinkRefused(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="pod_link_refused", status_code=409)


@dataclass(frozen=True, slots=True)
class PodLinkView:
    """A link as a page shows it: the other pod, who looks after it, what it shares."""

    pod: AttachablePod
    steward_user_id: UUID | None
    steward_name: str | None
    shared: list[SharedResource]


class PodLinkService:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.links = PodLinkQueries(uow)

    async def connect(
        self,
        *,
        answering_pod_id: UUID,
        asking_pod_id: UUID,
        user_id: UUID,
        shares: Sequence[ResourceGrantInputProtocol],
    ) -> None:
        """Let ``asking_pod_id`` ask ``answering_pod_id``, sharing ``shares``."""
        if answering_pod_id == asking_pod_id:
            raise LinkRefused("A pod can't be connected to itself.")
        organization = await pod_organization_id(self.uow, answering_pod_id)
        if (
            organization is None
            or await pod_organization_id(self.uow, asking_pod_id) != organization
        ):
            raise LinkRefused("Only pods in the same organization can be connected.")
        if await pod_member_id(self.uow, asking_pod_id, user_id) is None:
            raise LinkRefused(
                "You can only connect a pod you're in. Ask someone in it to connect."
            )
        _refuse_beyond_reading(shares)
        validate_pod_resource_grant_permissions(shares)
        normalized = await normalize_pod_resource_grants(
            self.uow.session, pod_id=answering_pod_id, grants=shares
        )
        await self.links.connect(
            answering_pod_id=answering_pod_id,
            asking_pod_id=asking_pod_id,
            shares=normalized,
            steward_user_id=user_id,
        )

    async def disconnect(self, *, answering_pod_id: UUID, asking_pod_id: UUID) -> bool:
        """Take the link away. False when there was none."""
        if (
            await self.links.link(
                answering_pod_id=answering_pod_id, asking_pod_id=asking_pod_id
            )
            is None
        ):
            return False
        await self.links.disconnect(
            answering_pod_id=answering_pod_id, asking_pod_id=asking_pod_id
        )
        return True

    async def incoming(self, *, answering_pod_id: UUID) -> list[PodLinkView]:
        """The pods that may ask this one, and what each was shared."""
        rows = await self.links.incoming(answering_pod_id=answering_pod_id)
        return await self._views(rows, other=lambda row: row.asking_pod_id)

    async def outgoing(self, *, asking_pod_id: UUID) -> list[PodLinkView]:
        """The pods this one may ask, and what each shares with it."""
        rows = await self.links.outgoing(asking_pod_id=asking_pod_id)
        return await self._views(rows, other=lambda row: row.answering_pod_id)

    async def _views(
        self, rows: list[LinkRow], *, other: Callable[[LinkRow], UUID]
    ) -> list[PodLinkView]:
        pods = {
            pod.id: pod
            for pod in await pods_by_ids(
                session=self.uow.session, pod_ids=[other(row) for row in rows]
            )
        }
        views: list[PodLinkView] = []
        for row in rows:
            pod = pods.get(other(row))
            if pod is None:
                continue
            steward = (
                await user_profile(self.uow.session, row.steward_user_id)
                if row.steward_user_id
                else None
            )
            views.append(
                PodLinkView(
                    pod=pod,
                    steward_user_id=row.steward_user_id,
                    steward_name=_name(steward),
                    shared=await self.links.shared(
                        answering_pod_id=row.answering_pod_id,
                        asking_pod_id=row.asking_pod_id,
                    ),
                )
            )
        return views


def _name(profile: UserProfileRef | None) -> str | None:
    if profile is None:
        return None
    full = " ".join(p for p in (profile.first_name, profile.last_name) if p).strip()
    return full or profile.email


def _refuse_beyond_reading(shares: Sequence[ResourceGrantInputProtocol]) -> None:
    for share in shares:
        allowed = SHAREABLE.get(share.resource_type)
        if allowed is None:
            raise BadRequestError(
                f"A link can share tables and folders, not {share.resource_type.value}.",
                code="POD_LINK_UNSHAREABLE",
            )
        beyond = set(share.permission_ids) - allowed
        if beyond:
            raise BadRequestError(
                "A link shares for reading only: "
                + ", ".join(sorted(beyond))
                + " can't be shared.",
                code="POD_LINK_READ_ONLY",
            )
