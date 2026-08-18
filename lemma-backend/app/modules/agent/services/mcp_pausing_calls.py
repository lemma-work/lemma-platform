"""Putting an MCP-served interaction on the durable record before it runs.

A pausing tool — ``ask_user``, ``request_approval``, ``snooze`` — outlives its
own return. The person answers minutes later, on a different surface, through
``/approvals/{tool_call_id}/decision``; the timer fires hours later and the
resume is written under the same id. All of that is addressed by the id of the
tool call, which for an in-process run is the model's own and is handed to the
tool by pydantic-ai.

Nothing supplies one over MCP. ``tools/call`` carries a name and arguments; the
JSON-RPC request id dies with the response. So the tool ran with
``ctx.tool_call_id`` set to ``None``, and every one of those tools has a guard
that turns that into an error — an agent asking a question got back "requires a
durable tool call id" instead of the question reaching anybody.

The harness does eventually report the call, with an id of its own, and that
looked like the answer: wait for it and use it. It is a race that cannot be
won. The adapter announces a call and invokes it in the same breath, so which of
the two arrives at Lemma first — the announcement, over the run's event stream
and a normalizer that deliberately *holds* calls whose arguments are still
streaming, or the tool call itself, over HTTP — is not something either side
decides.

So Lemma records the call, exactly as it does for an in-process run, and the
harness's later duplicate is dropped (see ``agent_host_events``). Ownership
follows the interaction: the record has to exist for the card to render and the
decision to land somewhere, and Lemma is the only side that knows that.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.domain.value_objects import (
    JsonObject,
    MessageDraft,
    to_json_value,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.realtime import (
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.serialization import message_to_payload

logger = get_logger(__name__)

# Prefixed so the record says where the id came from. Nothing parses it — the
# point is that anyone reading a conversation, or a support log, can tell a call
# Lemma recorded on a harness's behalf from one the harness reported itself.
_ID_PREFIX = "lemma-mcp"


def is_pausing_tool(tool_name: str) -> bool:
    return tool_name in PAUSING_TOOL_NAMES


async def record_pausing_tool_call(
    uow_factory: UnitOfWorkFactory,
    *,
    conversation_id: UUID,
    agent_run_id: UUID | None,
    tool_name: str,
    arguments: JsonObject | None,
) -> str | None:
    """Record the call and return the id it is addressed by.

    ``None`` when there is no run to attach it to, which is the one case where
    an interaction genuinely has nowhere to live; the tool then reports that
    rather than pausing on a call nobody can answer.
    """
    if agent_run_id is None:
        return None
    tool_call_id = f"{_ID_PREFIX}-{uuid4().hex}"
    async with uow_factory() as uow:
        saved = await ConversationRepository(uow).append_message(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            draft=MessageDraft.of_tool_call(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_args=dict(arguments or {}),
            ),
        )
        await uow.commit()
    # The card the person answers is rendered from this event, so a live
    # conversation shows the question at the moment it is asked rather than
    # whenever the next poll happens to run.
    await publish_conversation_event(
        conversation_id,
        message_payload(agent_run_id, message_to_payload(saved)),
    )
    logger.debug(
        "agent.mcp_pausing_calls.recorded",
        conversation_id=str(conversation_id),
        tool_name=tool_name,
    )
    return tool_call_id


async def close_pausing_tool_call(
    uow_factory: UnitOfWorkFactory,
    *,
    conversation_id: UUID,
    agent_run_id: UUID | None,
    tool_call_id: str,
    tool_name: str,
    result: object,
) -> None:
    """Write the return for a pausing call that turned out not to pause.

    Most calls to these tools do wait, and this does nothing for those: the
    return is written later, by whoever resolves them. But a pausing tool can
    also answer straight away — a snooze under the minimum, a request the model
    was already granted, an argument that would not validate — and those calls
    are finished the moment they return.

    Leaving one open is not cosmetic. ``start_resume_run_if_ready`` refuses to
    start a resume while any pausing call in the run is still outstanding, on
    the grounds that resuming would orphan it. So a rejected ``snooze(5)``
    early in a turn would sit there, and the *next* snooze — the real one, with
    a timer counting down — would wake to a resume that declines to start. The
    agent sleeps forever, and nothing anywhere reports a failure.
    """
    if await _still_waiting(
        uow_factory,
        agent_run_id=agent_run_id,
        tool_call_id=tool_call_id,
        result=result,
    ):
        return
    async with uow_factory() as uow:
        saved = await ConversationRepository(uow).append_message(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_result=to_json_value(result),
            ),
        )
        await uow.commit()
    await publish_conversation_event(
        conversation_id,
        message_payload(agent_run_id, message_to_payload(saved)),
    )


async def _still_waiting(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID | None,
    tool_call_id: str,
    result: object,
) -> bool:
    """Whether this call is genuinely outstanding, asked two ways.

    ``parked_tool_call_id`` is how ``ask_user`` and ``request_approval`` say a
    person is being waited on. ``snooze`` says it by leaving an ACTIVE wait row,
    which is also the thing that will eventually wake the conversation — so
    reading the row rather than the response means the two cannot disagree.
    """
    if getattr(result, "parked_tool_call_id", None):
        return True
    if agent_run_id is None:
        return False
    async with uow_factory() as uow:
        wait = await AgentConversationWaitRepository(uow).find_active_for_run(
            agent_run_id
        )
    return wait is not None and wait.tool_call_id == tool_call_id
