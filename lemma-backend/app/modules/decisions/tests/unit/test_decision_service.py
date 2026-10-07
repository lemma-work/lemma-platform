"""The service: admit, meter, bound, and never pass on an answer it did not check."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.decisions.config import DecisionsSettings
from app.modules.decisions.domain.answers import Answer, DecisionResult
from app.modules.decisions.domain.errors import (
    DecisionInvalidError,
    DecisionLimitedError,
    DecisionUnavailableError,
)
from app.modules.decisions.domain.request import (
    DecisionCaller,
    DecisionRequest,
    DecisionTask,
)
from app.modules.decisions.services.decision_service import DecisionService
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.execution import UsageExecutionContext

pytestmark = pytest.mark.unit

ORG = uuid4()
CALLER = DecisionCaller(
    user_id=uuid4(),
    organization_id=ORG,
    pod_id=uuid4(),
    workload_type="function",
    workload_id=uuid4(),
    source_id="function:x",
)
REQUEST = DecisionRequest(
    instruction="Is it urgent?",
    evidence="Server down",
    schema={
        "type": "object",
        "properties": {
            "urgent": {"type": "boolean", "description": "Urgent?"},
            "team": {"type": "string", "enum": ["ops", "dev"], "description": "Who?"},
        },
    },
)


@dataclass
class _Provider:
    answers: dict[str, Answer] | None = None
    raises: BaseException | None = None
    delay: float = 0.0
    calls: list[DecisionTask] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake"

    async def decide(
        self, task: DecisionTask, *, caller: DecisionCaller, timeout_seconds: float
    ) -> DecisionResult:
        self.calls.append(task)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return DecisionResult(answers=self.answers or {}, provider="fake", model="m")


@dataclass
class _Limiter:
    wait: int | None = None
    keys: list[str] = field(default_factory=list)

    async def retry_after(self, key: str) -> int | None:
        self.keys.append(key)
        return self.wait


@dataclass
class _Metering:
    contexts: list[UsageExecutionContext] = field(default_factory=list)

    @asynccontextmanager
    async def __call__(self, context: UsageExecutionContext) -> AsyncIterator[object]:
        self.contexts.append(context)
        yield object()


GOOD = {"urgent": Answer(True, 0.9), "team": Answer(None)}


def _service(
    provider: _Provider,
    *,
    limiter: _Limiter | None = None,
    metering: _Metering | None = None,
    timeout: float = 5.0,
) -> DecisionService:
    return DecisionService(
        settings=DecisionsSettings(
            decision_background_timeout_seconds=timeout,
            decision_interactive_timeout_seconds=timeout,
        ),
        provider=lambda _: provider,
        limiter=limiter or _Limiter(),
        metering=metering or _Metering(),
    )


async def test_a_checked_answer_comes_back_metered_to_the_caller() -> None:
    metering = _Metering()
    limiter = _Limiter()

    result = await _service(_Provider(GOOD), limiter=limiter, metering=metering).decide(
        REQUEST, CALLER
    )

    assert result.answers == GOOD
    assert limiter.keys == [f"org:{ORG}"]
    context = metering.contexts[0]
    assert (context.user_id, context.organization_id, context.pod_id) == (
        CALLER.user_id,
        CALLER.organization_id,
        CALLER.pod_id,
    )
    assert (context.source_type, context.workload_type, context.workload_id) == (
        "decision",
        "function",
        CALLER.workload_id,
    )


async def test_an_invalid_request_never_reaches_the_provider() -> None:
    provider = _Provider(GOOD)

    with pytest.raises(DecisionInvalidError):
        await _service(provider).decide(
            DecisionRequest(instruction="x", evidence="y", schema={}), CALLER
        )

    assert provider.calls == []


async def test_over_the_rate_limit_is_refused_before_the_provider() -> None:
    provider = _Provider(GOOD)

    with pytest.raises(DecisionLimitedError) as raised:
        await _service(provider, limiter=_Limiter(wait=17)).decide(REQUEST, CALLER)

    assert raised.value.retry_after_seconds == 17
    assert provider.calls == []


async def test_a_provider_answer_outside_the_questions_is_unavailable() -> None:
    for answers in (
        {"urgent": Answer("yes"), "team": Answer("ops")},
        {"urgent": Answer(True)},
        {**GOOD, "extra": Answer(True)},
        {"urgent": Answer(True, 1.5), "team": Answer("ops")},
    ):
        with pytest.raises(DecisionUnavailableError) as raised:
            await _service(_Provider(answers)).decide(REQUEST, CALLER)
        assert raised.value.reason == "invalid_output", answers


async def test_a_slow_provider_is_a_timeout() -> None:
    with pytest.raises(DecisionUnavailableError) as raised:
        await _service(_Provider(GOOD, delay=1.0), timeout=0.05).decide(REQUEST, CALLER)

    assert raised.value.reason == "timeout"


async def test_a_spend_limit_is_passed_on_as_itself() -> None:
    with pytest.raises(UsageLimitExceededError):
        await _service(_Provider(raises=UsageLimitExceededError())).decide(
            REQUEST, CALLER
        )


async def test_a_server_failure_from_the_runtime_is_unavailable() -> None:
    failures = [
        (DomainError("boom", code="X", status_code=502), "provider_error"),
        (
            DomainError("No model", code="model_not_configured", status_code=503),
            "not_configured",
        ),
    ]
    for failure, reason in failures:
        with pytest.raises(DecisionUnavailableError) as raised:
            await _service(_Provider(raises=failure)).decide(REQUEST, CALLER)
        assert raised.value.reason == reason


async def test_a_person_without_an_organization_is_limited_on_their_own() -> None:
    limiter = _Limiter()
    caller = DecisionCaller(user_id=uuid4(), organization_id=None, pod_id=None)

    await _service(_Provider(GOOD), limiter=limiter).decide(REQUEST, caller)

    assert limiter.keys == [f"user:{caller.user_id}"]


async def test_a_spent_budget_survives_a_failed_usage_checkpoint() -> None:
    """When the provider is refused for spend and the usage checkpoint fails
    too, the metering scope raises both as a group. The caller must still see
    the spend limit -- a 429 it should not retry into -- not a retryable 503."""

    @asynccontextmanager
    async def failing_checkpoint(_: UsageExecutionContext) -> AsyncIterator[object]:
        try:
            yield object()
        except UsageLimitExceededError as failure:
            raise BaseExceptionGroup(
                "Execution and usage finalization failed",
                [failure, RuntimeError("checkpoint lost")],
            ) from None

    service = DecisionService(
        settings=DecisionsSettings(),
        provider=lambda _: _Provider(raises=UsageLimitExceededError()),
        limiter=_Limiter(),
        metering=failing_checkpoint,
    )

    with pytest.raises(UsageLimitExceededError):
        await service.decide(REQUEST, CALLER)


async def test_any_other_double_failure_is_retryable() -> None:
    @asynccontextmanager
    async def failing_checkpoint(_: UsageExecutionContext) -> AsyncIterator[object]:
        try:
            yield object()
        except DecisionUnavailableError as failure:
            raise BaseExceptionGroup(
                "Execution and usage finalization failed",
                [failure, RuntimeError("checkpoint lost")],
            ) from None

    service = DecisionService(
        settings=DecisionsSettings(),
        provider=lambda _: _Provider(raises=DecisionUnavailableError("transport")),
        limiter=_Limiter(),
        metering=failing_checkpoint,
    )

    with pytest.raises(DecisionUnavailableError) as raised:
        await service.decide(REQUEST, CALLER)
    assert raised.value.reason == "provider_error"
