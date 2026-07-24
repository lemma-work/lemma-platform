"""Minimal domain telemetry for durable function execution phases."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import sys
import time
from typing import Iterator, Literal

import httpx
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from agentbox_client import AgentBoxApiError, RetryDisposition

from app.core.log.log import get_logger
from app.modules.function.domain.entities import FunctionRunEntity


FunctionOutcome = Literal[
    "success",
    "failure",
    "timeout",
    "cancelled",
    "rejected",
]
FunctionPhaseName = Literal[
    "function.execution.accepted",
    "function.agentbox.admission",
    "function.runtime.call",
    "function.execution.finalize",
]

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
_EXECUTION_MODES = frozenset({"synchronous", "asynchronous"})
_RUNTIME_PROFILE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


@dataclass(slots=True)
class FunctionPhaseTimings:
    """Durations measured at bounded execution phases.

    Values remain optional because old runtime images can legitimately omit
    phases during a rolling deployment. Missing values are never synthesized as
    zero.
    """

    queue_wait_ms: float | None = None
    sandbox_start_ms: float | None = None
    runtime_call_ms: float | None = None
    finalization_ms: float | None = None
    cold: bool | None = None


def duration_ms(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    return round(max(0.0, (ended_at - started_at).total_seconds() * 1000), 3)


def _bounded_execution_mode(value: str) -> str:
    normalized = value.lower()
    return normalized if normalized in _EXECUTION_MODES else "other"


def _bounded_runtime_profile(value: str) -> str:
    return value if _RUNTIME_PROFILE_RE.fullmatch(value) else "other"


def span_attributes(*, execution_mode: str, runtime_profile: str) -> dict[str, str]:
    """Return only reviewed, finite attributes for manual domain spans."""

    return {
        "lemma.execution_mode": _bounded_execution_mode(execution_mode),
        "lemma.runtime_profile": _bounded_runtime_profile(runtime_profile),
    }


@contextmanager
def function_span(
    name: FunctionPhaseName,
    *,
    execution_mode: str,
    runtime_profile: str,
) -> Iterator[Span]:
    """Create one INTERNAL span around a durable domain phase.

    FastAPI, HTTPX, SQLAlchemy, and queue instrumentation own their transport
    spans. These manual spans group those children without pretending to be a
    second client, producer, or server boundary.
    """

    with tracer.start_as_current_span(
        name,
        attributes=span_attributes(
            execution_mode=execution_mode,
            runtime_profile=runtime_profile,
        ),
    ) as span:
        yield span


def mark_span_outcome(
    span: Span,
    outcome: FunctionOutcome,
    *,
    error_type: str | None = None,
) -> None:
    span.set_attribute("lemma.outcome", outcome)
    if error_type is not None:
        span.set_attribute("error.type", error_type)
    if outcome in {"failure", "timeout", "rejected"}:
        span.set_status(Status(StatusCode.ERROR, error_type or outcome))


def record_function_accepted(
    *,
    execution_mode: str,
    runtime_profile: str,
) -> None:
    """Record the durable accepted milestone outside queue transport spans."""

    with function_span(
        "function.execution.accepted",
        execution_mode=execution_mode,
        runtime_profile=runtime_profile,
    ) as span:
        mark_span_outcome(span, "success")


def exception_outcome(
    exc: BaseException,
) -> FunctionOutcome:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, AgentBoxApiError) and exc.retry not in {
        RetryDisposition.WAIT,
        RetryDisposition.SAFE_SAME_OPERATION,
    }:
        return "rejected"
    if isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
        return "rejected"
    return "failure"


def exception_error_code(
    exc: BaseException,
) -> str | None:
    if isinstance(exc, AgentBoxApiError):
        code = str(getattr(exc, "code", "PROVIDER_UNAVAILABLE"))
        return code if re.fullmatch(r"[A-Z0-9_.-]{1,64}", code) else None
    if isinstance(exc, httpx.HTTPStatusError):
        return str(exc.response.status_code)
    return None


@contextmanager
def observe_function_phase(
    name: FunctionPhaseName,
    *,
    execution_mode: str,
    runtime_profile: str,
    phases: FunctionPhaseTimings | None = None,
    duration_field: Literal["sandbox_start_ms", "runtime_call_ms"] | None = None,
) -> Iterator[None]:
    """Observe one durable phase without intercepting failure or cancellation."""

    started = time.perf_counter()
    with function_span(
        name,
        execution_mode=execution_mode,
        runtime_profile=runtime_profile,
    ) as span:
        try:
            yield
        finally:
            caught = sys.exception()
            if caught is None:
                mark_span_outcome(span, "success")
            else:
                mark_span_outcome(
                    span,
                    exception_outcome(caught),
                    error_type=type(caught).__name__,
                )
            if phases is not None and duration_field is not None:
                setattr(
                    phases,
                    duration_field,
                    round((time.perf_counter() - started) * 1000, 3),
                )


def _error_fingerprint(
    *,
    outcome: FunctionOutcome,
    error_type: str | None,
    error_code: str | None,
) -> str | None:
    if error_type is None:
        return None
    return hashlib.sha256(
        f"{outcome}:{error_type}:{error_code or ''}".encode()
    ).hexdigest()


def _terminal_fields(
    run: FunctionRunEntity,
    *,
    outcome: FunctionOutcome,
    execution_mode: str,
    runtime_profile: str,
    phases: FunctionPhaseTimings,
    error_type: str | None,
    error_code: str | None,
) -> dict[str, str | float | bool | None]:
    return {
        "run_id": str(run.id) if run.id is not None else None,
        "operation_name": "function.execute",
        "outcome": outcome,
        "duration_ms": duration_ms(run.created_at, run.completed_at),
        "queue_wait_ms": phases.queue_wait_ms,
        "sandbox_start_ms": phases.sandbox_start_ms,
        "runtime_call_ms": phases.runtime_call_ms,
        "finalization_ms": phases.finalization_ms,
        "execution_mode": _bounded_execution_mode(execution_mode),
        "runtime_profile": _bounded_runtime_profile(runtime_profile),
        "cold": phases.cold,
        "error_type": error_type,
        "error_code": error_code,
        "error_stack_hash": _error_fingerprint(
            outcome=outcome,
            error_type=error_type,
            error_code=error_code,
        ),
    }


def record_terminal(
    run: FunctionRunEntity,
    *,
    outcome: FunctionOutcome,
    execution_mode: str,
    runtime_profile: str,
    phases: FunctionPhaseTimings,
    error_type: str | None = None,
    error_code: str | None = None,
) -> None:
    """Emit the one unsampled terminal event for a durable function run."""

    fields = _terminal_fields(
        run,
        outcome=outcome,
        execution_mode=execution_mode,
        runtime_profile=runtime_profile,
        phases=phases,
        error_type=error_type,
        error_code=error_code,
    )

    match outcome:
        case "success":
            logger.info(
                "function.execution.completed",
                run_id=fields["run_id"],
                operation_name=fields["operation_name"],
                outcome=fields["outcome"],
                duration_ms=fields["duration_ms"],
                queue_wait_ms=fields["queue_wait_ms"],
                sandbox_start_ms=fields["sandbox_start_ms"],
                runtime_call_ms=fields["runtime_call_ms"],
                finalization_ms=fields["finalization_ms"],
                execution_mode=fields["execution_mode"],
                runtime_profile=fields["runtime_profile"],
                cold=fields["cold"],
                error_type=fields["error_type"],
                error_code=fields["error_code"],
                error_stack_hash=fields["error_stack_hash"],
            )
        case "failure":
            logger.error(
                "function.execution.failed",
                run_id=fields["run_id"],
                operation_name=fields["operation_name"],
                outcome=fields["outcome"],
                duration_ms=fields["duration_ms"],
                queue_wait_ms=fields["queue_wait_ms"],
                sandbox_start_ms=fields["sandbox_start_ms"],
                runtime_call_ms=fields["runtime_call_ms"],
                finalization_ms=fields["finalization_ms"],
                execution_mode=fields["execution_mode"],
                runtime_profile=fields["runtime_profile"],
                cold=fields["cold"],
                error_type=fields["error_type"],
                error_code=fields["error_code"],
                error_stack_hash=fields["error_stack_hash"],
            )
        case "timeout":
            logger.warning(
                "function.execution.timed_out",
                run_id=fields["run_id"],
                operation_name=fields["operation_name"],
                outcome=fields["outcome"],
                duration_ms=fields["duration_ms"],
                queue_wait_ms=fields["queue_wait_ms"],
                sandbox_start_ms=fields["sandbox_start_ms"],
                runtime_call_ms=fields["runtime_call_ms"],
                finalization_ms=fields["finalization_ms"],
                execution_mode=fields["execution_mode"],
                runtime_profile=fields["runtime_profile"],
                cold=fields["cold"],
                error_type=fields["error_type"],
                error_code=fields["error_code"],
                error_stack_hash=fields["error_stack_hash"],
            )
        case "cancelled":
            logger.info(
                "function.execution.cancelled",
                run_id=fields["run_id"],
                operation_name=fields["operation_name"],
                outcome=fields["outcome"],
                duration_ms=fields["duration_ms"],
                queue_wait_ms=fields["queue_wait_ms"],
                sandbox_start_ms=fields["sandbox_start_ms"],
                runtime_call_ms=fields["runtime_call_ms"],
                finalization_ms=fields["finalization_ms"],
                execution_mode=fields["execution_mode"],
                runtime_profile=fields["runtime_profile"],
                cold=fields["cold"],
                error_type=fields["error_type"],
                error_code=fields["error_code"],
                error_stack_hash=fields["error_stack_hash"],
            )
        case "rejected":
            logger.warning(
                "function.execution.rejected",
                run_id=fields["run_id"],
                operation_name=fields["operation_name"],
                outcome=fields["outcome"],
                duration_ms=fields["duration_ms"],
                queue_wait_ms=fields["queue_wait_ms"],
                sandbox_start_ms=fields["sandbox_start_ms"],
                runtime_call_ms=fields["runtime_call_ms"],
                finalization_ms=fields["finalization_ms"],
                execution_mode=fields["execution_mode"],
                runtime_profile=fields["runtime_profile"],
                cold=fields["cold"],
                error_type=fields["error_type"],
                error_code=fields["error_code"],
                error_stack_hash=fields["error_stack_hash"],
            )
