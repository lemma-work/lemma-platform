"""Bounded OpenTelemetry and terminal events for function execution."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Iterator, Literal

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from app.core.log.log import get_logger
from app.modules.function.domain.entities import FunctionRunEntity


FunctionOutcome = Literal[
    "completed",
    "failed",
    "timed_out",
    "cancelled",
    "rejected",
]

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

function_executions = meter.create_counter(
    "lemma.function.executions",
    description="Terminal function executions",
)
function_end_to_end_duration = meter.create_histogram(
    "lemma.function.end_to_end.duration",
    unit="ms",
    description="Time from accepted function run to durable terminal state",
)
function_queue_wait_duration = meter.create_histogram(
    "lemma.function.queue_wait.duration",
    unit="ms",
    description="Time from accepted function run to backend dispatch",
)
function_sandbox_start_duration = meter.create_histogram(
    "lemma.function.sandbox_start.duration",
    unit="ms",
    description="Time to obtain a ready AgentBox function runtime endpoint",
)
function_runtime_duration = meter.create_histogram(
    "lemma.function.runtime.duration",
    unit="ms",
    description="Active function runtime invocation duration",
)
function_active = meter.create_up_down_counter(
    "lemma.function.active",
    description="Function dispatches currently active in this service",
)


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


def function_attributes(
    *,
    execution_mode: str,
    runtime_profile: str,
    outcome: FunctionOutcome | None = None,
    cold: bool | None = None,
) -> dict[str, str | bool]:
    """Return only finite dimensions approved by the observability contract."""

    attributes: dict[str, str | bool] = {
        "execution_mode": execution_mode.lower(),
        "runtime_profile": runtime_profile,
    }
    if outcome is not None:
        attributes["outcome"] = outcome
    if cold is not None:
        attributes["cold"] = cold
    return attributes


def span_attributes(
    *,
    execution_mode: str,
    runtime_profile: str,
    outcome: FunctionOutcome | None = None,
    cold: bool | None = None,
) -> dict[str, str | bool]:
    return {
        f"lemma.{key}": value
        for key, value in function_attributes(
            execution_mode=execution_mode,
            runtime_profile=runtime_profile,
            outcome=outcome,
            cold=cold,
        ).items()
    }


@contextmanager
def function_span(
    name: str,
    *,
    execution_mode: str,
    runtime_profile: str,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[Span]:
    with tracer.start_as_current_span(
        name,
        kind=kind,
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
    if outcome in {"failed", "timed_out", "rejected"}:
        span.set_status(Status(StatusCode.ERROR, error_type or outcome))


def record_active(
    delta: int,
    *,
    execution_mode: str,
    runtime_profile: str,
) -> None:
    function_active.add(
        delta,
        function_attributes(
            execution_mode=execution_mode,
            runtime_profile=runtime_profile,
        ),
    )


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
    """Emit one terminal event and its matching bounded metric measurements."""

    total_ms = duration_ms(run.created_at, run.completed_at)
    metric_attributes = function_attributes(
        execution_mode=execution_mode,
        runtime_profile=runtime_profile,
        outcome=outcome,
        cold=phases.cold,
    )
    function_executions.add(1, metric_attributes)
    if total_ms is not None:
        function_end_to_end_duration.record(total_ms, metric_attributes)
    if phases.queue_wait_ms is not None:
        function_queue_wait_duration.record(
            max(0.0, phases.queue_wait_ms), metric_attributes
        )
    if phases.sandbox_start_ms is not None:
        function_sandbox_start_duration.record(
            max(0.0, phases.sandbox_start_ms), metric_attributes
        )
    if phases.runtime_call_ms is not None:
        function_runtime_duration.record(
            max(0.0, phases.runtime_call_ms), metric_attributes
        )

    fingerprint = None
    if error_type is not None:
        fingerprint = hashlib.sha256(
            f"{outcome}:{error_type}:{error_code or ''}".encode()
        ).hexdigest()
    if outcome == "completed":
        logger.info(
            "function.execution.completed",
            run_id=str(run.id) if run.id is not None else None,
            function_id=str(run.function_id),
            operation_name="function.execute",
            outcome=outcome,
            duration_ms=total_ms,
            queue_wait_ms=phases.queue_wait_ms,
            sandbox_start_ms=phases.sandbox_start_ms,
            runtime_call_ms=phases.runtime_call_ms,
            finalization_ms=phases.finalization_ms,
            execution_mode=execution_mode.lower(),
            runtime_profile=runtime_profile,
            cold=phases.cold,
            error_type=error_type,
            error_code=error_code,
            error_stack_hash=fingerprint,
        )
    elif outcome == "failed":
        logger.error(
            "function.execution.failed",
            run_id=str(run.id) if run.id is not None else None,
            function_id=str(run.function_id),
            operation_name="function.execute",
            outcome=outcome,
            duration_ms=total_ms,
            queue_wait_ms=phases.queue_wait_ms,
            sandbox_start_ms=phases.sandbox_start_ms,
            runtime_call_ms=phases.runtime_call_ms,
            finalization_ms=phases.finalization_ms,
            execution_mode=execution_mode.lower(),
            runtime_profile=runtime_profile,
            cold=phases.cold,
            error_type=error_type,
            error_code=error_code,
            error_stack_hash=fingerprint,
        )
    elif outcome == "timed_out":
        logger.warning(
            "function.execution.timed_out",
            run_id=str(run.id) if run.id is not None else None,
            function_id=str(run.function_id),
            operation_name="function.execute",
            outcome=outcome,
            duration_ms=total_ms,
            queue_wait_ms=phases.queue_wait_ms,
            sandbox_start_ms=phases.sandbox_start_ms,
            runtime_call_ms=phases.runtime_call_ms,
            finalization_ms=phases.finalization_ms,
            execution_mode=execution_mode.lower(),
            runtime_profile=runtime_profile,
            cold=phases.cold,
            error_type=error_type,
            error_code=error_code,
            error_stack_hash=fingerprint,
        )
    elif outcome == "cancelled":
        logger.info(
            "function.execution.cancelled",
            run_id=str(run.id) if run.id is not None else None,
            function_id=str(run.function_id),
            operation_name="function.execute",
            outcome=outcome,
            duration_ms=total_ms,
            queue_wait_ms=phases.queue_wait_ms,
            sandbox_start_ms=phases.sandbox_start_ms,
            runtime_call_ms=phases.runtime_call_ms,
            finalization_ms=phases.finalization_ms,
            execution_mode=execution_mode.lower(),
            runtime_profile=runtime_profile,
            cold=phases.cold,
            error_type=error_type,
            error_code=error_code,
            error_stack_hash=fingerprint,
        )
    else:
        logger.warning(
            "function.execution.rejected",
            run_id=str(run.id) if run.id is not None else None,
            function_id=str(run.function_id),
            operation_name="function.execute",
            outcome=outcome,
            duration_ms=total_ms,
            queue_wait_ms=phases.queue_wait_ms,
            sandbox_start_ms=phases.sandbox_start_ms,
            runtime_call_ms=phases.runtime_call_ms,
            finalization_ms=phases.finalization_ms,
            execution_mode=execution_mode.lower(),
            runtime_profile=runtime_profile,
            cold=phases.cold,
            error_type=error_type,
            error_code=error_code,
            error_stack_hash=fingerprint,
        )
