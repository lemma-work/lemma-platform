"""Persist the run a contact's call starts: no member, no request context.

A member's call is authorized as the member (``function.execute``) before its
run is written. A contact holds no grant, so there is nothing to authorize them
against: the pod opting the function in (``contacts_invoke``) is the whole of
the permission, and it is re-read under the row lock that creates the run, so
a function closed to contacts a moment ago starts nothing.

The run carries the contact instead of a user. The dispatcher sees no user on
it and runs it as the function's own workload (``function_run`` in core).

The contact's proved handles go in the input under ``contact`` only when the
function declares that key: they are personal data, and a function that never
asked for them has no use for them. Whatever the model put there is dropped
either way, so the key only ever holds what the platform proved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from app.modules.function.config import function_settings
from app.modules.function.domain.entities import (
    FunctionEntity,
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

__all__ = ["CONTACT_HANDLES_KEY", "resolve_contact_execute", "with_contact_handles"]

#: The input key a function that declares it is told the asking contact's
#: proved handles under.
CONTACT_HANDLES_KEY = "contact"


async def resolve_contact_execute(
    service: FunctionService,
    *,
    pod_id: UUID,
    name: str,
    input_data: JsonObject,
    contact_id: UUID | None,
    contact: JsonObject | None,
) -> ResolvedExecution:
    """Select the opted-in function's revision and persist its PENDING run.

    Always asynchronous: the caller waits on the run row, and the worker runs
    it, so no request holds a sandbox round trip open. ``contact`` is the
    asking contact's proved handles, ``None`` when there is nobody to prove.
    """
    found = await service.repository.get_by_name(pod_id, name)
    function = (
        await service.repository.get_for_share(found.id)
        if found is not None and found.id is not None
        else None
    )
    # Not opted in reads as not found: which of the pod's functions exist is
    # not the contact's to learn.
    if function is None or function.id is None or not function.contacts_invoke:
        raise FunctionNotFoundError(f"Function {name} not found")
    require_ready_revision(function)
    input_data = with_contact_handles(function, input_data, contact)
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


def with_contact_handles(
    function: FunctionEntity, input_data: JsonObject, contact: JsonObject | None
) -> JsonObject:
    """The input with the platform's ``contact`` in place of the model's.

    Set only when the function declares the key; dropped otherwise.
    """
    payload = {
        key: value for key, value in input_data.items() if key != CONTACT_HANDLES_KEY
    }
    properties = (function.input_schema or {}).get("properties")
    if isinstance(properties, dict) and CONTACT_HANDLES_KEY in properties:
        payload[CONTACT_HANDLES_KEY] = contact
    return payload
