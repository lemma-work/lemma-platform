"""The two things a contact's run can do beyond reading what is Public.

* ``contact_records`` reads the contact's own rows of a contact-owned table.
* ``contact_function`` calls a function the pod opened to contacts.

Both take the contact from the run (``deps.audience``, set by routing from a
vouched-for handle) and never from the model: there is no argument to name a
contact by, so a prompt cannot ask for somebody else's rows or act for them.
Offered only on a contact's run; a group's outsiders are nobody in particular
and have no rows.
"""

from __future__ import annotations

from functools import partial
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import (
    ReplayClass,
    ToolGroup,
    ToolPolicy,
    ToolSpec,
    tool,
)
from app.modules.agent.infrastructure.tools.payload_limits import bounded_tool_payload
from app.modules.datastore.contracts.contact_rows import (
    MAX_CONTACT_ROWS,
    ContactRowsUnavailable,
    rows_for_contact,
)
from app.modules.function.contracts.contact_functions import (
    ContactFunctionOutcome,
    ContactFunctionUnavailable,
    run_function_for_contact,
)

logger = get_logger(__name__)

CONTACT_RECORDS_TOOL = "contact_records"
CONTACT_FUNCTION_TOOL = "contact_function"

_NOT_A_CONTACT = "This conversation is not with a contact."


_STILL_RUNNING = (
    "That is taking longer than expected and has not finished. It may still "
    "have gone through, so do not tell them it failed and do not try it again; "
    "tell them it is in hand, and call ask_a_person if it matters."
)

_JSON_SCALARS = (str, int, float, bool, list, dict)


def _call_key(ctx: ToolContext) -> str | None:
    """One key per tool call, so a replayed turn does not run it twice.

    Scoped to the conversation: a tool call id is only unique within the
    messages it belongs to.
    """
    if not ctx.tool_call_id:
        return None
    return f"{getattr(ctx, 'conversation_id', None)}:{ctx.tool_call_id}"


def _plain(value: object) -> object:
    """Ids, timestamps and decimals as text, the way every other tool returns rows."""
    if value is None or isinstance(value, _JSON_SCALARS):
        return value
    return str(value)


class ReadContactRows(Protocol):
    async def __call__(
        self,
        *,
        pod_id: UUID,
        table_name: str,
        contact_id: UUID,
        limit: int,
        offset: int,
    ) -> list[dict[str, object]]: ...


class RunContactFunction(Protocol):
    async def __call__(
        self,
        *,
        pod_id: UUID,
        name: str,
        contact_id: UUID,
        input_data: dict[str, object],
        idempotency_key: str | None,
    ) -> ContactFunctionOutcome: ...


class ContactRecordsRequest(BaseModel):
    table: str = Field(description="A contact-owned table named in your context.")
    limit: int = Field(default=20, ge=1, le=MAX_CONTACT_ROWS)
    offset: int = Field(default=0, ge=0)


class ContactFunctionRequest(BaseModel):
    name: str = Field(description="A function opened to contacts, by name.")
    input: JsonObject = Field(
        default_factory=dict,
        description=(
            "The function's input. Who is asking, and the email addresses and "
            "phone numbers they have proved, are added for you; never put an "
            "id, email or phone number for them here."
        ),
    )


def contact_specs(
    *,
    uow_factory: UnitOfWorkFactory,
    read_rows: ReadContactRows | None = None,
    run_function: RunContactFunction | None = None,
) -> tuple[ToolSpec, ...]:
    read_rows = read_rows or partial(rows_for_contact, uow_factory)
    run_function = run_function or partial(run_function_for_contact, uow_factory)

    async def contact_records(
        ctx: ToolContext, request: ContactRecordsRequest
    ) -> dict[str, object]:
        """Read the rows a contact-owned table holds about the person you are
        talking to. Only their rows are ever returned."""
        contact_id, pod_id = ctx.audience.contact_id, ctx.pod_id
        if contact_id is None or pod_id is None:
            return {"success": False, "error": _NOT_A_CONTACT}
        try:
            rows = await read_rows(
                pod_id=pod_id,
                table_name=request.table,
                contact_id=contact_id,
                limit=request.limit,
                offset=request.offset,
            )
        except ContactRowsUnavailable as exc:
            return {"success": False, "error": str(exc)}
        plain = [{key: _plain(value) for key, value in row.items()} for row in rows]
        return {
            "success": True,
            "rows": bounded_tool_payload(plain, what="rows"),
            "count": len(rows),
        }

    async def contact_function(
        ctx: ToolContext, request: ContactFunctionRequest
    ) -> dict[str, object]:
        """Run a function the pod opened to contacts, for the person you are
        talking to. Use it for what they ask you to do, not to explore."""
        contact_id, pod_id = ctx.audience.contact_id, ctx.pod_id
        if contact_id is None or pod_id is None:
            return {"success": False, "error": _NOT_A_CONTACT}
        try:
            outcome = await run_function(
                pod_id=pod_id,
                name=request.name,
                contact_id=contact_id,
                input_data=dict(request.input),
                idempotency_key=_call_key(ctx),
            )
        except ContactFunctionUnavailable as exc:
            return {"success": False, "error": str(exc)}
        if outcome.still_running:
            return {"success": False, "error": _STILL_RUNNING}
        if not outcome.completed:
            logger.info(
                "agent.contact_tools.function_not_completed.observed",
                function_name=request.name,
                status=outcome.status,
            )
            return {
                "success": False,
                "error": (
                    "That did not go through. Tell them plainly, and call "
                    "ask_a_person if it matters."
                ),
            }
        return {
            "success": True,
            "output": bounded_tool_payload(
                outcome.output or {}, what="function output"
            ),
        }

    # Both take the contact from the run, never from the model.
    return (
        tool(
            contact_records,
            ToolGroup.CONTACT,
            ToolPolicy(outsider_allowed=True, replay=ReplayClass.SAFE),
        ),
        tool(
            contact_function,
            ToolGroup.CONTACT,
            ToolPolicy(outsider_allowed=True, replay=ReplayClass.KEYED),
        ),
    )
