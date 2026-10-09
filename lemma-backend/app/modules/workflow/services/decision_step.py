"""Asking the decision a workflow step waits on, and what each failure means.

Three short steps: read what is asked (one transaction), ask it (no
transaction), route the run on the answer (another). The middle one is the slow
one -- a model call up to the decision's own deadline -- which is why nothing
holds a pooled connection or the run's row lock across it.

How a decision fails decides what happens to the run, and no failure ever takes
a branch: a step that routes on a judgement must not route on the absence of
one.

- The provider did not answer (timeout, transport, a provider error, an answer
  that did not fit) or the organization asked too fast: retry with backoff,
  then fail the run naming the cause.
- The question cannot be asked as stored, the spend limit is reached, the
  evidence needs more tokens than a decision may use, or no provider is
  configured: retrying cannot help, so fail the run now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.modules.decisions.contracts import (
    DecisionInvalidError,
    DecisionLimitedError,
    DecisionMaker,
    DecisionResult,
    DecisionUnavailableError,
)
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.workflow.services.decision_resume_service import PendingDecision

#: Attempts per queued job, the first included. Each attempt is bounded by the
#: decision's own background deadline, and the backoff below keeps the whole
#: run of them inside the reconciliation sweep's grace period.
DECISION_STEP_MAX_ATTEMPTS = 6
_FIRST_DELAY_SECONDS = 5
_MAX_DELAY_SECONDS = 120
#: Unavailable for a reason the same request cannot get past.
_NOT_RETRYABLE = frozenset({"token_limit", "not_configured"})


class DecisionWaits(Protocol):
    """The transaction-side steps, each in a unit of work of its own."""

    async def pending_decision(self, external_ref: str) -> PendingDecision | None:
        """What is asked, or None when the run no longer waits on it."""

    async def route_decision(self, external_ref: str, result: DecisionResult) -> None:
        """Resume the run on the answer's route, or fail it if there is none."""

    async def fail_decision(self, external_ref: str, error: str) -> None:
        """Fail the run waiting on this decision."""


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Ask again after this long; the job queue does the waiting."""

    delay_seconds: int


async def ask_waiting_decision(
    external_ref: str,
    *,
    attempt: int,
    waits: DecisionWaits,
    maker: DecisionMaker,
    max_attempts: int = DECISION_STEP_MAX_ATTEMPTS,
) -> RetryDecision | None:
    """Ask the decision `external_ref` names and act on what comes back.

    Returns a retry for the caller to schedule, or None when the run has been
    resumed, failed, or was not waiting any more (cancelled, already answered
    by a duplicate job).
    """
    pending = await waits.pending_decision(external_ref)
    if pending is None:
        return None
    try:
        result = await maker.decide(pending.request, pending.caller)
    except DecisionLimitedError as exc:
        return await _retry_or_fail(
            external_ref,
            exc.message,
            attempt=attempt,
            max_attempts=max_attempts,
            waits=waits,
            at_least=exc.retry_after_seconds,
        )
    except DecisionUnavailableError as exc:
        if exc.reason in _NOT_RETRYABLE:
            await waits.fail_decision(
                external_ref, f"The question was not asked: {exc.message}"
            )
            return None
        return await _retry_or_fail(
            external_ref,
            exc.message,
            attempt=attempt,
            max_attempts=max_attempts,
            waits=waits,
        )
    except DecisionInvalidError as exc:
        await waits.fail_decision(external_ref, _invalid(exc))
        return None
    except UsageLimitExceededError as exc:
        await waits.fail_decision(
            external_ref, f"The question was not asked: {exc.message}."
        )
        return None
    await waits.route_decision(external_ref, result)
    return None


async def _retry_or_fail(
    external_ref: str,
    cause: str,
    *,
    attempt: int,
    max_attempts: int,
    waits: DecisionWaits,
    at_least: int = 0,
) -> RetryDecision | None:
    if attempt < max_attempts:
        backoff = min(_FIRST_DELAY_SECONDS * 2 ** (attempt - 1), _MAX_DELAY_SECONDS)
        return RetryDecision(delay_seconds=max(backoff, at_least))
    await waits.fail_decision(
        external_ref,
        f"The question went unanswered after {attempt} attempts: {cause}",
    )
    return None


def _invalid(exc: DecisionInvalidError) -> str:
    problems = "; ".join(
        f"{problem['path']}: {problem['message']}" for problem in exc.problems
    )
    return f"The question could not be asked: {problems or exc.message}"
