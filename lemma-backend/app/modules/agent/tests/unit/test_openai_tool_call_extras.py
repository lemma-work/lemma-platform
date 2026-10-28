"""A tool call's ``extra_content`` goes back to the endpoint that sent it.

pydantic-ai carries it (2.57+): read into the call's ``provider_details`` and
sent back unchanged. These hold that through Lemma's own model and its run
log.

Gemini 3's OpenAI-compatible endpoint puts a ``thought_signature`` in a tool
call's ``extra_content`` and refuses a request that sends the call back
without it. The gateway below is a stand-in for any endpoint that
does this; the payload inside ``extra_content`` is opaque to the code under
test, so its shape here is only illustrative.
"""

from __future__ import annotations

import json

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.harness.lemma.run_log import decode, encode
from app.modules.agent.infrastructure.providers.model_factory import (
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


def test_a_logged_call_replays_with_what_it_arrived_with() -> None:
    """A run that resumes -- after an approval, say -- replays its calls from
    the run log, and the endpoint checks them all again."""
    details = {"extra_content": _EXTRA}
    part = ToolCallPart(
        "add",
        {"a": 1},
        tool_call_id="call_1",
        provider_name="openai",
        provider_details=details,
    )

    (replayed,) = decode([encode([ModelResponse(parts=[part])])])

    (rebuilt,) = replayed.parts
    assert isinstance(rebuilt, ToolCallPart)
    assert rebuilt.provider_name == "openai"
    assert rebuilt.provider_details == details
