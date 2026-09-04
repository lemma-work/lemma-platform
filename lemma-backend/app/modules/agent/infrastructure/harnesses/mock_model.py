"""Deterministic mock LLM for fast e2e (no real model, no API key).

When ``settings.e2e_llm_mode == "mock"`` every pydantic-ai model is built here as
a ``FunctionModel`` instead of the real provider model. We keep the *whole* rest
of the system — harness, tool execution, streaming, persistence — so an e2e run
exercises the full pipeline against the (fake or real) sandbox; only the token
source is deterministic.

A test scripts the model by putting ``mock_llm_script`` on the conversation
metadata: a list of turns, each a dict with optional ``text``, ``usage`` and
``tool_calls``
(``[{"tool_name", "args", "tool_call_id"}]``). The agent loop really executes any
tool calls and feeds results back, then asks the model again — so turn N of the
script answers the Nth model request of the run. With no script, the model
returns a single short final answer — or, for structured-output agents, calls
the output tool with the smallest payload its schema accepts — so "a run that
completes" tests need zero setup.

Both a non-streaming ``function`` and a ``stream_function`` are provided because
the harness drives the model through the streaming API.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingPart,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.usage import RequestUsage

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

MOCK_SCRIPT_METADATA_KEY = "mock_llm_script"

# Delta size for scripted text. Smaller than `CharStreamBuffer`'s 50-char window
# so a scripted answer of any realistic length crosses it several times and the
# run emits a sequence of token frames rather than one.
_STREAM_DELTA_CHARS = 12


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
    """The user's own last message.

    Parts are scanned in reverse as well as messages: a request can carry more
    than one ``UserPromptPart`` — the runtime-notes block is prepended to the
    user's turn — and the *user's* text is the last of them, not the first.
    """
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
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


# ``${tool_call_id.dotted.path}`` in a scripted argument, replaced with that
# value from the named earlier tool result. A script is static JSON persisted on
# the conversation, so without this it can only ever pass literals - and an id
# the workspace generated at runtime (a process id, a file handle) cannot be a
# literal. Scripting a made-up one instead tests nothing: the tool correctly
# reports that no such process exists.
_RESULT_REFERENCE = re.compile(r"^\$\{([^.{}]+)\.([^{}]+)\}$")


def _as_mapping(value: Any) -> Any:
    """Normalize a tool result to a dict when it plausibly is one.

    A tool return reaches the model as whatever the tool returned - a dict, a
    pydantic model, or a JSON string depending on the toolset - and a reference
    should resolve against any of them.
    """
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _tool_result_value(
    messages: Sequence[ModelMessage],
    tool_call_id: str,
    path: str,
) -> Any:
    """Read ``path`` out of the result of an earlier tool call in this run."""
    for message in reversed(messages):
        for part in getattr(message, "parts", None) or []:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_call_id != tool_call_id:
                continue
            value = _as_mapping(part.content)
            for key in path.split("."):
                if not isinstance(value, dict):
                    return None
                value = _as_mapping(value.get(key))
            return value
    return None


def _resolve_references(value: Any, messages: Sequence[ModelMessage]) -> Any:
    if isinstance(value, str):
        match = _RESULT_REFERENCE.match(value)
        if match is None:
            return value
        return _tool_result_value(messages, match.group(1), match.group(2))
    if isinstance(value, dict):
        return {key: _resolve_references(item, messages) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_references(item, messages) for item in value]
    return value


def _resolve_turn(
    messages: Sequence[ModelMessage],
    info: AgentInfo,
    script: list[dict[str, Any]] | None,
    drop_counts: dict[int, int] | None = None,
) -> tuple[str | None, list[dict[str, Any]], str | None]:
    """Return ``(text, tool_calls, thinking)`` for the current model request.

    ``thinking`` is the reasoning a scripted turn declares, delivered the way a
    reasoning model delivers it -- as its own part, not inside the text. A test
    that wants the *other* shape, reasoning inlined in the answer as tags, just
    puts the tags in ``text``: the delta size below straddles them exactly as a
    real provider does, which is the whole difficulty.
    """
    turn_index = _current_run_turn_index(messages)
    if script is not None:
        if turn_index < len(script):
            turn = script[turn_index]
            _raise_scripted_error(
                turn.get("error"), turn_index=turn_index, drop_counts=drop_counts
            )
            tool_calls = [
                {**call, "args": _resolve_references(call.get("args") or {}, messages)}
                for call in (turn.get("tool_calls") or [])
            ]
            return turn.get("text"), tool_calls, turn.get("thinking")
        # Script exhausted (e.g. an extra request after the last tool round) —
        # close out the run with a final answer.
        return "[mock] done", [], None

    # Unscripted default.
    if not info.allow_text_output and info.output_tools:
        # Structured-output agent: call the output tool with the smallest payload
        # its schema accepts, so the run completes. Tests needing specific output
        # should script it.
        output_tool = info.output_tools[0]
        logger.debug("agent.mock_model.mock_llm_structured_output_required.diagnostic")
        return (
            None,
            [
                {
                    "tool_name": output_tool.name,
                    "args": _minimal_valid_args(
                        getattr(output_tool, "parameters_json_schema", None)
                    ),
                    "tool_call_id": "mock-output",
                }
            ],
            None,
        )
    user_text = _last_user_text(messages)
    return (f"[mock] {user_text}" if user_text else "[mock] ok"), [], None


# Called, not stored: a shared `[]` would be handed to every caller to mutate.
_ZERO_BY_TYPE: dict[str, Any] = {
    "array": list,
    "string": str,
    "integer": int,
    "number": int,
    "boolean": bool,
}


def _zero_value(schema: Any, depth: int = 0) -> Any:
    """The simplest value of the type ``schema`` declares.

    Only shape matters here: the mock is proving the pipeline runs, not
    producing meaningful content. Unknown or unconstrained schemas fall back to
    ``None``, which is what an absent field would have been anyway.
    """
    if not isinstance(schema, dict) or depth > 4:
        return None
    branches = next(
        (
            schema[key]
            for key in ("anyOf", "oneOf", "allOf")
            if isinstance(schema.get(key), list) and schema[key]
        ),
        None,
    )
    if branches is not None:
        return _zero_value(branches[0], depth + 1)
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    declared = schema.get("type")
    if isinstance(declared, list):
        declared = next((item for item in declared if item != "null"), None)
    if declared == "object":
        return _minimal_valid_args(schema, depth + 1)
    factory = _ZERO_BY_TYPE.get(declared)
    return factory() if factory is not None else None


def _minimal_valid_args(schema: Any, depth: int = 0) -> dict[str, Any]:
    """The smallest object satisfying ``schema``, for an unscripted output tool.

    The mock used to send ``{}`` here and call it best-effort. It is not: an
    output schema with a required field rejects ``{}``, pydantic-ai asks the
    model to retry, and the mock -- having no script -- answers with the same
    empty object every time. The run dies on the retry ceiling with "a tool
    failed repeatedly", which reads like the agent is misconfigured and is
    really just the mock being unable to satisfy a schema it was shown.

    Every property is filled, not only the required ones. A JSON schema
    converted to a pydantic model does not always keep "optional" optional, and
    an extra zero-valued key costs a mock nothing.
    """
    if not isinstance(schema, dict) or depth > 4:
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {
        str(name): _zero_value(subschema, depth)
        for name, subschema in properties.items()
    }


def _raise_scripted_error(
    error: object,
    *,
    turn_index: int = 0,
    drop_counts: dict[int, int] | None = None,
) -> None:
    """Translate the E2E DSL's failure control into real model exceptions."""
    if not isinstance(error, dict):
        return
    kind = str(error.get("kind") or "generic")
    message = str(error.get("message") or "scripted model failure")
    if kind == "model_http":
        raise ModelHTTPError(
            status_code=int(error.get("status_code") or 502),
            model_name="mock",
            body={"message": message},
        )
    if kind == "unexpected_model_behavior":
        raise UnexpectedModelBehavior(message)
    if kind == "usage_limit":
        raise UsageLimitExceeded(message)
    if kind == "stream_drop":
        # Only fail the first N attempts at this turn; the retry has to be
        # able to succeed or the journey proves nothing.
        seen = (drop_counts or {}).get(turn_index, 0)
        if seen >= _drop_after_turns(error):
            return
        if drop_counts is not None:
            drop_counts[turn_index] = seen + 1
        # The production failure this exists to reproduce: the provider accepts
        # the request and then drops the connection mid-answer. The harness
        # retries from the messages already recorded, so a journey scripting
        # this should still complete cleanly.
        raise httpx.ReadError(message)
    raise RuntimeError(message)


def _drop_after_turns(error: object) -> int:
    """How many attempts a `stream_drop` should fail before succeeding.

    Defaults to 1 so a scripted drop is transient — the point is to prove the
    run recovers, not that it gives up.
    """
    if not isinstance(error, dict):
        return 0
    try:
        return max(0, int(error.get("times") or 1))
    except TypeError, ValueError:
        return 1


def _response_parts(
    thinking: str | None,
    text: str | None,
    tool_calls: list[dict[str, Any]],
) -> list[Any]:
    """One scripted turn as whole parts, for the non-streaming path."""
    parts: list[Any] = []
    if thinking:
        parts.append(ThinkingPart(content=thinking))
    if text:
        parts.append(TextPart(content=text))
    for index, call in enumerate(tool_calls):
        parts.append(
            ToolCallPart(
                tool_name=str(call["tool_name"]),
                args=call.get("args") or {},
                tool_call_id=str(call.get("tool_call_id") or f"mock-{index}"),
            )
        )
    return parts or [TextPart(content="[mock] (empty)")]


def _chunks(text: str) -> Iterator[str]:
    """Small deltas, the way a provider actually sends them.

    Yielding a whole answer in one chunk made the mock unable to tell
    incremental streaming apart from a harness that buffers the entire response
    and flushes it at the end -- which is what `test_sse_streaming_e2e` exists to
    catch. It also matters for reasoning: a `<think>` tag written into the text
    only straddles a delta boundary because the deltas are this small, and
    straddling is precisely what the tag handling has to survive.
    """
    for start in range(0, len(text), _STREAM_DELTA_CHARS):
        yield text[start : start + _STREAM_DELTA_CHARS]


def _streamed_deltas(
    thinking: str | None,
    text: str | None,
    tool_calls: list[dict[str, Any]],
) -> Iterator[str | dict[int, Any]]:
    """One scripted turn as a delta stream, in the order a provider sends it."""
    if thinking:
        for chunk in _chunks(thinking):
            yield {0: DeltaThinkingPart(content=chunk)}
    if text:
        yield from _chunks(text)
    if tool_calls:
        yield {
            index: DeltaToolCall(
                name=str(call["tool_name"]),
                json_args=json.dumps(call.get("args") or {}),
                tool_call_id=str(call.get("tool_call_id") or f"mock-{index}"),
            )
            for index, call in enumerate(tool_calls)
        }
    if not thinking and not text and not tool_calls:
        yield "[mock] (empty)"


def build_mock_model(conversation: Any) -> FunctionModel:
    """Build a deterministic FunctionModel (text + tool calls) for one run."""
    script = _extract_script(conversation)
    # Per-run, so a scripted stream drop fails an attempt rather than a turn:
    # the retry re-enters with the same history and must be allowed through.
    drop_counts: dict[int, int] = {}

    async def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await _emulate_model_latency()
        text, tool_calls, thinking = _resolve_turn(messages, info, script, drop_counts)
        return ModelResponse(
            parts=_response_parts(thinking, text, tool_calls), model_name="mock"
        )

    async def _stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        await _emulate_model_latency()
        text, tool_calls, thinking = _resolve_turn(messages, info, script, drop_counts)
        for delta in _streamed_deltas(thinking, text, tool_calls):
            yield delta

    def _usage_for(messages: list[ModelMessage]) -> RequestUsage | None:
        return _scripted_usage(messages, script)

    return _UsageScriptedFunctionModel(
        _fn,
        stream_function=_stream_fn,
        model_name="mock",
        usage_for=_usage_for,
    )


class _UsageScriptedFunctionModel(FunctionModel):
    """A ``FunctionModel`` that reports the token counts a script asked for.

    Without this the mock reports pydantic-ai's *estimate* -- a flat 50 input
    tokens per request and never a cached one -- so no test could assert what a
    run cost, and the cached-input discount could not be exercised end to end at
    all. The billing suite worked around it by reading `cost_usd` back out and
    setting the plan limit to whatever it happened to be, which cannot catch a
    pricing bug because it derives the expectation from the thing under test.

    Both paths are overridden because the harness streams and the helper calls
    (titles, compaction) do not. On the streaming path the usage is applied after
    the stream is exhausted, which is where the accumulated estimate would
    otherwise stand -- and still before the caller folds it into the run.

    `StreamedResponse.usage` is a read-only property over `_usage`, so the
    streaming path has to write the private attribute; there is no public seam.
    The write is guarded rather than trusted: were pydantic-ai to rename it, an
    unguarded assignment would quietly create a new attribute and every cost
    assertion downstream would go back to asserting the estimator's flat 50
    tokens while still passing. Failing here instead points at the one line that
    has to change.
    """

    #: The attribute `StreamedResponse.usage` reads from. Named once so the
    #: guard below and the failure message agree.
    _STREAM_USAGE_ATTRIBUTE = "_usage"

    def __init__(
        self,
        *args: object,
        usage_for: Callable[[list[ModelMessage]], RequestUsage | None],
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._usage_for = usage_for

    async def request(self, messages, model_settings, model_request_parameters):
        response = await super().request(
            messages, model_settings, model_request_parameters
        )
        scripted = self._usage_for(messages)
        if scripted is not None:
            response.usage = scripted
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages,
        model_settings,
        model_request_parameters,
        run_context=None,
    ) -> AsyncIterator[object]:
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream
            scripted = self._usage_for(messages)
            if scripted is None:
                return
            if not hasattr(stream, self._STREAM_USAGE_ATTRIBUTE):
                raise AttributeError(
                    f"{type(stream).__name__} no longer stores its usage in "
                    f"{self._STREAM_USAGE_ATTRIBUTE!r}; scripted token counts "
                    "cannot be applied to a streamed response until this is "
                    "pointed at whatever replaced it."
                )
            setattr(stream, self._STREAM_USAGE_ATTRIBUTE, scripted)


def _scripted_usage(
    messages: Sequence[ModelMessage],
    script: list[dict[str, object]] | None,
) -> RequestUsage | None:
    """The usage this turn declared, if it declared any."""
    if script is None:
        return None
    turn_index = _current_run_turn_index(messages)
    if turn_index >= len(script):
        return None
    declared = script[turn_index].get("usage")
    if not isinstance(declared, dict):
        return None
    return RequestUsage(
        input_tokens=_usage_field(declared, "input_tokens"),
        output_tokens=_usage_field(declared, "output_tokens"),
        cache_read_tokens=_usage_field(declared, "cache_read_tokens"),
        cache_write_tokens=_usage_field(declared, "cache_write_tokens"),
    )


def _usage_field(declared: dict[str, object], name: str) -> int:
    try:
        return max(0, int(declared.get(name) or 0))
    except TypeError, ValueError:
        return 0
