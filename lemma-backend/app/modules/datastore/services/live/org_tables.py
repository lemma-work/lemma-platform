"""Tables on the ``org`` topic: a table appearing in a pod's list, or leaving it.

Each of a pod's tables (the system's ``reserved_*`` ones aside) is told to who
may read it -- ``datastore.table.read`` on the table, its owner and visibility
weighed as the table list weighs them -- as ``table.added`` when it first is
theirs to see and ``table.removed`` once it is not: dropped, or made personal
or restricted away from them. Creating, changing or dropping a table says when
to read (``tables``, ``table_repository``); a grant on a restricted table
moves the pod's grants epoch, which has the topic weigh every reader again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import ResourceRef, ResourceType, ResourceVisibility
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.org_feeds import Audience, OrgItem, PodItems
from app.modules.datastore.domain.datastore_entities import RESERVED_TABLE_PREFIX
from app.modules.datastore.infrastructure.models import DatastoreTable

#: The pod signal a table's row moving sends.
TABLES_SIGNAL = "tables"
#: Most tables one read returns, across the pods it covers.
MAX_TABLES = 5000


def _listed(
    pod_id: UUID, table_id: UUID, name: str, owner: UUID | None, visibility: str
) -> OrgItem:
    said = {"table_id": str(table_id), "name": name}
    return OrgItem(
        audience=Audience(
            permission=Permissions.DATASTORE_TABLE_READ,
            resource=ResourceRef(
                resource_type=ResourceType.DATASTORE_TABLE,
                resource_id=table_id,
                pod_id=pod_id,
                owner_user_id=owner,
                visibility=ResourceVisibility(visibility),
            ),
        ),
        version="listed",
        held={"type": "table.added", **said},
        ended={"type": "table.removed", **said},
    )


class PodTables:
    name = "datastore.tables_listed"
    kinds = frozenset({TABLES_SIGNAL})

    async def read(self, pod_ids: Sequence[UUID]) -> Mapping[UUID, PodItems]:
        table = DatastoreTable
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            rows = await uow.session.execute(
                select(
                    table.pod_id,
                    table.id,
                    table.table_name,
                    table.user_id,
                    table.visibility,
                )
                .where(
                    table.pod_id.in_(sorted(set(pod_ids))),
                    ~table.table_name.startswith(
                        RESERVED_TABLE_PREFIX, autoescape=True
                    ),
                )
                .order_by(table.id)
                .limit(MAX_TABLES)
            )
        found: dict[UUID, dict[str, OrgItem]] = {}
        for pod_id, table_id, name, owner, visibility in rows:
            found.setdefault(pod_id, {})[str(table_id)] = _listed(
                pod_id, table_id, name, owner, visibility
            )
        return found


__all__ = ["PodTables", "TABLES_SIGNAL"]
