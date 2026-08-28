from __future__ import annotations

import pytest

from load_tests.function_execution import (
    BenchmarkCase,
    BenchmarkConfig,
    BenchmarkPhase,
    FunctionExecutionBenchmark,
    FunctionKind,
    InvocationSample,
    LatencyBudget,
    OperationKind,
    evaluate_latency_budgets,
    summarize_case,
)


_API_READ_SINGLE = BenchmarkCase(
    name="api_read_single",
    function_kind=FunctionKind.API,
    operation=OperationKind.READ,
    rows_per_invocation=1,
)


def _sample(index: int, seconds: float, *, status: str = "COMPLETED"):
    return InvocationSample(
        case=_API_READ_SINGLE.name,
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
        _API_READ_SINGLE,
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
    assert summary.rows_per_invocation == 1
    assert summary.sdk_calls_per_invocation == 1
    assert summary.terminal.p50_seconds == 2.5
    assert summary.terminal.p95_seconds == pytest.approx(3.85)
    assert summary.submit.p95_seconds == pytest.approx(1.925)


def test_one_slow_sample_does_not_decide_a_p95_budget() -> None:
    """A percentile budget needs enough samples to be a percentile.

    `_percentile` interpolates at `(n - 1) * 0.95`. At the old default of five
    invocations that is position 3.8 -- four fifths of the way to the maximum --
    so the p95 gates were the slowest of five wearing a percentile's name, and
    one scheduling hiccup on a shared runner failed the protected suite and
    with it every Desktop release:

        job_write_batch platform overhead p95 2.742s exceeds 2.000s

    from a single 3.37s sample among siblings near 0.2s. Driven off
    `BenchmarkConfig`'s own default rather than a hard-coded 20, so lowering it
    back to a number that cannot express a p95 fails here first.
    """
    default = BenchmarkConfig(provider="docker").invocations
    steady = [_sample(index, 0.2) for index in range(default - 1)]
    hiccup = [_sample(default, 8.0)]
    budget = (
        LatencyBudget(case=_API_READ_SINGLE.name, platform_overhead_p95_seconds=2.0),
    )

    summary = summarize_case(
        _API_READ_SINGLE, steady + hiccup, concurrency=5, wall_seconds=5
    )
    assert evaluate_latency_budgets([summary], budget) == ()

    # ...and it still fails for a tail that is actually slow, which is the only
    # reason to gate on p95 rather than the median.
    regressed = [_sample(index, 0.2) for index in range(default - 4)] + [
        _sample(index, 8.0) for index in range(default - 4, default)
    ]
    regressed_summary = summarize_case(
        _API_READ_SINGLE, regressed, concurrency=5, wall_seconds=5
    )
    assert evaluate_latency_budgets([regressed_summary], budget) != ()


def test_latency_budget_reports_terminal_and_job_submission_regressions() -> None:
    summary = summarize_case(
        BenchmarkCase(
            name="job_write_batch",
            function_kind=FunctionKind.JOB,
            operation=OperationKind.WRITE,
            rows_per_invocation=1_000,
        ),
        [_sample(index, seconds) for index, seconds in enumerate(range(1, 6), 1)],
        concurrency=5,
        wall_seconds=5,
    )

    failures = evaluate_latency_budgets(
        [summary],
        (
            LatencyBudget(
                case="job_write_batch",
                terminal_p95_seconds=4.0,
                submit_p95_seconds=2.0,
                platform_overhead_p95_seconds=3.0,
            ),
        ),
    )

    assert failures == (
        "job_write_batch terminal p95 4.800s exceeds 4.000s",
        "job_write_batch submit p95 2.400s exceeds 2.000s",
        "job_write_batch platform overhead p95 3.600s exceeds 3.000s",
    )


def test_latency_budget_accepts_report_within_limits() -> None:
    summary = summarize_case(
        _API_READ_SINGLE,
        [_sample(1, 1.0)],
        concurrency=1,
        wall_seconds=1,
    )

    assert not evaluate_latency_budgets(
        [summary],
        (LatencyBudget(case="api_read_single", terminal_p95_seconds=1.0),),
    )


def test_api_platform_overhead_uses_caller_observed_latency() -> None:
    assert (
        FunctionExecutionBenchmark._platform_overhead_seconds(
            case="api_write_single",
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
            case="job_write_batch",
            terminal_seconds=4.5,
            queue_seconds=0.4,
            execution_seconds=1.6,
            function_call_seconds=1.2,
        )
        == 0.8
    )
