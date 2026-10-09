"""Persist the run a contact's call starts: no member, no request context.

A member's call is authorized as the member (``function.execute``) before its
run is written. A contact holds no grant, so there is nothing to authorize them
against: the pod opting the function in (``contacts_invoke``) is the whole of
the permission, and it is re-read under the row lock that creates the run, so
a function closed to contacts a moment ago starts nothing.

The run carries the contact instead of a user. The dispatcher sees no user on
it and runs it as the function's own workload (``function_run`` in core).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from app.modules.function.config import function_settings
from app.modules.function.domain.entities import (
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionType,
    ResolvedExecution,
)
from app.modules.function.domain.errors import FunctionNotFoundError
from app.modules.function.domain.identities import function_run_job_id
from app.modules.function.domain.types import JsonObject
from app.modules.function.services.execution_preflight import (
    require_ready_revision,
    validate_input,
)
from app.modules.function.services.function_service import FunctionService

__all__ = ["resolve_contact_execute"]


async def resolve_contact_execute(
    service: FunctionService,
    *,
    pod_id: UUID,
    name: str,
    input_data: JsonObject,
    contact_id: UUID | None,
) -> ResolvedExecution:
    """Select the opted-in function's revision and persist its PENDING run.

    Always asynchronous: the caller waits on the run row, and the worker runs
    it, so no request holds a sandbox round trip open.
    """
    found = await service.repository.get_by_name(pod_id, name)
    function = (
        await service.repository.get_for_update(found.id)
        if found is not None and found.id is not None
        else None
    )
    # Not opted in reads as not found: which of the pod's functions exist is
    # not the contact's to learn.
    if function is None or function.id is None or not function.contacts_invoke:
        raise FunctionNotFoundError(f"Function {name} not found")
    require_ready_revision(function)
    validate_input(function, input_data)
    deadline_seconds = (
        function_settings.function_job_deadline_seconds
        if function.type == FunctionType.JOB
        else function_settings.function_api_deadline_seconds
    )
    run_id = uuid7()
    run = await service.run_repository.create_run(
        FunctionRunEntity(
            id=run_id,
            function_id=function.id,
            revision_hash=function.revision_hash,
            user_id=None,
            contact_id=contact_id,
            input_data=input_data,
            status=FunctionRunStatus.PENDING,
            deadline_at=datetime.now(timezone.utc)
            + timedelta(seconds=deadline_seconds),
            job_id=function_run_job_id(run_id),
        )
    )
    return ResolvedExecution(function=function, run=run)
