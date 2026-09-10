"""The table a datastore schedule watches, resolved for authorization.

One operation, not `DatastoreTableRepository`.
`app/composition/schedule_datastore_policy.py` built the repository itself and
then read two fields off the row: the table's id, to name it to `ctx`, and
`enable_rls`, to decide whether reading another member's runs needs a delete
grant. Both are datastore's answers; the repository's constructor -- and the
fact that a table is looked up by *datastore* and name rather than by pod and
name -- were never schedule's to know.

Returning a `ResourceRef` rather than the id keeps it that way. The caller
authorizes against what datastore says the resource is, and never assembles a
reference to another module's row out of parts.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
repository layer, and everything importing any datastore contract would
otherwise pay for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.modules.datastore.infrastructure.repositories import DatastoreTableRepository


@dataclass(frozen=True, slots=True)
class ScheduleTableTarget:
    """A named table as a schedule needs it: what to authorize, and whether RLS.

    The two travel together because the second only means anything about the
    first: ``enable_rls`` says whether rows in *this* table are partitioned by
    owner, which is the question that decides whether one member may read
    another's schedule runs against it.
    """

    ref: ResourceRef
    enable_rls: bool


async def schedule_table_target(
    uow, *, pod_id: UUID, table_name: str, ctx: Context
) -> ScheduleTableTarget | None:
    """The named table in this pod, or ``None`` when it does not have one."""
    table = await DatastoreTableRepository(uow).get_by_datastore_and_name(
        pod_id, table_name, ctx=ctx
    )
    if table is None:
        return None
    return ScheduleTableTarget(
        ref=ResourceRef.table(pod_id, table.id),
        enable_rls=table.enable_rls,
    )


__all__ = ["ScheduleTableTarget", "schedule_table_target"]
