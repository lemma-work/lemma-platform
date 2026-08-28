from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import UUID

from sandbox_runtime.protocol import WorkloadKind
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


_DEFAULT_PLATFORM_OVERHEAD_P95_SECONDS = 2.0
_DEFAULT_WARM_NOOP_P95_SECONDS = 2.0
_DEFAULT_COLD_NOOP_SECONDS = 8.0
# Resuming a paused function sandbox restores a memory snapshot, so it should
# land far closer to warm than to a fresh build. Budgeted separately from
# `_DEFAULT_REBUILT_NOOP_SECONDS` precisely so the two can be compared: if
# resume is not meaningfully cheaper than rebuild, pausing is buying nothing and
# the provider should kill instead (`E2BSandboxProvider._lifecycle`).
_DEFAULT_RESUMED_NOOP_SECONDS = 8.0
_DEFAULT_REBUILT_NOOP_SECONDS = 20.0


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


def _latency_budgets(
    provider: str, *, lifecycle_measured: bool
) -> tuple[LatencyBudget, ...]:
    del provider
    budgets: list[LatencyBudget] = []
    for case in (
        "api_noop",
        "api_read_single",
        "api_write_single",
        "api_read_batch",
        "api_write_batch",
        "job_read_batch",
        "job_write_batch",
    ):
        prefix = f"FUNCTION_BENCH_{case.upper()}"
        budgets.append(
            LatencyBudget(
                case=case,
                terminal_p95_seconds=(
                    _positive_float(
                        f"{prefix}_TERMINAL_P95_SECONDS",
                        _DEFAULT_WARM_NOOP_P95_SECONDS,
                    )
                    if case == "api_noop"
                    else None
                ),
                submit_p95_seconds=(
                    _positive_float(f"{prefix}_SUBMIT_P95_SECONDS", 2.0)
                    if case.startswith("job_")
                    else None
                ),
                platform_overhead_p95_seconds=_positive_float(
                    f"{prefix}_PLATFORM_OVERHEAD_P95_SECONDS",
                    _DEFAULT_PLATFORM_OVERHEAD_P95_SECONDS,
                ),
                cold_terminal_seconds=(
                    _positive_float(
                        f"{prefix}_COLD_TERMINAL_SECONDS",
                        _DEFAULT_COLD_NOOP_SECONDS,
                    )
                    if case == "api_noop"
                    else None
                ),
                resumed_terminal_seconds=(
                    _positive_float(
                        f"{prefix}_RESUMED_TERMINAL_SECONDS",
                        _DEFAULT_RESUMED_NOOP_SECONDS,
                    )
                    if case == "api_noop" and lifecycle_measured
                    else None
                ),
                rebuilt_terminal_seconds=(
                    _positive_float(
                        f"{prefix}_REBUILT_TERMINAL_SECONDS",
                        _DEFAULT_REBUILT_NOOP_SECONDS,
                    )
                    if case == "api_noop" and lifecycle_measured
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


def _lifecycle_comparison(report) -> dict[str, object]:
    """The four numbers that decide the function sandbox's lifecycle policy.

    A function invocation is latency-sensitive on every path, not just the warm
    one, so the interesting comparison is not any single figure but the spread:

    * `warm` -- the sandbox was already running.
    * `cold` -- its very first invocation.
    * `resumed` -- it had been paused, and this call woke it. On the function
      profile that pause keeps memory, so this restores a snapshot rather than
      re-running the image command.
    * `rebuilt` -- it had been destroyed, and this call had to build a new one.

    `resumed_vs_rebuilt_seconds` is the one to read. Pausing only earns its
    place if resume is faster than a fresh build by more than the pause itself
    costs -- E2B prices a memory snapshot at roughly four seconds per GiB, and
    the function profile has two. If that number goes to zero or negative,
    keeping memory is buying nothing and the provider should kill on timeout
    instead of pausing.
    """
    by_phase = {sample.phase: sample for sample in report.lifecycle}
    cold = next((sample for sample in report.cold if sample.case == "api_noop"), None)
    warm = next((case for case in report.cases if case.case == "api_noop"), None)
    resumed = by_phase.get("resumed")
    rebuilt = by_phase.get("rebuilt")
    comparison: dict[str, object] = {
        "warm_p95_seconds": warm.terminal.p95_seconds if warm else None,
        "cold_seconds": cold.terminal_seconds if cold else None,
        "resumed_seconds": resumed.terminal_seconds if resumed else None,
        "rebuilt_seconds": rebuilt.terminal_seconds if rebuilt else None,
        "pause_seconds": resumed.disturb_seconds if resumed else None,
        "kill_seconds": rebuilt.disturb_seconds if rebuilt else None,
    }
    if resumed is not None and rebuilt is not None:
        comparison["resumed_vs_rebuilt_seconds"] = (
            rebuilt.terminal_seconds - resumed.terminal_seconds
        )
        comparison["pause_is_worth_taking"] = (
            rebuilt.terminal_seconds - resumed.terminal_seconds
        ) > (resumed.disturb_seconds or 0.0)
    return comparison


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

    # The two lifecycle hooks. They exist here rather than in the harness
    # because only the test process can reach the sandbox control plane -- the
    # harness speaks public HTTP APIs, and no public route releases or destroys
    # a sandbox. Each opens its own client: the benchmark runs for minutes
    # between calls, and holding one open across that adds a connection whose
    # only purpose is to be idle.
    async def _drop_cached_endpoint() -> None:
        """Forget the direct runtime lease, the way `quarantine` does.

        Not optional, and not bookkeeping. `RouteResolver.quarantine` invalidates
        the cached endpoint *before* destroying the sandbox, and skipping that
        half does not measure a rebuild -- it measures dispatch to an endpoint
        that no longer exists. Left out, the rebuilt sample came back
        `InvocationOutcomeUnconfirmed` rather than a latency, because the cache
        happily served the dead sandbox's URL for the rest of its TTL.

        The cache is a process-local singleton and the benchmark's API server
        runs in this process, so this reaches the same instance the dispatch
        path reads.
        """
        from app.modules.function.api.dependencies import (
            _function_runtime_endpoint_cache,
        )
        from app.modules.function.application.function_runtime_endpoint_cache import (
            FunctionRuntimeEndpointKey,
        )
        from app.modules.workspace.providers.profiles import profile_for
        from app.modules.workspace.domain.sandbox import SandboxKind

        await _function_runtime_endpoint_cache.invalidate(
            FunctionRuntimeEndpointKey(
                pod_id=pod_id,
                profile_digest=profile_for(SandboxKind.FUNCTION).digest,
            )
        )

    async def _release_function_sandbox() -> None:
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        await _drop_cached_endpoint()
        async with build_local_client() as client:
            await client.release_sandbox(WorkloadKind.FUNCTION, pod_id)

    async def _destroy_function_sandbox() -> None:
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        await _drop_cached_endpoint()
        async with build_local_client() as client:
            await client.destroy_sandbox(WorkloadKind.FUNCTION, pod_id)

    lifecycle_measured = os.getenv(
        "FUNCTION_BENCH_LIFECYCLE", "1"
    ).strip().lower() not in {"0", "false", "no"}

    config = BenchmarkConfig(
        provider=provider,
        release_sandbox=_release_function_sandbox if lifecycle_measured else None,
        destroy_sandbox=_destroy_function_sandbox if lifecycle_measured else None,
        concurrency=_positive_int("FUNCTION_BENCH_CONCURRENCY", 5),
        invocations=_positive_int("FUNCTION_BENCH_INVOCATIONS", 20),
        batch_rows=_positive_int("FUNCTION_BENCH_BATCH_ROWS", 1_000),
        poll_interval_seconds=_positive_float(
            "FUNCTION_BENCH_POLL_INTERVAL_SECONDS",
            0.5,
        ),
        terminal_timeout_seconds=float(
            os.getenv("FUNCTION_BENCH_TERMINAL_TIMEOUT_SECONDS", "240")
        ),
        pool_fill_hold_ms=_positive_int("FUNCTION_BENCH_POOL_FILL_HOLD_MS", 750),
        latency_budgets=_latency_budgets(
            provider, lifecycle_measured=lifecycle_measured
        ),
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
                        "rows_per_invocation": case.rows_per_invocation,
                        "sdk_calls_per_invocation": case.sdk_calls_per_invocation,
                        "success_rate": case.success_rate,
                        "terminal_mean_seconds": case.terminal.mean_seconds,
                        "terminal_p95_seconds": case.terminal.p95_seconds,
                        "submit_p95_seconds": case.submit.p95_seconds,
                        "function_call_mean_seconds": (case.function_call.mean_seconds),
                        "platform_overhead_p95_seconds": (
                            case.platform_overhead.p95_seconds
                        ),
                        "wall_seconds": case.wall_seconds,
                    }
                    for case in report.cases
                ],
                "lifecycle": _lifecycle_comparison(report),
                "errors": report.errors,
            },
            indent=2,
        )
    )

    assert report.successful, report.errors
