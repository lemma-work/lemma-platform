"""Ask one decision: check it, admit it, meter it, bound it, check the answer.

Nothing is stored. The caller owns what the decision is about, whether it was
asked before, and what is kept of it afterwards -- a schedule's run row, a
workflow step's output, a function's own table. That is what lets this be one
call with no visibility rules, no retention and no identity of its own.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.decisions.config import DecisionsSettings, decisions_settings
from app.modules.decisions.domain.answers import (
    Answer,
    DecisionResult,
    answer_problems,
    to_json,
)
from app.modules.decisions.domain.errors import (
    DecisionLimitedError,
    DecisionUnavailableError,
)
from app.modules.decisions.domain.ports import DecisionProvider, DecisionRateLimiter
from app.modules.decisions.domain.request import (
    DecisionCaller,
    DecisionRequest,
    DecisionTask,
    build_task,
)
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.execution import UsageExecutionContext

logger = get_logger(__name__)

MeteringOpener = Callable[[UsageExecutionContext], AbstractAsyncContextManager[object]]

#: What the model runtime raises when the deployment has no model at all. Kept
#: distinct from a provider failure: it is fixed in settings, not by retrying.
_MODEL_NOT_CONFIGURED = "model_not_configured"


class DecisionService:
    def __init__(
        self,
        *,
        settings: DecisionsSettings | None = None,
        provider: Callable[[DecisionsSettings], DecisionProvider] | None = None,
        limiter: DecisionRateLimiter | None = None,
        metering: MeteringOpener | None = None,
    ) -> None:
        self._settings = settings or decisions_settings
        self._provider = provider
        self._limiter = limiter
        self._metering = metering

    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult:
        task = build_task(request)
        retry_after = await self._rate_limiter().retry_after(_rate_key(caller))
        if retry_after is not None:
            raise DecisionLimitedError(retry_after)
        provider = self._build_provider()
        timeout = self._timeout(task)
        started = time.monotonic()
        result = await self._ask(provider, task, caller, timeout)
        checked = _checked(task, result)
        logger.info(
            "decisions.decision_service.decided.observed",
            provider=checked.provider,
            model=checked.model,
            priority=task.priority,
            source_type=caller.source_type,
            questions=len(task.schema.questions),
            unsure=sum(
                1 for answer in checked.answers.values() if answer.value is None
            ),
            input_tokens=checked.usage.input_tokens,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return checked

    async def _ask(
        self,
        provider: DecisionProvider,
        task: DecisionTask,
        caller: DecisionCaller,
        timeout: float,
    ) -> DecisionResult:
        try:
            async with self._open_metering(caller):
                async with asyncio.timeout(timeout):
                    return await provider.decide(
                        task, caller=caller, timeout_seconds=timeout
                    )
        except TimeoutError as exc:
            raise DecisionUnavailableError("timeout") from exc
        except DomainError as exc:
            # A 429 (a spend limit) and a 4xx are the caller's to see as they
            # are. Anything a provider or the model runtime raised as a server
            # failure is, to the caller, a provider that did not answer.
            if exc.status_code < 500 or isinstance(exc, DecisionUnavailableError):
                raise
            reason = (
                "not_configured"
                if exc.code == _MODEL_NOT_CONFIGURED
                else "provider_error"
            )
            raise DecisionUnavailableError(reason, exc.message) from exc
        except BaseExceptionGroup as group:
            # The decision and the usage checkpoint both failed. A cancellation
            # and a spent budget keep their own meaning -- the second is a 429
            # the caller must not retry into; anything else is retryable.
            if group.subgroup(asyncio.CancelledError) is not None:
                raise
            spent = group.subgroup(UsageLimitExceededError)
            if spent is not None:
                raise _first(spent) from group
            raise DecisionUnavailableError("provider_error") from group

    def _timeout(self, task: DecisionTask) -> float:
        if task.priority == "interactive":
            return self._settings.decision_interactive_timeout_seconds
        return self._settings.decision_background_timeout_seconds

    def _build_provider(self) -> DecisionProvider:
        if self._provider is not None:
            return self._provider(self._settings)
        from app.modules.decisions.infrastructure.providers.registry import (
            build_provider,
        )

        return build_provider(self._settings)

    def _rate_limiter(self) -> DecisionRateLimiter:
        if self._limiter is not None:
            return self._limiter
        from app.modules.decisions.infrastructure.rate_limit import (
            OrganizationRateLimiter,
        )

        return OrganizationRateLimiter(
            limit_per_minute=self._settings.decision_rate_limit_per_minute
        )

    def _open_metering(
        self, caller: DecisionCaller
    ) -> AbstractAsyncContextManager[object]:
        context = UsageExecutionContext(
            user_id=caller.user_id,
            organization_id=caller.organization_id,
            pod_id=caller.pod_id,
            agent_id=caller.agent_id,
            source_type=caller.source_type,
            source_id=caller.source_id,
            workload_type=caller.workload_type,
            workload_id=caller.workload_id,
        )
        if self._metering is not None:
            return self._metering(context)
        from app.modules.usage.contracts.metering import metering_execution

        return metering_execution(context)


def _first(group: BaseExceptionGroup[BaseException]) -> BaseException:
    """The first leaf of a group, however deeply it is nested."""
    leaf = group.exceptions[0]
    return _first(leaf) if isinstance(leaf, BaseExceptionGroup) else leaf


def _rate_key(caller: DecisionCaller) -> str:
    if caller.organization_id is not None:
        return f"org:{caller.organization_id}"
    return f"user:{caller.user_id}"


def _checked(task: DecisionTask, result: DecisionResult) -> DecisionResult:
    """The provider's answers, if they answer exactly the questions asked.

    The one place the promise "every provider answers every kind, within the
    question" is enforced. A provider that breaks it did not answer.
    """
    raw = {key: to_json(answer.value) for key, answer in result.answers.items()}
    problems = answer_problems(task.schema, raw)
    confidences_valid = all(
        answer.confidence is None or 0.0 <= answer.confidence <= 1.0
        for answer in result.answers.values()
    )
    if problems or not confidences_valid:
        logger.error(
            "decisions.decision_service.provider_answer_invalid.degraded",
            provider=result.provider,
            problems=len(problems),
        )
        raise DecisionUnavailableError("invalid_output")
    return DecisionResult(
        answers={
            key: Answer(result.answers[key].value, result.answers[key].confidence)
            for key in task.schema.keys
        },
        provider=result.provider,
        model=result.model,
        usage=result.usage,
    )
