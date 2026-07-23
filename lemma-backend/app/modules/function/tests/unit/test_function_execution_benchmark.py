from __future__ import annotations

from load_tests.function_execution import (
    BenchmarkPhase,
    FunctionExecutionBenchmark,
    FunctionKind,
    InvocationSample,
    LatencyBudget,
    OperationKind,
    evaluate_latency_budgets,
    summarize_case,
)


def _sample(index: int, seconds: float, *, status: str = "COMPLETED"):
    return InvocationSample(
        case="api_read",
        phase=BenchmarkPhase.STEADY.value,
        index=index,
        run_id=str(index),
        status=status,
        submit_seconds=seconds / 2,
        terminal_seconds=seconds,
        queue_seconds=seconds / 4,
        execution_seconds=seconds / 2,
        function_call_seconds=seconds / 4,
        platform_overhead_seconds=seconds * 3 / 4,
        output_data={},
        error=None,
    )


def test_case_summary_uses_interpolated_p95_and_counts_failures() -> None:
    summary = summarize_case(
        "api_read",
        FunctionKind.API,
        OperationKind.READ,
        [
            _sample(1, 1),
            _sample(2, 2),
            _sample(3, 3),
            _sample(4, 4),
            _sample(5, 5, status="FAILED"),
        ],
        concurrency=5,
        wall_seconds=10,
    )

    assert summary.completed == 4
    assert summary.failed == 1
    assert summary.success_rate == 0.8
    assert summary.invocations_per_second == 0.4
    assert summary.terminal.p50_seconds == 3
    assert summary.terminal.p95_seconds == 4.8
    assert summary.submit.p95_seconds == 2.4


def test_latency_budget_reports_terminal_and_job_submission_regressions() -> None:
    summary = summarize_case(
        "job_write",
        FunctionKind.JOB,
        OperationKind.WRITE,
        [_sample(index, seconds) for index, seconds in enumerate(range(1, 6), 1)],
        concurrency=5,
        wall_seconds=5,
    )

    failures = evaluate_latency_budgets(
        [summary],
        (
            LatencyBudget(
                case="job_write",
                terminal_p95_seconds=4.0,
                submit_p95_seconds=2.0,
                platform_overhead_p95_seconds=3.0,
            ),
        ),
    )

    assert failures == (
        "job_write terminal p95 4.800s exceeds 4.000s",
        "job_write submit p95 2.400s exceeds 2.000s",
        "job_write platform overhead p95 3.600s exceeds 3.000s",
    )


def test_latency_budget_accepts_report_within_limits() -> None:
    summary = summarize_case(
        "api_read",
        FunctionKind.API,
        OperationKind.READ,
        [_sample(1, 1.0)],
        concurrency=1,
        wall_seconds=1,
    )

    assert not evaluate_latency_budgets(
        [summary],
        (LatencyBudget(case="api_read", terminal_p95_seconds=1.0),),
    )


def test_api_platform_overhead_uses_caller_observed_latency() -> None:
    assert (
        FunctionExecutionBenchmark._platform_overhead_seconds(
            case="api_write",
            terminal_seconds=1.8,
            queue_seconds=0.1,
            execution_seconds=1.2,
            function_call_seconds=0.7,
        )
        == 1.1
    )


def test_job_platform_overhead_excludes_poll_observation_delay() -> None:
    assert (
        FunctionExecutionBenchmark._platform_overhead_seconds(
            case="job_write",
            terminal_seconds=4.5,
            queue_seconds=0.4,
            execution_seconds=1.6,
            function_call_seconds=1.2,
        )
        == 0.8
    )
