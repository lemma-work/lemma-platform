"""Functions a contact's conversation may call.

A pod opts a function in (``contacts_invoke``). A contact holds no grant, so the
call is not authorized as them: it runs the way its owner's own runs do -- the
owner's authority, narrowed by the function's grants, since a function never
exceeds whoever invokes it -- which is exactly what the owner signed up to when
the function was opened to contacts.

The asking contact is told to the function as ``contact_id`` in its input, set
here from what routing knows. Whatever the model put under that key is
replaced, so a prompt cannot make a function act for somebody else. A
function's input schema must accept ``contact_id`` for this reason.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.function.domain.entities import (
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
)
from app.modules.function.domain.errors import FunctionDomainError
from app.modules.function.infrastructure.models import FunctionModel

__all__ = [
    "CONTACT_INPUT_KEY",
    "ContactFunctionOutcome",
    "ContactFunctionUnavailable",
    "ContactFunction",
    "contact_functions",
    "run_function_for_contact",
]

#: The input key a function is told the asking contact under.
CONTACT_INPUT_KEY = "contact_id"

#: The most functions a contact's run is told about.
MAX_LISTED_FUNCTIONS = 50

#: How long a contact's run waits on one function call.
_DEADLINE_SECONDS = 120.0
_POLL_SECONDS = 0.5

_TERMINAL = frozenset(
    {FunctionRunStatus.COMPLETED, FunctionRunStatus.FAILED, FunctionRunStatus.CANCELLED}
)


class ContactFunctionUnavailable(Exception):
    """No function by that name is open to contacts, or it could not start."""


class ContactFunctionOutcome(BaseModel):
    """How one call went: finished with output, or not finished at all."""

    model_config = ConfigDict(frozen=True)

    completed: bool
    output: dict[str, object] | None = None
    status: str


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
) -> ContactFunctionOutcome:
    """Run one opted-in function for this contact and wait for it to finish.

    ``contact_id`` is ``None`` for an anonymous form submission: the function
    is then told ``contact_id: null``, whatever the input said.

    A function that is not opted in is reported as not found: which of the
    pod's functions exist is not the contact's to learn.
    """
    from app.modules.function.api.dependencies import build_function_use_cases
    from app.modules.function.infrastructure.repositories import FunctionRunRepository

    async with uow_factory() as uow:
        owner = await uow.session.scalar(
            select(FunctionModel.user_id).where(
                FunctionModel.pod_id == pod_id,
                FunctionModel.name == name,
                FunctionModel.contacts_invoke.is_(True),
            )
        )
    if owner is None:
        raise ContactFunctionUnavailable(f"Function {name} not found")
    try:
        run = await build_function_use_cases(
            uow_factory
        ).dispatch_function_for_workflow(
            pod_id=pod_id,
            name=name,
            input_data={
                **input_data,
                CONTACT_INPUT_KEY: str(contact_id) if contact_id else None,
            },
            user_id=owner,
        )
    except FunctionDomainError as exc:
        raise ContactFunctionUnavailable(str(exc)) from exc
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _DEADLINE_SECONDS
    while run.status not in _TERMINAL and loop.time() < deadline:
        await asyncio.sleep(_POLL_SECONDS)
        async with uow_factory() as uow:
            latest = await FunctionRunRepository(uow).get_run(run.id)
        if latest is not None:
            run = latest
    return _outcome(run)


def _outcome(run: FunctionRunEntity) -> ContactFunctionOutcome:
    completed = run.status is FunctionRunStatus.COMPLETED
    return ContactFunctionOutcome(
        completed=completed,
        output=dict(run.output_data or {}) if completed else None,
        status=str(run.status.value),
    )
