"""The messages one normalized tool event becomes.

Split from the normalizer, which decides *when* -- which step a call closes,
which calls are dropped, what a run leaves open -- while this module owns only
*what*: the shape of a call, a return, and the live tokens around them. Every
input here is already the host's canonical vocabulary
(docs/architecture/agent-host-events.md#tool-calls), so nothing reads an
adapter's payload and nothing guesses.
"""

from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostToolCallPayload,
    AgentHostToolRef,
    AgentHostToolResultPayload,
    AgentHostToolSource,
    AgentHostToolStatus,
)
from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    JsonValue,
    MessageDraft,
)
from app.modules.agent.infrastructure.harnesses.agent_host.final_answer_stream import (
    final_answer_record,
)
from app.modules.agent.infrastructure.harnesses.agent_host.tool_calls import (
    OpenToolCall,
)
from app.modules.agent.infrastructure.harnesses.agent_host.tool_payload import (
    bounded_tool_value,
    unbounded_tool_value,
)

logger = get_logger(__name__)

#: Live output of a running tool. Not a kind any client renders today -- the web
#: client keys on ``text``, ``thinking`` and ``tool`` -- so it is ignored rather
#: than mistaken for answer text, while a client that wants a live terminal can
#: read it without the backend changing.
TOOL_OUTPUT_TOKEN_KIND = "tool_output"

#: What a failed result says when the host gave no sentence of its own. The
#: host always sends one (``exited with code N``, ``not allowed``...); this is
#: only so a card never reads "failed: None".
_DEFAULT_ERRORS = {
    AgentHostToolStatus.FAILED: "failed",
    AgentHostToolStatus.DENIED: "not allowed",
    AgentHostToolStatus.CANCELLED: "cancelled",
}


def parsed_payload[ModelT: BaseModel](
    model: type[ModelT],
    payload: JsonObject,
    *,
    agent_run_id: UUID,
    call_id: str,
    sequence: int,
) -> ModelT | None:
    """The typed payload, or ``None`` (logged) for one the host mis-shaped.

    A malformed event must not end the run: this is one tool card, and the
    consume loop raising here would fail a turn that is otherwise fine.
    """
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "agent.harnesses.agent_host.malformed_event.degraded",
            agent_run_id=str(agent_run_id),
            tool_call_id=call_id,
            agent_host_sequence=sequence,
            payload_model=model.__name__,
            error=str(exc),
        )
        return None


def left_to_lemma(tool: AgentHostToolRef) -> bool:
    """Whether Lemma records this call itself, so the harness's copy is dropped.

    Lemma's pausing tools are already on the record, under an id that can be
    answered: Lemma writes them when the MCP call arrives, because the id the
    harness reports is one no approval endpoint, timer or resume can address.
    Keeping both would ask the person the same question twice, once on a card
    nothing resolves. See ``mcp_pausing_calls``.

    Only Lemma's own: an agent's native tool that happens to be called
    ``ask_user`` is not recorded anywhere else, and dropping it would lose it.
    Before the host said whose tool a call was, this matched on a guessed name,
    and never matched a Codex call.
    """
    return tool.source is AgentHostToolSource.LEMMA and tool.name in PAUSING_TOOL_NAMES


def open_tool_call(
    call: AgentHostToolCallPayload, metadata: JsonObject
) -> OpenToolCall:
    """The call as the ledger keeps it, with the metadata its messages carry.

    ``title`` and ``kind`` are the adapter's own words, kept for display only;
    ``source``/``server`` say whose tool it is; ``parent_call_id`` nests a
    sub-agent's calls under the one that started it.
    """
    tool = call.tool
    call_metadata: JsonObject = {**metadata, "tool_source": tool.source.value}
    for key, value in (
        ("tool_title", tool.title),
        ("tool_kind", tool.kind),
        ("tool_server", tool.server),
        ("parent_call_id", call.parent_call_id),
    ):
        if value:
            call_metadata[key] = value
    return OpenToolCall(name=tool.name, input=call.input, metadata=call_metadata)


def tool_call_events(
    *, agent_run_id: UUID, call_id: str, call: OpenToolCall, sequence: int
) -> list[AgentEvent]:
    """The call's durable message, then its live "running" indicator.

    The pydantic-ai harness streams a call as ``kind: "tool"`` tokens that
    concatenate to ``{"tool_name": ..., "args": ...}``, and clients parse that
    into their running-tool indicator. A host call arrives whole, so it is one
    token, and it comes *after* the message: a client clears its live tool when
    a message lands, so sending it first would make it vanish the moment it
    appeared, while after it the indicator lasts until the tool's return clears
    it. The id is included so a client can pair the indicator with the card.

    Arguments are never bounded; see ``unbounded_tool_value``.
    """
    tool_args = unbounded_tool_value(call.input)
    return [
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name=call.name,
                tool_call_id=call_id,
                tool_args=tool_args,
                metadata=call.metadata,
            ),
            agent_run_id=agent_run_id,
            sequence=sequence,
        ),
        AgentEvent(
            type=AgentEventType.TOKEN,
            data={
                "kind": "tool",
                "data": json.dumps(
                    {
                        "tool_name": call.name,
                        "tool_call_id": call_id,
                        "args": tool_args,
                    },
                    default=str,
                ),
            },
            agent_run_id=agent_run_id,
        ),
    ]


def tool_output_token(*, agent_run_id: UUID, call_id: str, text: str) -> AgentEvent:
    """Live output while a call runs. Never persisted: the result is."""
    return AgentEvent(
        type=AgentEventType.TOKEN,
        data={"kind": TOOL_OUTPUT_TOKEN_KIND, "data": text, "tool_call_id": call_id},
        agent_run_id=agent_run_id,
    )


def tool_return_event(
    *,
    agent_run_id: UUID,
    call_id: str,
    call: OpenToolCall,
    result: AgentHostToolResultPayload,
    sequence: int,
) -> AgentEvent:
    """The return, under the name its call was announced with."""
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_return(
            tool_name=call.name,
            tool_call_id=call_id,
            tool_result=tool_result_value(result),
            metadata={
                **call.metadata,
                "agent_host_sequence": sequence,
                "tool_status": result.status.value,
            },
        ),
        agent_run_id=agent_run_id,
        sequence=sequence,
    )


def tool_result_value(result: AgentHostToolResultPayload) -> JsonValue:
    """What a finished call returned, as the conversation stores it.

    A completed call's output is the tool's own value, bounded. A call that did
    not complete keeps its output too -- replacing it with `{"success": false}`
    is what left a failed command showing only the word "failed", when what a
    person needed was the error it printed -- and gains the `success: false`
    and `error` every card already reads a failure from.
    """
    output = bounded_tool_value(result.output)
    if result.status is AgentHostToolStatus.COMPLETED:
        return output
    if isinstance(output, dict):
        failed: JsonObject = dict(output)
    elif output in (None, "", []):
        failed = {}
    else:
        failed = {"output": output}
    failed["success"] = False
    failed["error"] = result.error or _DEFAULT_ERRORS.get(
        result.status, result.status.value
    )
    return failed


def final_answer_in(output: object, tool_input: object) -> JsonObject | None:
    """A final-answer record in a call's output or, failing that, its input.

    Read from the RAW values, before bounding: ``bounded_tool_value`` truncates
    long strings and replaces anything past its depth limit with a placeholder,
    which would turn a valid structured answer into something that still looks
    structured but is not.

    The host takes an MCP result out of its envelope when it can (the
    ``structuredContent``, or a single text block that is a JSON object) and
    passes the content blocks through when it cannot. The final-answer tool
    stamps its marker into both halves, so the text blocks are read too.
    """
    record = final_answer_record(output)
    if record is None and isinstance(output, list):
        for block in output:
            if isinstance(block, dict) and block.get("type") == "text":
                record = final_answer_record(block.get("text"))
                if record is not None:
                    break
    return record or final_answer_record(tool_input)
