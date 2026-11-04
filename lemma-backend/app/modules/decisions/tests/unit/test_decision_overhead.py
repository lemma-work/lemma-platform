"""What a decision adds on top of the provider's own time.

The real Anthropic SDK and pydantic-ai model, the real provider and service,
over an HTTP transport that answers after a fixed delay standing in for the
provider. Everything a decision spends beyond that delay is Lemma's.

What is proved: one interactive decision is one provider round-trip -- no
token pre-count first -- it asks a model that thinks by default not to, and
the time it adds beyond the round-trip is a small fraction of it.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from uuid import uuid4

import httpx2 as httpx
import pytest
from anthropic import AsyncAnthropic
from pydantic_ai import UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings

from app.modules.agent.services.anthropic_thinking import (
    AdaptiveThinkingAnthropicModel,
)
from app.modules.agent.services.runtime_model_factory import usage_limits_for
from app.modules.decisions.config import DecisionsSettings
from app.modules.decisions.domain.request import DecisionCaller, DecisionRequest
from app.modules.decisions.infrastructure.providers.model_provider import (
    ModelDecisionProvider,
)
from app.modules.decisions.services.decision_service import DecisionService

pytestmark = pytest.mark.unit

#: What the stand-in provider takes to answer each HTTP request.
PROVIDER_SECONDS = 0.2
#: A model whose profile thinks when a request does not say otherwise.
THINKS_BY_DEFAULT = "claude-haiku-5-5"
REQUEST = DecisionRequest(
    instruction="Route this line of the call.",
    evidence="Can you pull up last quarter's numbers?",
    schema={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "enum": ["worker", "talker"],
                "description": "Who handles it?",
            },
            "urgent": {"type": "boolean", "description": "Now?"},
        },
    },
    priority="interactive",
)
CALLER = DecisionCaller(user_id=uuid4(), organization_id=uuid4(), pod_id=uuid4())


@dataclass
class _Provider:
    """An Anthropic Messages endpoint that answers after `PROVIDER_SECONDS`."""

    paths: list[str] = field(default_factory=list)
    bodies: list[dict[str, object]] = field(default_factory=list)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(PROVIDER_SECONDS)
        body = json.loads(request.content)
        self.paths.append(request.url.path)
        self.bodies.append(body)
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 120})
        tool = body["tools"][0]["name"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": tool,
                        "input": {"to": "worker", "urgent": False},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 120, "output_tokens": 12},
            },
        )


@dataclass(frozen=True)
class _Runtime:
    model: Model
    runtime_profile: Mapping[str, object | None]
    usage_limits: UsageLimits


def _service(provider: _Provider) -> DecisionService:
    model = AdaptiveThinkingAnthropicModel(
        THINKS_BY_DEFAULT,
        provider=AnthropicProvider(
            anthropic_client=AsyncAnthropic(
                api_key="test",
                base_url="http://provider.test",
                max_retries=0,
                http_client=httpx.AsyncClient(
                    transport=httpx.MockTransport(provider.handle)
                ),
            )
        ),
        settings=ModelSettings(max_tokens=1024),
    )

    async def resolve(*, usage_limits: UsageLimits, **_: object) -> _Runtime:
        # What `resolve_system_runtime` does with the provider's ceiling.
        return _Runtime(
            model=model,
            runtime_profile={"model_name": THINKS_BY_DEFAULT},
            usage_limits=usage_limits_for(model, usage_limits),
        )

    class _NoLimit:
        async def retry_after(self, key: str) -> int | None:
            return None

    return DecisionService(
        settings=DecisionsSettings(),
        provider=lambda _: ModelDecisionProvider(model_name=None, resolver=resolve),
        limiter=_NoLimit(),
        metering=lambda _: nullcontext(),
    )


async def test_an_interactive_decision_is_one_round_trip() -> None:
    provider = _Provider()

    result = await _service(provider).decide(REQUEST, CALLER)

    assert result.answers["to"].value == "worker"
    assert provider.paths == ["/v1/messages"]


async def test_a_model_that_thinks_by_default_is_asked_not_to() -> None:
    provider = _Provider()

    await _service(provider).decide(REQUEST, CALLER)

    assert provider.bodies[-1].get("thinking") == {"type": "disabled"}


async def test_what_a_decision_adds_is_small_beside_the_provider() -> None:
    provider = _Provider()
    service = _service(provider)
    await service.decide(REQUEST, CALLER)  # imports and first-use work
    runs = 5

    started = time.perf_counter()
    for _ in range(runs):
        await service.decide(REQUEST, CALLER)
    added = (time.perf_counter() - started) / runs - PROVIDER_SECONDS

    # Measured locally at 6-9 ms. A second round-trip -- the token pre-count
    # this replaced -- adds the whole of PROVIDER_SECONDS again (215 ms).
    assert added < PROVIDER_SECONDS / 4, f"a decision added {added * 1000:.0f} ms"
