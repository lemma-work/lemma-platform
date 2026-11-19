"""The record a run a person starts is about, read as that person.

A run takes no free-form input; inputs arrive through FORM nodes. What an app
needs is to start a workflow *about* one row -- prepare this meeting, look
into this deal -- which a table trigger can do and a button could not, so
plugins kept a "wanted" column and a trigger on it. Here the button names the
row, the platform reads it as the person (the table permission and row scope
the API applies, through ``datastore.contracts.event_reads``), and the run gets
it as a table change would hand it over: the fields in ``start.payload``, the
table and record in ``start.metadata``. A record the person cannot read is
indistinguishable from no record, so a run cannot be used to learn of one.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from pydantic_core import to_jsonable_python

from app.core.authorization.context import Context
from app.modules.workflow.api.schemas import WorkflowRunAbout
from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.start import WorkflowStartType


async def run_about_record(
    uow, *, pod_id: UUID, about: WorkflowRunAbout, ctx: Context
) -> TriggerContext:
    """``start.*`` for a run about ``about``; 404 when the person cannot read it."""
    from app.modules.datastore.contracts.event_reads import read_record_as

    if ctx.user_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="A run about a record needs a person."
        )
    row = await read_record_as(
        uow,
        pod_id=pod_id,
        table_name=about.table,
        record_id=about.record_id,
        user_id=ctx.user_id,
        ctx=ctx,
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No record you can read matches that."
        )
    return TriggerContext(
        trigger_type=WorkflowStartType.MANUAL,
        payload=to_jsonable_python(row),
        metadata={"table_name": about.table, "record_id": about.record_id},
    )


__all__ = ["run_about_record"]
