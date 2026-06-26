"""Deterministic mock LLM for fast e2e (no real model, no API key).

When ``settings.e2e_llm_mode == "mock"`` every pydantic-ai model is built here as
a ``FunctionModel`` instead of the real provider model. We keep the *whole* rest
of the system — harness, tool execution, streaming, persistence — so an e2e run
exercises the full pipeline against the (fake or real) AgentBox; only the token
source is deterministic.

A test scripts the model by putting ``mock_llm_script`` on the conversation
metadata: a list of turns, each a dict with optional ``text`` and ``tool_calls``
(``[{"tool_name", "args", "tool_call_id"}]``). The agent loop really executes any
tool calls and feeds results back, then asks the model again — so turn N of the
script answers the Nth model request of the run. With no script, the model
returns a single short final answer (or, for structured-output agents, calls the
output tool with empty args), so "a run that completes" tests need zero setup.

Both a non-streaming ``function`` and a ``stream_function`` are provided because
the harness drives the model through the streaming API.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

MOCK_SCRIPT_METADATA_KEY = "mock_llm_script"


async def _emulate_model_latency() -> None:
    """Sleep per model turn to emulate real LLM I/O (load-test honesty).

    The instant mock makes an agent run pure CPU, so concurrent runs saturate one
    worker core and every short DB UoW gets stretched (looking like a connection
    leak). A non-zero ``e2e_mock_llm_latency_ms`` makes runs I/O-bound like a real
    model, freeing the core between turns. Default 0 keeps unit/e2e tests instant.
    """
    latency_ms = settings.e2e_mock_llm_latency_ms
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000.0)


def is_mock_llm_enabled() -> bool:
    """True when the agent LLM should be the deterministic mock (e2e only)."""
    return settings.e2e_llm_mode == "mock"


def _current_run_turn_index(messages: Sequence[ModelMessage]) -> int:
    """Model-response count since this run's user prompt = this run's turn index.

    The anchor is the last ModelRequest that carries a real user prompt — a
    ``UserPromptPart`` and NOT a ``ToolReturnPart``. The harness re-injects the
    user prompt alongside every tool return (``ModelRequest[ToolReturnPart,
    UserPromptPart]``), so anchoring on any UserPromptPart would reset the count
    to 0 after each tool call and the mock would re-emit its first turn forever.
    """
    last_user = -1
    for i, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        parts = message.parts
        has_user = any(isinstance(part, UserPromptPart) for part in parts)
        has_tool_return = any(isinstance(part, ToolReturnPart) for part in parts)
        if has_user and not has_tool_return:
            last_user = i
    return sum(
        1 for message in messages[last_user + 1 :] if isinstance(message, ModelResponse)
    )


def _last_user_text(messages: Sequence[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content
                    return content.strip() if isinstance(content, str) else str(content)
    return ""


def _extract_script(conversation: Any) -> list[dict[str, Any]] | None:
    metadata = getattr(conversation, "metadata", None) or {}
    raw = metadata.get(MOCK_SCRIPT_METADATA_KEY) if isinstance(metadata, dict) else None
    if isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
        return raw  # type: ignore[return-value]
    return None


def _resolve_turn(
    messages: Sequence[ModelMessage],
    info: AgentInfo,
    script: list[dict[str, Any]] | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return ``(text, tool_calls)`` for the current model request."""
    turn_index = _current_run_turn_index(messages)
    if script is not None:
        if turn_index < len(script):
            turn = script[turn_index]
            return turn.get("text"), list(turn.get("tool_calls") or [])
        # Script exhausted (e.g. an extra request after the last tool round) —
        # close out the run with a final answer.
        return "[mock] done", []

    # Unscripted default.
    if not info.allow_text_output and info.output_tools:
        # Structured-output agent: best-effort call the output tool so the run
        # completes; tests needing specific output should script it.
        logger.warning(
            "Mock LLM: structured output required but unscripted; calling '%s' with {}",
            info.output_tools[0].name,
        )
        return None, [
            {"tool_name": info.output_tools[0].name, "args": {}, "tool_call_id": "mock-output"}
        ]
    user_text = _last_user_text(messages)
    return (f"[mock] {user_text}" if user_text else "[mock] ok"), []


def build_mock_model(conversation: Any) -> FunctionModel:
    """Build a deterministic FunctionModel (text + tool calls) for one run."""
    script = _extract_script(conversation)

    async def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await _emulate_model_latency()
        text, tool_calls = _resolve_turn(messages, info, script)
        parts: list[Any] = []
        if text:
            parts.append(TextPart(content=text))
        for j, call in enumerate(tool_calls):
            parts.append(
                ToolCallPart(
                    tool_name=str(call["tool_name"]),
                    args=call.get("args") or {},
                    tool_call_id=str(call.get("tool_call_id") or f"mock-{j}"),
                )
            )
        if not parts:
            parts.append(TextPart(content="[mock] (empty)"))
        return ModelResponse(parts=parts, model_name="mock")

    async def _stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        await _emulate_model_latency()
        text, tool_calls = _resolve_turn(messages, info, script)
        if text:
            yield text
        if tool_calls:
            yield {
                j: DeltaToolCall(
                    name=str(call["tool_name"]),
                    json_args=json.dumps(call.get("args") or {}),
                    tool_call_id=str(call.get("tool_call_id") or f"mock-{j}"),
                )
                for j, call in enumerate(tool_calls)
            }
        if not text and not tool_calls:
            yield "[mock] (empty)"

    return FunctionModel(_fn, stream_function=_stream_fn, model_name="mock")
