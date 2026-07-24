from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.function.application import function_observability as observability
from app.modules.function.application.function_observability import (
    FunctionPhaseTimings,
)
from app.modules.function.domain.entities import FunctionRunEntity, FunctionRunStatus


def test_accepted_milestone_is_a_successful_domain_span(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    fake_span = object()

    @contextmanager
    def fake_function_span(name, *, execution_mode, runtime_profile):
        calls.append((name, execution_mode, runtime_profile))
        yield fake_span

    monkeypatch.setattr(observability, "function_span", fake_function_span)
    monkeypatch.setattr(
        observability,
        "mark_span_outcome",
        lambda span, outcome, *, error_type=None: calls.append(
            (span, outcome, error_type)
        ),
    )

    observability.record_function_accepted(
        execution_mode="asynchronous",
        runtime_profile="function-python-v1",
    )

    assert calls == [
        (
            "function.execution.accepted",
            "asynchronous",
            "function-python-v1",
        ),
        (fake_span, "success", None),
    ]


def test_phase_observation_records_failed_elapsed_time(monkeypatch) -> None:
    timings = iter((10.0, 10.125))
    outcomes: list[tuple[str, str | None]] = []
    monkeypatch.setattr(observability.time, "perf_counter", lambda: next(timings))
    monkeypatch.setattr(
        observability,
        "mark_span_outcome",
        lambda _span, outcome, *, error_type=None: outcomes.append(
            (outcome, error_type)
        ),
    )
    phases = FunctionPhaseTimings()

    with pytest.raises(TimeoutError):
        with observability.observe_function_phase(
            "function.runtime.call",
            execution_mode="synchronous",
            runtime_profile="function-python-v1",
            phases=phases,
            duration_field="runtime_call_ms",
        ):
            raise TimeoutError

    assert phases.runtime_call_ms == 125
    assert outcomes == [("timeout", "TimeoutError")]
    assert observability.exception_outcome(asyncio.CancelledError()) == "cancelled"


def test_phase_observation_preserves_cancellation_and_elapsed_time(monkeypatch) -> None:
    timings = iter((20.0, 20.25))
    outcomes: list[tuple[str, str | None]] = []
    monkeypatch.setattr(observability.time, "perf_counter", lambda: next(timings))
    monkeypatch.setattr(
        observability,
        "mark_span_outcome",
        lambda _span, outcome, *, error_type=None: outcomes.append(
            (outcome, error_type)
        ),
    )
    phases = FunctionPhaseTimings()

    with pytest.raises(asyncio.CancelledError):
        with observability.observe_function_phase(
            "function.agentbox.admission",
            execution_mode="asynchronous",
            runtime_profile="function-python-v1",
            phases=phases,
            duration_field="sandbox_start_ms",
        ):
            raise asyncio.CancelledError

    assert phases.sandbox_start_ms == 250
    assert outcomes == [("cancelled", "CancelledError")]


def test_terminal_event_retains_all_phase_durations(monkeypatch) -> None:
    logged: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        observability,
        "logger",
        SimpleNamespace(
            info=lambda event, **fields: logged.append((event, fields)),
            warning=lambda event, **fields: logged.append((event, fields)),
            error=lambda event, **fields: logged.append((event, fields)),
        ),
    )
    accepted_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    completed_at = accepted_at + timedelta(milliseconds=1875)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=uuid4(),
        user_id=uuid4(),
        status=FunctionRunStatus.COMPLETED,
        created_at=accepted_at,
        completed_at=completed_at,
    )

    observability.record_terminal(
        run,
        outcome="success",
        execution_mode="asynchronous",
        runtime_profile="function-python-v1",
        phases=FunctionPhaseTimings(
            queue_wait_ms=125,
            sandbox_start_ms=300,
            runtime_call_ms=1200,
            finalization_ms=15,
            cold=True,
        ),
    )

    assert logged[0][0] == "function.execution.completed"
    assert logged[0][1]["outcome"] == "success"
    assert logged[0][1]["duration_ms"] == 1875
    assert logged[0][1]["queue_wait_ms"] == 125
    assert logged[0][1]["sandbox_start_ms"] == 300
    assert logged[0][1]["runtime_call_ms"] == 1200
    assert logged[0][1]["finalization_ms"] == 15
    assert logged[0][1]["run_id"] == str(run.id)
    assert "function_id" not in logged[0][1]


def test_failure_fingerprint_never_uses_error_message(monkeypatch) -> None:
    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        observability,
        "logger",
        SimpleNamespace(
            info=lambda _event, **fields: logged.append(fields),
            warning=lambda _event, **fields: logged.append(fields),
            error=lambda _event, **fields: logged.append(fields),
        ),
    )
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=uuid4(),
        user_id=uuid4(),
        status=FunctionRunStatus.FAILED,
    )

    observability.record_terminal(
        run,
        outcome="failure",
        execution_mode="synchronous",
        runtime_profile="function-python-v1",
        phases=FunctionPhaseTimings(),
        error_type="RuntimeError",
        error_code="RUNTIME_FAILED",
    )

    assert logged[0]["error_type"] == "RuntimeError"
    assert logged[0]["error_code"] == "RUNTIME_FAILED"
    assert len(str(logged[0]["error_stack_hash"])) == 64


@pytest.mark.parametrize(
    ("outcome", "severity", "event"),
    (
        ("success", "info", "function.execution.completed"),
        ("failure", "error", "function.execution.failed"),
        ("timeout", "warning", "function.execution.timed_out"),
        ("cancelled", "info", "function.execution.cancelled"),
        ("rejected", "warning", "function.execution.rejected"),
    ),
)
def test_canonical_outcomes_keep_stable_event_names(
    monkeypatch,
    outcome,
    severity,
    event,
) -> None:
    logged: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        observability,
        "logger",
        SimpleNamespace(
            info=lambda name, **fields: logged.append(("info", name, fields)),
            warning=lambda name, **fields: logged.append(("warning", name, fields)),
            error=lambda name, **fields: logged.append(("error", name, fields)),
        ),
    )
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=uuid4(),
        user_id=uuid4(),
        status=FunctionRunStatus.COMPLETED,
    )

    observability.record_terminal(
        run,
        outcome=outcome,
        execution_mode="synchronous",
        runtime_profile="function-python-v1",
        phases=FunctionPhaseTimings(),
    )

    assert logged[0][:2] == (severity, event)
    assert logged[0][2]["outcome"] == outcome


def test_span_and_log_dimensions_fail_closed_to_bounded_values(monkeypatch) -> None:
    assert observability.span_attributes(
        execution_mode="tenant-controlled-mode",
        runtime_profile="../../customer/profile",
    ) == {
        "lemma.execution_mode": "other",
        "lemma.runtime_profile": "other",
    }

    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        observability,
        "logger",
        SimpleNamespace(
            info=lambda _event, **fields: logged.append(fields),
            warning=lambda _event, **fields: logged.append(fields),
            error=lambda _event, **fields: logged.append(fields),
        ),
    )
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=uuid4(),
        user_id=uuid4(),
        status=FunctionRunStatus.COMPLETED,
    )

    observability.record_terminal(
        run,
        outcome="success",
        execution_mode="tenant-controlled-mode",
        runtime_profile="../../customer/profile",
        phases=FunctionPhaseTimings(),
    )

    assert logged[0]["execution_mode"] == "other"
    assert logged[0]["runtime_profile"] == "other"
