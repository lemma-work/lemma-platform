from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import UUID

from agentbox_client import WorkloadKind
import pytest

from load_tests.function_execution import (
    BenchmarkConfig,
    FunctionExecutionBenchmark,
    LatencyBudget,
    write_report,
)


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.real_sandbox,
]


_DEFAULT_TERMINAL_P95_SECONDS = {
    "docker": {
        "api_read": 45.0,
        "api_write": 90.0,
        "job_read": 45.0,
        "job_write": 90.0,
    },
    "e2b": {
        "api_read": 15.0,
        "api_write": 45.0,
        "job_read": 20.0,
        "job_write": 55.0,
    },
}


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _latency_budgets(provider: str) -> tuple[LatencyBudget, ...]:
    defaults = _DEFAULT_TERMINAL_P95_SECONDS[provider]
    budgets: list[LatencyBudget] = []
    for case, terminal_default in defaults.items():
        prefix = f"FUNCTION_BENCH_{case.upper()}"
        budgets.append(
            LatencyBudget(
                case=case,
                terminal_p95_seconds=_positive_float(
                    f"{prefix}_TERMINAL_P95_SECONDS", terminal_default
                ),
                submit_p95_seconds=(
                    _positive_float(f"{prefix}_SUBMIT_P95_SECONDS", 2.0)
                    if case.startswith("job_")
                    else None
                ),
            )
        )
    return tuple(budgets)


def _report_path(provider: str) -> Path:
    configured = os.getenv("FUNCTION_BENCH_REPORT")
    if configured:
        return Path(configured)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        Path(__file__).resolve().parents[6]
        / ".benchmark-results"
        / "function-execution"
        / provider
        / f"{timestamp}.json"
    )


@pytest.mark.asyncio
async def test_function_execution_quality_benchmark(
    authenticated_client,
    fixed_test_user,
    test_pod,
    function_benchmark_runtime,
) -> None:
    provider = function_benchmark_runtime.provider
    pod_id = UUID(test_pod["id"])
    function_benchmark_runtime.tracked_sandboxes.extend(
        (
            (WorkloadKind.WORKSPACE, UUID(fixed_test_user["id"])),
            (WorkloadKind.FUNCTION, pod_id),
        )
    )
    config = BenchmarkConfig(
        provider=provider,
        concurrency=_positive_int("FUNCTION_BENCH_CONCURRENCY", 5),
        invocations=_positive_int("FUNCTION_BENCH_INVOCATIONS", 5),
        source_rows_per_table=_positive_int("FUNCTION_BENCH_SOURCE_ROWS", 1_000),
        rows_per_write=_positive_int("FUNCTION_BENCH_WRITE_ROWS", 1_000),
        terminal_timeout_seconds=float(
            os.getenv("FUNCTION_BENCH_TERMINAL_TIMEOUT_SECONDS", "240")
        ),
        latency_budgets=_latency_budgets(provider),
    )
    report = await FunctionExecutionBenchmark(
        authenticated_client,
        pod_id=str(pod_id),
        config=config,
    ).run()
    destination = _report_path(provider)
    write_report(report, destination)
    print(
        json.dumps(
            {
                "provider": provider,
                "successful": report.successful,
                "report": str(destination),
                "cases": [
                    {
                        "case": case.case,
                        "success_rate": case.success_rate,
                        "terminal_p95_seconds": case.terminal.p95_seconds,
                        "submit_p95_seconds": case.submit.p95_seconds,
                        "wall_seconds": case.wall_seconds,
                    }
                    for case in report.cases
                ],
                "errors": report.errors,
            },
            indent=2,
        )
    )

    assert report.successful, report.errors
