"""``final_answer`` as a real, schema-typed tool for remote (Agent Host) runs.

The in-process LEMMA harness gets its final answer through pydantic-ai's
``output_type`` (see :mod:`final_answer_tool`). A remote ACP agent has no such
channel: ACP carries text in and a stop reason out, so a structured result can
only come back the way any other result does — as a tool call over the run-scoped
Lemma MCP bridge. This module is that tool.

Two things it does that the ``output_type`` path does not:

* **It validates.** ``StructuredDict`` resolves to a plain ``dict[str, Any]``
  core schema, so the agent's ``output_schema`` only ever reached the model as
  advertised JSON Schema and was never checked server-side. Here it is, with a
  bounded number of rejections so an agent that cannot satisfy its own schema
  degrades instead of looping.
* **It records the answer where the run can find it.** ACP tool-call events
  carry no tool *name* (``ToolCall`` has ``title``/``toolCallId`` and nothing
  else), so recognising the call from the event stream alone is a per-adapter
  heuristic. Persisting here makes the server the authority; the event stream is
  only the fast path.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import TypeAdapter
from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.toolsets import FunctionToolset
from pydantic_core import SchemaValidator
from pydantic_ai._function_schema import FunctionSchema

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import ConversationType, JsonObject
from app.modules.agent.tools.callable_tool_factory import (
    _inline_schema,
    _normalize_json_schema,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.final_answer.final_answer_tool import FinalAgentResult

logger = get_logger(__name__)

FINAL_ANSWER_TOOL_NAME = "final_answer"

# The marker that lets the Agent Host normalizer recognise this result in an
# event stream that does not carry tool names. Both the text block and
# structuredContent of the MCP result carry it, so whichever an adapter echoes
# into rawOutput survives.
FINAL_ANSWER_MARKER = "lemma_final_answer"

# After this many schema rejections, take the answer anyway and flag it. An agent
# that cannot satisfy its own output schema should end with a marked-bad result,
# not spend the whole run arguing with a validator.
_MAX_SCHEMA_REJECTIONS = 3

_STATUSES = ("COMPLETED", "FAILED", "WAITING")


def agent_output_schema(agent: Agent | None) -> JsonObject | None:
    """The agent's output schema, tolerating a partially-built agent stub."""
    schema = getattr(agent, "output_schema", None) if agent is not None else None
    return schema if isinstance(schema, dict) and schema else None


def final_answer_expected(*, agent: Agent | None, conversation: Conversation) -> bool:
    """Does this run owe a structured final answer?

    Mirrors the gate on the prose output contract in ``remote_payload`` so the
    tool exists in exactly the cases the contract asks the agent to use it.
    """
    if agent_output_schema(agent) is not None:
        return True
    return getattr(conversation, "type", None) == ConversationType.TASK


def build_final_answer_toolset(
    *,
    agent: Agent | None,
    uow_factory: UnitOfWorkFactory | None = None,
) -> FunctionToolset[BaseAgentContext]:
    """Build the one-tool toolset exposing ``final_answer`` over MCP."""
    output_schema = agent_output_schema(agent)
    schema = _final_answer_input_schema(output_schema)
    validator = (
        Draft202012Validator(output_schema)
        if _is_validatable_schema(output_schema)
        else None
    )
    rejections = {"count": 0}

    async def _final_answer(
        ctx: RunContext[BaseAgentContext],
        **request: Any,
    ) -> JsonObject:
        status = str(request.get("status") or "").upper()
        if status not in _STATUSES:
            return {
                "success": False,
                "error": (
                    f"`status` must be one of {', '.join(_STATUSES)}; got {status!r}."
                ),
            }
        output = request.get("output")
        error = request.get("error")

        violation = _schema_violation(validator, output)
        if violation is not None:
            rejections["count"] += 1
            if rejections["count"] <= _MAX_SCHEMA_REJECTIONS:
                return {
                    "success": False,
                    "error": (
                        "`output` does not match the agent's output schema: "
                        f"{violation}. Fix it and call final_answer again."
                    ),
                }
            logger.debug("agent.final_answer.schema_violation_accepted.diagnostic")

        result = FinalAgentResult(
            status=cast(Any, status),
            output=output,
            error=error or (str(output) if status == "FAILED" and output else None),
        )
        record: JsonObject = {
            FINAL_ANSWER_MARKER: True,
            "status": result.status,
            "output": result.output,
            "error": result.error,
        }
        if violation is not None:
            record["schema_violation"] = violation

        await _persist(ctx, uow_factory=uow_factory, record=record)
        return {"success": True, **record}

    toolset = FunctionToolset[BaseAgentContext]()
    toolset.add_tool(
        Tool(
            _final_answer,
            name=FINAL_ANSWER_TOOL_NAME,
            description=_description(bool(output_schema)),
            takes_ctx=True,
            strict=False,
            function_schema=FunctionSchema(
                name=FINAL_ANSWER_TOOL_NAME,
                function=_final_answer,
                description=_description(bool(output_schema)),
                validator=cast(
                    SchemaValidator, TypeAdapter(dict[str, Any]).validator
                ),
                json_schema=schema,
                takes_ctx=True,
                is_async=True,
            ),
        )
    )
    return toolset


def _description(has_schema: bool) -> str:
    tail = (
        " `output` must match the agent's output schema."
        if has_schema
        else " `output` is your answer as text."
    )
    return (
        "End the task and return its result. Call this exactly once, when the "
        "work is done or cannot be finished. Use status WAITING when you need "
        "more from the user, FAILED when the task cannot be completed." + tail
    )


def _final_answer_input_schema(output_schema: JsonObject | None) -> JsonObject:
    output = (
        _inline_schema(_normalize_json_schema(output_schema))
        if output_schema
        else {"type": "string"}
    )
    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": list(_STATUSES),
                "description": "COMPLETED, FAILED, or WAITING for more user input.",
            },
            "output": output,
            "error": {
                "type": ["string", "null"],
                "description": "Short reason, when status is FAILED.",
            },
        },
        "required": ["status", "output"],
        "additionalProperties": False,
    }


def _is_validatable_schema(schema: JsonObject | None) -> bool:
    if not schema:
        return False
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:  # noqa: BLE001 - a bad stored schema must not break the run
        logger.debug("agent.final_answer.unusable_output_schema.diagnostic")
        return False
    return True


def _schema_violation(
    validator: Draft202012Validator | None, output: object
) -> str | None:
    if validator is None:
        return None
    try:
        validator.validate(output)
    except ValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path)
        return f"{path or '<root>'}: {exc.message}"
    return None


async def _persist(
    ctx: RunContext[BaseAgentContext],
    *,
    uow_factory: UnitOfWorkFactory | None,
    record: JsonObject,
) -> None:
    """Record the answer on the agent run, so the run can read it back.

    Best-effort: a failure here costs the authoritative copy but leaves the
    event-stream path intact, and must never turn a successful final answer into
    a tool error.
    """
    agent_run_id = getattr(ctx.deps, "agent_run_id", None)
    if uow_factory is None or not isinstance(agent_run_id, UUID):
        return
    try:
        from app.modules.agent.infrastructure.agent_host_final_answer import (
            store_final_answer,
        )

        await store_final_answer(
            uow_factory, agent_run_id=agent_run_id, record=record
        )
    except Exception:  # noqa: BLE001 - see docstring
        logger.debug("agent.final_answer.persist_failed.diagnostic", exc_info=True)


__all__ = [
    "FINAL_ANSWER_MARKER",
    "FINAL_ANSWER_TOOL_NAME",
    "build_final_answer_toolset",
    "final_answer_expected",
]
