"""System One over HTTP: what is sent, what is metered, how failure is told."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import pytest

from app.modules.decisions.domain.errors import DecisionUnavailableError
from app.modules.decisions.domain.request import (
    DecisionCaller,
    DecisionRequest,
    build_task,
)
from app.modules.decisions.infrastructure.providers.typesafe_provider import (
    TypesafeDecisionProvider,
)

pytestmark = pytest.mark.unit

CALLER = DecisionCaller(user_id=uuid4(), organization_id=uuid4(), pod_id=uuid4())
TASK = build_task(
    DecisionRequest(
        instruction="Is it urgent?",
        evidence="Server down",
        schema={
            "type": "object",
            "properties": {"urgent": {"type": "boolean", "description": "Urgent?"}},
        },
    )
)


@dataclass
class _Metered:
    settled: list[int] = field(default_factory=list)
    rejected: bool = False

    def settle(self, *, input_tokens: int, output_tokens: int = 0) -> None:
        self.settled.append(input_tokens)

    def reject(self) -> None:
        self.rejected = True


@dataclass
class _Meter:
    profiles: list[Mapping[str, object]] = field(default_factory=list)
    requests: list[_Metered] = field(default_factory=list)

    @asynccontextmanager
    async def __call__(self, profile: Mapping[str, object]) -> AsyncIterator[_Metered]:
        self.profiles.append(profile)
        metered = _Metered()
        self.requests.append(metered)
        yield metered


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], meter: _Meter
) -> TypesafeDecisionProvider:
    return TypesafeDecisionProvider(
        api_key="sk-test",
        base_url="https://typesafe.test/v1/",
        model="jev-latest",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        meter=meter,
    )


async def test_an_answer_is_metered_by_the_tokens_it_reports() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13",
                "answers": {"urgent": {"noul": 0.92}},
                "usage": {"input_tokens": 640},
            },
        )

    meter = _Meter()
    result = await _provider(handler, meter).decide(
        TASK, caller=CALLER, timeout_seconds=5
    )

    assert result.answers["urgent"].value is True
    assert result.answers["urgent"].confidence == pytest.approx(0.92)
    assert (result.provider, result.model) == ("typesafe", "jev-1.13")
    assert str(sent[0].url) == "https://typesafe.test/v1/systemone"
    assert sent[0].headers["authorization"] == "Bearer sk-test"
    assert json.loads(sent[0].content)["state"] == {"evidence": "Server down"}
    assert meter.requests[0].settled == [640]
    assert meter.profiles[0]["model_name"] == "typesafe:jev-latest"


async def test_a_refusal_costs_nothing_and_is_unavailable() -> None:
    meter = _Meter()

    with pytest.raises(DecisionUnavailableError) as raised:
        await _provider(lambda _: httpx.Response(401), meter).decide(
            TASK, caller=CALLER, timeout_seconds=5
        )

    assert raised.value.reason == "provider_error"
    assert meter.requests[0].rejected is True


async def test_a_server_error_is_unavailable_and_left_unconfirmed() -> None:
    meter = _Meter()

    with pytest.raises(DecisionUnavailableError):
        await _provider(lambda _: httpx.Response(502), meter).decide(
            TASK, caller=CALLER, timeout_seconds=5
        )

    assert meter.requests[0].rejected is False
    assert meter.requests[0].settled == []


async def test_an_answer_that_does_not_fit_is_invalid_output() -> None:
    meter = _Meter()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": {"urgent": {"noul": 3}}})

    with pytest.raises(DecisionUnavailableError) as raised:
        await _provider(handler, meter).decide(TASK, caller=CALLER, timeout_seconds=5)

    assert raised.value.reason == "invalid_output"


async def test_an_unreachable_provider_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(DecisionUnavailableError) as raised:
        await _provider(handler, _Meter()).decide(
            TASK, caller=CALLER, timeout_seconds=5
        )

    assert raised.value.reason == "transport"


async def test_a_slow_provider_is_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(DecisionUnavailableError) as raised:
        await _provider(handler, _Meter()).decide(
            TASK, caller=CALLER, timeout_seconds=5
        )

    assert raised.value.reason == "timeout"
