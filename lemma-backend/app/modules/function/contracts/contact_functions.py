"""Functions a contact's conversation may call.

A pod opts a function in (``contacts_invoke``). A contact holds no grant and is
nobody in the pod, so the call is not authorized as them, and not as the
function's owner either: lending the owner's session to a stranger's request
would let the stranger reach whatever the owner can. The run is the function's
own workload with no member behind it. It carries no user, names the contact,
and calls back with a function-run token (``core/authorization/function_run``)
that gets the function's own grants, pinned to the pod. Contact-owned tables
show it only that contact's rows.

The asking contact is told to the function as ``contact_id`` in its input, set
here from what routing knows. Whatever the model put under that key is
replaced, so a prompt cannot make a function act for somebody else. A function
must declare ``contact_id`` in its input schema to be opened to contacts.

Three limits, each because a contact's conversation can ask on every turn:

* a daily allowance per contact per function (``function_contact_calls_per_day``);
* a deadline (``function_contact_call_wait_seconds``). A call still unfinished
  then is cancelled and reported as still running, not as failed: it may have
  done its work, and telling the contact otherwise invites a second attempt;
* an idempotency key per tool call, so a replayed turn finds the first
  attempt's run instead of starting another.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.function.config import function_settings
from app.modules.function.domain.entities import (
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
)
from app.modules.function.domain.errors import FunctionDomainError
from app.modules.function.domain.types import JsonObject
from app.modules.function.infrastructure.contact_calls import (
    ContactCallLedger,
    ContactCallsUnavailable,
    PriorCall,
)
from app.modules.function.infrastructure.models import FunctionModel

__all__ = [
    "CONTACT_INPUT_KEY",
    "ContactFunctionOutcome",
    "ContactFunctionUnavailable",
    "ContactFunction",
    "contact_functions",
    "run_function_for_contact",
]

logger = get_logger(__name__)

#: The input key a function is told the asking contact under.
CONTACT_INPUT_KEY = "contact_id"

#: The most functions a contact's run is told about.
MAX_LISTED_FUNCTIONS = 50

_POLL_SECONDS = 0.5
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

_TERMINAL = frozenset(
    {FunctionRunStatus.COMPLETED, FunctionRunStatus.FAILED, FunctionRunStatus.CANCELLED}
)


class ContactFunctionUnavailable(Exception):
    """No function by that name is open to contacts, or it could not start."""


class ContactFunctionOutcome(BaseModel):
    """How one call went: finished with output, failed, or still going."""

    model_config = ConfigDict(frozen=True)

    completed: bool
    output: dict[str, object] | None = None
    status: str
    #: The deadline passed first. The run was asked to stop, but it may
    #: already have done what it was for, so this is not a failure.
    still_running: bool = False


class ContactFunction(BaseModel):
    """A function opened to contacts, as a contact's run is told about it."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None
    input_schema: dict[str, object]


async def contact_functions(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> list[ContactFunction]:
    """The pod's functions opened to contacts, ready to run."""
    rows = await uow.session.scalars(
        select(FunctionModel)
        .where(
            FunctionModel.pod_id == pod_id,
            FunctionModel.contacts_invoke.is_(True),
            FunctionModel.status == FunctionStatus.READY,
        )
        .order_by(FunctionModel.name)
        .limit(MAX_LISTED_FUNCTIONS)
    )
    return [
        ContactFunction(
            name=row.name,
            description=row.description,
            input_schema={
                key: value
                for key, value in (row.input_schema or {}).items()
                if key != "$defs"
            },
        )
        for row in rows
    ]


async def run_function_for_contact(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    name: str,
    contact_id: UUID | None,
    input_data: dict[str, object],
    idempotency_key: str | None = None,
    ledger: ContactCallLedger | None = None,
) -> ContactFunctionOutcome:
    """Run one opted-in function for this contact and wait for it to finish.

    ``contact_id`` is ``None`` for an anonymous form submission: the function
    is then told ``contact_id: null``, whatever the input said. Such a call has
    no contact to count against, so only a contact's calls are limited.

    A function that is not opted in is reported as not found: which of the
    pod's functions exist is not the contact's to learn.
    """
    ledger = ledger or ContactCallLedger()
    deadline_seconds = function_settings.function_contact_call_wait_seconds
    actor = f"contact:{contact_id}" if contact_id else "anonymous"
    claim = (
        f"{pod_id}:{contact_id or 'anonymous'}:{name}:{idempotency_key}"
        if idempotency_key
        else None
    )
    try:
        prior = await ledger.claim(claim) if claim else None
    except ContactCallsUnavailable as exc:
        raise ContactFunctionUnavailable(f"Function {name} cannot run now") from exc
    if prior is not None:
        return await _prior_outcome(uow_factory, prior, deadline_seconds)
    try:
        run = await _start(
            uow_factory,
            ledger=ledger,
            pod_id=pod_id,
            name=name,
            contact_id=contact_id,
            input_data=input_data,
        )
    except (ContactCallsUnavailable, ContactFunctionUnavailable) as exc:
        # This attempt started nothing, so its claim must not make a retry
        # wait on a run that will never exist.
        if claim:
            await ledger.release(claim)
        if isinstance(exc, ContactCallsUnavailable):
            raise ContactFunctionUnavailable(f"Function {name} cannot run now") from exc
        raise
    assert run.id is not None
    if claim:
        await _record_quietly(ledger, claim, run.id)
    logger.info(
        "function.contact_functions.run_started.observed",
        actor=actor,
        function_name=name,
        pod_id=str(pod_id),
        run_id=str(run.id),
    )
    return await _wait(uow_factory, run, deadline_seconds, actor=actor)


async def _start(
    uow_factory: UnitOfWorkFactory,
    *,
    ledger: ContactCallLedger,
    pod_id: UUID,
    name: str,
    contact_id: UUID | None,
    input_data: dict[str, object],
) -> FunctionRunEntity:
    from app.modules.function.api.dependencies import build_function_use_cases

    if contact_id is not None and not await ledger.count_call(
        pod_id=pod_id,
        contact_id=contact_id,
        function_name=name,
        limit=function_settings.function_contact_calls_per_day,
    ):
        logger.info(
            "function.contact_functions.daily_limit_reached.observed",
            actor=f"contact:{contact_id}",
            function_name=name,
        )
        raise ContactFunctionUnavailable(
            f"Function {name} has been called as often as it may be today"
        )
    try:
        # Validated rather than cast: the model wrote this input, and a value
        # that is not JSON must be refused here, not stored on the run.
        payload = _JSON_OBJECT.validate_python(
            {**input_data, CONTACT_INPUT_KEY: str(contact_id) if contact_id else None}
        )
    except ValidationError as exc:
        raise ContactFunctionUnavailable(f"Input for {name} is not JSON") from exc
    try:
        return await build_function_use_cases(
            uow_factory
        ).dispatch_function_for_contact(
            pod_id=pod_id, name=name, input_data=payload, contact_id=contact_id
        )
    except FunctionDomainError as exc:
        raise ContactFunctionUnavailable(str(exc)) from exc


async def _record_quietly(ledger: ContactCallLedger, claim: str, run_id: UUID) -> None:
    # The run exists and will run whatever happens here. Failing the call now
    # would tell the contact it did not go through when it did; a retry that
    # finds the claim still pending is told it is in progress, which is true.
    try:
        await ledger.record(claim, run_id)
    except ContactCallsUnavailable:
        return


async def _prior_outcome(
    uow_factory: UnitOfWorkFactory, prior: PriorCall, deadline_seconds: float
) -> ContactFunctionOutcome:
    """What the first attempt at this call came to, without running it again."""
    from app.modules.function.infrastructure.repositories import FunctionRunRepository

    if prior.run_id is None:
        return ContactFunctionOutcome(
            completed=False, status="PENDING", still_running=True
        )
    async with uow_factory() as uow:
        run = await FunctionRunRepository(uow).get_run(prior.run_id)
    if run is None:
        raise ContactFunctionUnavailable("That call is no longer available")
    return await _wait(uow_factory, run, deadline_seconds, actor=None)


async def _wait(
    uow_factory: UnitOfWorkFactory,
    run: FunctionRunEntity,
    deadline_seconds: float,
    *,
    actor: str | None,
) -> ContactFunctionOutcome:
    from app.modules.function.infrastructure.repositories import FunctionRunRepository

    run_id = run.id
    assert run_id is not None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    while run.status not in _TERMINAL and loop.time() < deadline:
        await asyncio.sleep(_POLL_SECONDS)
        async with uow_factory() as uow:
            latest = await FunctionRunRepository(uow).get_run(run_id)
        if latest is not None:
            run = latest
    if run.status in _TERMINAL:
        return _outcome(run)
    if actor is not None:
        # Only the call that started the run stops it: a retry waiting on
        # somebody else's attempt has no business cancelling it.
        await _cancel(uow_factory, run, actor=actor)
    return ContactFunctionOutcome(
        completed=False, status=str(run.status.value), still_running=True
    )


async def _cancel(
    uow_factory: UnitOfWorkFactory, run: FunctionRunEntity, *, actor: str
) -> None:
    from app.modules.function.api.dependencies import build_function_use_cases

    assert run.id is not None
    logger.info(
        "function.contact_functions.deadline_passed.observed",
        actor=actor,
        run_id=str(run.id),
    )
    await build_function_use_cases(uow_factory).cancel_function_run(run.id)


def _outcome(run: FunctionRunEntity) -> ContactFunctionOutcome:
    completed = run.status is FunctionRunStatus.COMPLETED
    return ContactFunctionOutcome(
        completed=completed,
        output=dict(run.output_data or {}) if completed else None,
        status=str(run.status.value),
    )
