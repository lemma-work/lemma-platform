from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.modules.function.application import function_observability as observability
from app.modules.function.application.function_observability import (
    FunctionPhaseTimings,
)
from app.modules.function.domain.entities import FunctionRunEntity, FunctionRunStatus


class _Instrument:
    def __init__(self) -> None:
        self.measurements: list[tuple[float, dict[str, object]]] = []

    def add(self, value, attributes) -> None:
        self.measurements.append((value, attributes))

    def record(self, value, attributes) -> None:
        self.measurements.append((value, attributes))


def test_terminal_observation_records_bounded_metrics_and_phase_durations(
    monkeypatch,
) -> None:
    instruments = {
        name: _Instrument()
        for name in (
            "function_executions",
            "function_end_to_end_duration",
            "function_queue_wait_duration",
            "function_sandbox_start_duration",
            "function_runtime_duration",
        )
    }
    for name, instrument in instruments.items():
        monkeypatch.setattr(observability, name, instrument)
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
        outcome="completed",
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

    count, attributes = instruments["function_executions"].measurements[0]
    assert count == 1
    assert attributes == {
        "execution_mode": "asynchronous",
        "runtime_profile": "function-python-v1",
        "outcome": "completed",
        "cold": True,
    }
    assert all(
        forbidden not in attributes
        for forbidden in ("run_id", "function_id", "pod_id", "user_id", "sandbox_id")
    )
    assert instruments["function_end_to_end_duration"].measurements[0][0] == 1875
    assert instruments["function_queue_wait_duration"].measurements[0][0] == 125
    assert instruments["function_sandbox_start_duration"].measurements[0][0] == 300
    assert instruments["function_runtime_duration"].measurements[0][0] == 1200
    assert logged[0][0] == "function.execution.completed"
    assert logged[0][1]["duration_ms"] == 1875
    assert logged[0][1]["finalization_ms"] == 15


def test_failure_fingerprint_never_uses_error_message(monkeypatch) -> None:
    monkeypatch.setattr(observability, "function_executions", _Instrument())
    for name in (
        "function_end_to_end_duration",
        "function_queue_wait_duration",
        "function_sandbox_start_duration",
        "function_runtime_duration",
    ):
        monkeypatch.setattr(observability, name, _Instrument())
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
        outcome="failed",
        execution_mode="synchronous",
        runtime_profile="function-python-v1",
        phases=FunctionPhaseTimings(),
        error_type="RuntimeError",
        error_code="RUNTIME_FAILED",
    )

    assert logged[0]["error_type"] == "RuntimeError"
    assert logged[0]["error_code"] == "RUNTIME_FAILED"
    assert len(str(logged[0]["error_stack_hash"])) == 64
