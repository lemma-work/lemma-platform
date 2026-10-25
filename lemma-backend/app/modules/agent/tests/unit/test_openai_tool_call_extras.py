"""A tool call's ``extra_content`` goes back to the endpoint that sent it.

Gemini 3's OpenAI-compatible endpoint puts a ``thought_signature`` in a tool
call's ``extra_content`` and refuses the next request in the tool loop when the
call comes back without it -- every Gemini 3 run with a tool call failed on its
second model request. The gateway below is a stand-in for any endpoint that
does this; the payload inside ``extra_content`` is opaque to the code under
test, so its shape here is only illustrative.
"""

from __future__ import annotations

import json
from uuid import uuid7

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import JsonObject, MessageKind, MessageRole
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_stream_parts import (
    StreamingParts,
)
from app.modules.agent.services.runtime_model_factory import (
    _UsageOnlyStreamOptionsChatModel,
)

pytestmark = pytest.mark.unit

_BASE_URL = "https://gateway.example.test/v1/"
_EXTRA = {"vendor": {"signature": "opaque-sig"}}


def _call(index: int, call_id: str, args: str, extra: bool) -> JsonObject:
    call: JsonObject = {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {"name": "add", "arguments": args},
    }
    if extra:
        call["extra_content"] = _EXTRA
    return call


class _Gateway:
    """Answers with two calls (only the first carrying extras), then text."""

    def __init__(self, *, stream: bool) -> None:
        self.stream = stream
        self.bodies: list[JsonObject] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        if len(self.bodies) == 1:
            calls = [
                _call(0, "call_1", '{"a":2,"b":3}', extra=True),
                _call(1, "call_2", '{"a":4,"b":5}', extra=False),
            ]
            message: JsonObject = {"role": "assistant", "tool_calls": calls}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "5 and 9"}
            finish = "stop"
        if not self.stream:
            for call in message.get("tool_calls") or []:
                call.pop("index")
            return httpx2.Response(
                200,
                json={
                    "id": "c",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "m",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                },
            )
        chunks = [
            {"choices": [{"index": 0, "delta": message}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        body = "".join(
            "data: "
            + json.dumps(
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "m",
                }
                | chunk
            )
            + "\n\n"
            for chunk in chunks
        )
        return httpx2.Response(
            200,
            content=(body + "data: [DONE]\n\n").encode(),
            headers={"content-type": "text/event-stream"},
        )


def _model(gateway: _Gateway, base_url: str = _BASE_URL):
    return _UsageOnlyStreamOptionsChatModel(
        "m",
        provider=OpenAIProvider(
            openai_client=AsyncOpenAI(
                base_url=base_url,
                api_key="test-key",
                max_retries=0,
                http_client=httpx2.AsyncClient(
                    transport=httpx2.MockTransport(gateway.handle)
                ),
            )
        ),
    )


def _agent(gateway: _Gateway) -> PydanticAgent:
    agent = PydanticAgent(_model(gateway))

    @agent.tool_plain
    def add(a: int, b: int) -> int:
        return a + b

    return agent


def _sent_back_calls(body: JsonObject) -> dict[str, JsonObject]:
    return {
        call["id"]: call
        for message in body["messages"]
        for call in message.get("tool_calls") or []
    }


@pytest.mark.parametrize("stream", [True, False], ids=["streamed", "whole"])
async def test_extra_content_is_sent_back_on_the_call_that_carried_it(
    stream: bool,
) -> None:
    gateway = _Gateway(stream=stream)
    agent = _agent(gateway)

    async def drain(_ctx, events) -> None:
        async for _ in events:
            pass

    result = await agent.run("add", event_stream_handler=drain if stream else None)

    assert result.output == "5 and 9"
    calls = _sent_back_calls(gateway.bodies[1])
    assert calls["call_1"]["extra_content"] == _EXTRA
    # Only what an endpoint attached comes back; nothing is invented.
    assert "extra_content" not in calls["call_2"]


def test_a_call_from_another_endpoint_goes_without_its_extra_content() -> None:
    """An agent's model can change mid-conversation; a different endpoint never
    sent the field, and a strict one would refuse it."""
    part = ToolCallPart(
        "add",
        {"a": 1},
        tool_call_id="call_1",
        provider_name="openai",
        provider_details={"extra_content": _EXTRA, "origin": _BASE_URL},
    )

    here = _model(_Gateway(stream=False))._map_tool_call(part)
    elsewhere = _model(
        _Gateway(stream=False), base_url="https://other.example.test/v1/"
    )._map_tool_call(part)

    assert here.get("extra_content") == _EXTRA
    assert "extra_content" not in elsewhere


def test_a_stored_call_rebuilds_with_what_it_arrived_with() -> None:
    """A run that resumes -- after an approval, say -- rebuilds its tool calls
    from their message rows, and the endpoint checks them all again."""
    details = {"extra_content": _EXTRA, "origin": _BASE_URL}
    part = ToolCallPart(
        "add",
        {"a": 1},
        tool_call_id="call_1",
        provider_name="openai",
        provider_details=details,
    )
    draft = StreamingParts(
        emit_tokens=False, malformed_tool_call_ids=set()
    ).completed_part_message(part=part, part_kind="tool_call", part_content=None)
    assert draft is not None

    conversation_id = uuid7()

    def row(sequence: int, **fields) -> Message:
        return Message.create(
            conversation_id=conversation_id,
            sequence=sequence,
            agent_run_id=None,
            **fields,
        )

    history, _ = history_and_prompt(
        [
            row(1, role=MessageRole.USER, kind=MessageKind.TEXT, text="add"),
            row(
                2,
                role=draft.role,
                kind=draft.kind,
                tool_name=draft.tool_name,
                tool_call_id=draft.tool_call_id,
                tool_args=draft.tool_args,
                metadata=draft.metadata,
            ),
            row(
                3,
                role=MessageRole.TOOL,
                kind=MessageKind.TOOL_RETURN,
                tool_name="add",
                tool_call_id="call_1",
                tool_result=1,
            ),
        ]
    )

    (rebuilt,) = [
        p
        for m in history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    assert rebuilt.provider_name == "openai"
    assert rebuilt.provider_details == details


def test_a_call_with_nothing_attached_stores_nothing_extra() -> None:
    draft = StreamingParts(
        emit_tokens=False, malformed_tool_call_ids=set()
    ).completed_part_message(
        part=ToolCallPart("add", {"a": 1}, tool_call_id="call_1"),
        part_kind="tool_call",
        part_content=None,
    )
    assert draft is not None and draft.metadata == {"tool_name": "add"}
