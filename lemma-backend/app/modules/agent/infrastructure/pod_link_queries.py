"""Links between pods, which are grants and nothing else.

Pod B lets pod A ask it by granting ``POD:A`` -- a ``resource_permission_grants``
row with grantee type ``POD`` -- ``agent.execute`` on pod B itself: "may run B's
assistant". Whatever else B shares with A is more rows with the same grantee: a
table to read, a folder to read. So a link is all of B's grants to ``POD:A``,
and taking it away is deleting them.

Whoever made the link grant looks after the link (its steward): the
conversations A's asks open in B are theirs, and so are the questions B passes
on. ``created_by_user_id`` already records exactly that person.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select

from app.core.authorization.context import ResourceType
from app.core.authorization.grants import (
    NormalizedResourceGrant,
    delete_grantee_grants,
    list_grantee_resource_grants,
    replace_grantee_resource_grants,
)
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.authorization.pod_principal import POD_PRINCIPAL_TYPE
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

#: The grant that is the link: the asking pod may run this pod's assistant.
LINK_PERMISSION = Permissions.AGENT_EXECUTE
#: A pod is linked to at most this many others in either direction, as one page.
MAX_LINKS = 100


@dataclass(frozen=True, slots=True)
class LinkRow:
    """One link, by the two pods' ids and who looks after it."""

    answering_pod_id: UUID
    asking_pod_id: UUID
    steward_user_id: UUID | None


@dataclass(frozen=True, slots=True)
class SharedResource:
    resource_type: ResourceType
    name: str
    permission_ids: list[str]


class PodLinkQueries:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.session = uow.session

    def _link_rows(self):
        return select(
            ResourcePermissionGrantModel.pod_id,
            ResourcePermissionGrantModel.grantee_id,
            ResourcePermissionGrantModel.created_by_user_id,
        ).where(
            ResourcePermissionGrantModel.grantee_type == POD_PRINCIPAL_TYPE,
            ResourcePermissionGrantModel.resource_type == ResourceType.POD.value,
            ResourcePermissionGrantModel.resource_id
            == ResourcePermissionGrantModel.pod_id,
            ResourcePermissionGrantModel.permission_id == LINK_PERMISSION,
        )

    async def link(
        self, *, answering_pod_id: UUID, asking_pod_id: UUID
    ) -> LinkRow | None:
        row = (
            await self.session.execute(
                self._link_rows()
                .where(
                    ResourcePermissionGrantModel.pod_id == answering_pod_id,
                    ResourcePermissionGrantModel.grantee_id == asking_pod_id,
                )
                .limit(1)
            )
        ).first()
        return LinkRow(*row) if row is not None else None

    async def incoming(self, *, answering_pod_id: UUID) -> list[LinkRow]:
        """The pods that may ask this one."""
        rows = await self.session.execute(
            self._link_rows()
            .where(ResourcePermissionGrantModel.pod_id == answering_pod_id)
            .order_by(ResourcePermissionGrantModel.created_at)
            .limit(MAX_LINKS)
        )
        return [LinkRow(*row) for row in rows]

    async def outgoing(self, *, asking_pod_id: UUID) -> list[LinkRow]:
        """The pods this one may ask."""
        rows = await self.session.execute(
            self._link_rows()
            .where(ResourcePermissionGrantModel.grantee_id == asking_pod_id)
            .order_by(ResourcePermissionGrantModel.created_at)
            .limit(MAX_LINKS)
        )
        return [LinkRow(*row) for row in rows]

    async def shared(
        self, *, answering_pod_id: UUID, asking_pod_id: UUID
    ) -> list[SharedResource]:
        """What the answering pod shares with the asking one, past the link itself."""
        grants = await list_grantee_resource_grants(
            self.session,
            pod_id=answering_pod_id,
            grantee_type=POD_PRINCIPAL_TYPE,
            grantee_id=asking_pod_id,
        )
        return [
            SharedResource(resource_type=kind, name=name, permission_ids=permissions)
            for (kind, name), permissions in grants.items()
            if kind is not ResourceType.POD
        ]

    async def connect(
        self,
        *,
        answering_pod_id: UUID,
        asking_pod_id: UUID,
        shares: Sequence[NormalizedResourceGrant],
        steward_user_id: UUID,
    ) -> None:
        """Write the link and what it shares, replacing whatever was there."""
        await replace_grantee_resource_grants(
            self.session,
            pod_id=answering_pod_id,
            grantee_type=POD_PRINCIPAL_TYPE,
            grantee_id=asking_pod_id,
            grants=[
                NormalizedResourceGrant(
                    resource_type=ResourceType.POD,
                    resource_id=answering_pod_id,
                    permission_ids=[LINK_PERMISSION],
                ),
                *shares,
            ],
            created_by_user_id=steward_user_id,
        )

    async def disconnect(self, *, answering_pod_id: UUID, asking_pod_id: UUID) -> None:
        await delete_grantee_grants(
            self.session,
            pod_id=answering_pod_id,
            grantee_type=POD_PRINCIPAL_TYPE,
            grantee_id=asking_pod_id,
        )

    async def forget_pod(self, *, pod_id: UUID) -> None:
        """Drop every link a deleted pod held, in every pod that gave one.

        Grant rows name their grantee by id with no foreign key, so a deleted
        pod's links would otherwise outlive it.
        """
        await self.session.execute(
            delete(ResourcePermissionGrantModel).where(
                ResourcePermissionGrantModel.grantee_type == POD_PRINCIPAL_TYPE,
                ResourcePermissionGrantModel.grantee_id == pod_id,
            )
        )
        await self.session.flush()
