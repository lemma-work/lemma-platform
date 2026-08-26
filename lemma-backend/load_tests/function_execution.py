"""Reusable full-path API/JOB function execution benchmark.

The benchmark provisions four isolated pod tables and seven immutable functions,
warms the per-pod sandbox once, then drives each function with bounded client
concurrency. It intentionally uses only public Lemma HTTP APIs so the measured
path includes authorization, durable runs, the sandbox runtime allocation, resident runtime
dispatch, runtime callbacks, delegated SDK table access, and terminal result
persistence.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
import json
from collections.abc import Awaitable, Callable
import math
from pathlib import Path
import statistics
import time
from typing import Any
from uuid import uuid4

import httpx


TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class FunctionKind(StrEnum):
    API = "API"
    JOB = "JOB"


class OperationKind(StrEnum):
    NOOP = "noop"
    READ = "read"
    WRITE = "write"


class BenchmarkPhase(StrEnum):
    COLD = "cold"
    POOL_FILL = "pool_fill"
    STEADY = "steady"
    # The two states a real pod spends most of its life entering, and the two
    # the benchmark could not previously price. STEADY measures a sandbox that
    # is already running; nobody's first call of the morning is that call.
    #
    # RESUMED: the sandbox was released and its next invocation woke it. This is
    # the routine one -- the idle sweep releases after
    # `WORKSPACE_IDLE_RELEASE_SECONDS`, so this is what a user pays after any
    # gap longer than that.
    #
    # REBUILT: the sandbox was destroyed and its next invocation had to make a
    # new one. This should be rare, and the gap between it and RESUMED is what
    # a lifecycle bug costs. It was not rare: two deployments sharing an E2B
    # team destroyed each other's sandboxes every five minutes, so every
    # invocation was paying REBUILT while the dashboards showed STEADY.
    RESUMED = "resumed"
    REBUILT = "rebuilt"


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    name: str
    function_kind: FunctionKind
    operation: OperationKind
    rows_per_invocation: int

    @property
    def sdk_calls_per_invocation(self) -> int:
        return 0 if self.operation == OperationKind.NOOP else 1


def benchmark_cases(batch_rows: int) -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase("api_noop", FunctionKind.API, OperationKind.NOOP, 0),
        BenchmarkCase("api_read_single", FunctionKind.API, OperationKind.READ, 1),
        BenchmarkCase("api_write_single", FunctionKind.API, OperationKind.WRITE, 1),
        BenchmarkCase(
            "api_read_batch",
            FunctionKind.API,
            OperationKind.READ,
            batch_rows,
        ),
        BenchmarkCase(
            "api_write_batch",
            FunctionKind.API,
            OperationKind.WRITE,
            batch_rows,
        ),
        BenchmarkCase(
            "job_read_batch",
            FunctionKind.JOB,
            OperationKind.READ,
            batch_rows,
        ),
        BenchmarkCase(
            "job_write_batch",
            FunctionKind.JOB,
            OperationKind.WRITE,
            batch_rows,
        ),
    )


@dataclass(frozen=True, slots=True)
class LatencyBudget:
    case: str
    terminal_p95_seconds: float | None = None
    submit_p95_seconds: float | None = None
    platform_overhead_p95_seconds: float | None = None
    cold_terminal_seconds: float | None = None
    resumed_terminal_seconds: float | None = None
    rebuilt_terminal_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.case:
            raise ValueError("latency budget case is required")
        configured = (
            self.terminal_p95_seconds,
            self.submit_p95_seconds,
            self.platform_overhead_p95_seconds,
            self.cold_terminal_seconds,
            self.resumed_terminal_seconds,
            self.rebuilt_terminal_seconds,
        )
        if not any(value is not None for value in configured):
            raise ValueError("latency budget must configure at least one limit")
        if any(value is not None and value <= 0 for value in configured):
            raise ValueError("latency budget limits must be positive")


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    provider: str
    concurrency: int = 5
    # Enough samples for the p95 budgets to be a p95.
    #
    # This was 5, and `_percentile` interpolates at `(n - 1) * 0.95`, which for
    # n=5 is position 3.8 -- four fifths of the way to the maximum. So the
    # "p95 platform overhead" gate was the slowest of five invocations wearing
    # a percentile's name, and one scheduling hiccup on a shared runner failed
    # it: `job_write_batch platform overhead p95 2.742s exceeds 2.000s`, from a
    # single 3.37s sample whose siblings were all near 0.2s.
    #
    # At n=20 the same outlier lands at position 18.05, between the two largest
    # samples, and one hiccup no longer decides the number -- which is the whole
    # point of gating on a percentile instead of a maximum. Tail regressions
    # still fail it, because several slow samples still move it.
    #
    # Costs four waves of the steady phase per case instead of one. The gate it
    # protects blocks every Desktop release, so a benchmark that takes two
    # minutes longer and means something beats one that is quick and random.
    invocations: int = 20
    batch_rows: int = 1_000
    # JOB completion is observed out of band. Poll slowly enough that the
    # observer does not become the workload under test, especially at higher
    # invocation concurrency.
    poll_interval_seconds: float = 0.5
    terminal_timeout_seconds: float = 180.0
    pool_fill_hold_ms: int = 750
    cleanup: bool = True
    latency_budgets: tuple[LatencyBudget, ...] = ()
    # Supplied by the caller because only it can reach the sandbox control
    # plane: the harness speaks public HTTP APIs, and there is no public route
    # that releases or destroys a sandbox. Left unset, the lifecycle phases are
    # skipped and the report simply carries no RESUMED/REBUILT samples.
    release_sandbox: Callable[[], Awaitable[None]] | None = None
    destroy_sandbox: Callable[[], Awaitable[None]] | None = None
    # Which case pays for the lifecycle measurement. `api_noop` by default: it
    # issues no SDK call, so its terminal time is the platform path and nothing
    # else, and it writes no rows -- which keeps sink-row verification exact
    # without teaching it about these extra invocations.
    lifecycle_case: str = "api_noop"

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("provider is required")
        if self.concurrency < 1 or self.invocations < 1:
            raise ValueError("concurrency and invocations must be positive")
        if self.batch_rows < 2 or self.batch_rows > 1_000:
            raise ValueError("batch rows must be in 2..1000")
        if self.poll_interval_seconds <= 0 or self.terminal_timeout_seconds <= 0:
            raise ValueError("poll interval and terminal timeout must be positive")
        if self.pool_fill_hold_ms < 0 or self.pool_fill_hold_ms > 10_000:
            raise ValueError("pool fill hold must be in 0..10000 milliseconds")
        budget_cases = [budget.case for budget in self.latency_budgets]
        if len(budget_cases) != len(set(budget_cases)):
            raise ValueError("latency budget cases must be unique")


@dataclass(frozen=True, slots=True)
class BenchmarkResources:
    suffix: str
    source_tables: tuple[str, str]
    sink_tables: tuple[str, str]
    functions: dict[str, str]


@dataclass(frozen=True, slots=True)
class InvocationSample:
    case: str
    phase: str
    index: int
    run_id: str | None
    status: str
    submit_seconds: float
    terminal_seconds: float
    queue_seconds: float | None
    execution_seconds: float | None
    function_call_seconds: float | None
    platform_overhead_seconds: float | None
    output_data: dict[str, Any] | None
    error: str | None
    # Only set on lifecycle samples: how long the release or destroy that
    # preceded this invocation took. It is not part of anyone's latency, but it
    # is what decides the policy -- E2B documents a pause at roughly four
    # seconds per GiB of RAM, so a memory-preserving pause of a 2 GiB function
    # sandbox spends real time that a kill does not. Pausing is only worth it
    # if `resumed` beats `rebuilt` by more than the pause costs to take.
    disturb_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class TimingSummary:
    mean_seconds: float | None
    p50_seconds: float | None
    p95_seconds: float | None
    max_seconds: float | None


@dataclass(frozen=True, slots=True)
class CaseSummary:
    case: str
    function_kind: str
    operation: str
    rows_per_invocation: int
    sdk_calls_per_invocation: int
    concurrency: int
    completed: int
    failed: int
    success_rate: float
    wall_seconds: float
    invocations_per_second: float
    submit: TimingSummary
    terminal: TimingSummary
    queue: TimingSummary
    execution: TimingSummary
    function_call: TimingSummary
    platform_overhead: TimingSummary


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    schema_version: int
    provider: str
    started_at: str
    finished_at: str
    config: dict[str, Any]
    resources: dict[str, Any]
    cold: tuple[InvocationSample, ...]
    pool_fill: tuple[InvocationSample, ...]
    lifecycle: tuple[InvocationSample, ...]
    cases: tuple[CaseSummary, ...]
    samples: tuple[InvocationSample, ...]
    verified_sink_rows: dict[str, int]
    errors: tuple[str, ...] = field(default=())

    @property
    def successful(self) -> bool:
        return not self.errors and all(case.failed == 0 for case in self.cases)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["successful"] = self.successful
        return payload


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _timings(values: list[float | None]) -> TimingSummary:
    present = [value for value in values if value is not None]
    return TimingSummary(
        mean_seconds=statistics.fmean(present) if present else None,
        p50_seconds=_percentile(present, 0.50),
        p95_seconds=_percentile(present, 0.95),
        max_seconds=max(present) if present else None,
    )


def summarize_case(
    case: BenchmarkCase,
    samples: list[InvocationSample],
    *,
    concurrency: int,
    wall_seconds: float,
) -> CaseSummary:
    successful_samples = [sample for sample in samples if sample.status == "COMPLETED"]
    completed = len(successful_samples)
    return CaseSummary(
        case=case.name,
        function_kind=case.function_kind.value,
        operation=case.operation.value,
        rows_per_invocation=case.rows_per_invocation,
        sdk_calls_per_invocation=case.sdk_calls_per_invocation,
        concurrency=concurrency,
        completed=completed,
        failed=len(samples) - completed,
        success_rate=completed / len(samples) if samples else 0.0,
        wall_seconds=wall_seconds,
        invocations_per_second=(completed / wall_seconds if wall_seconds else 0.0),
        submit=_timings([sample.submit_seconds for sample in successful_samples]),
        terminal=_timings([sample.terminal_seconds for sample in successful_samples]),
        queue=_timings([sample.queue_seconds for sample in successful_samples]),
        execution=_timings([sample.execution_seconds for sample in successful_samples]),
        function_call=_timings(
            [sample.function_call_seconds for sample in successful_samples]
        ),
        platform_overhead=_timings(
            [sample.platform_overhead_seconds for sample in successful_samples]
        ),
    )


def evaluate_latency_budgets(
    cases: tuple[CaseSummary, ...] | list[CaseSummary],
    budgets: tuple[LatencyBudget, ...],
    *,
    cold: tuple[InvocationSample, ...] | list[InvocationSample] = (),
    lifecycle: tuple[InvocationSample, ...] | list[InvocationSample] = (),
) -> tuple[str, ...]:
    """Return stable quality-gate failures for a benchmark report."""

    summaries = {summary.case: summary for summary in cases}
    cold_by_case = {sample.case: sample for sample in cold}
    # Keyed by phase as well as case: one case contributes both a RESUMED and a
    # REBUILT sample, and collapsing them onto the case would let whichever
    # arrived last answer for both.
    lifecycle_by_phase = {(sample.phase, sample.case): sample for sample in lifecycle}
    failures: list[str] = []
    for budget in budgets:
        summary = summaries.get(budget.case)
        if summary is None:
            failures.append(f"latency budget references missing case {budget.case}")
            continue
        if budget.terminal_p95_seconds is not None:
            terminal_p95 = summary.terminal.p95_seconds
            if terminal_p95 is None:
                failures.append(f"{budget.case} terminal p95 has no samples")
            elif terminal_p95 > budget.terminal_p95_seconds:
                failures.append(
                    f"{budget.case} terminal p95 {terminal_p95:.3f}s exceeds "
                    f"{budget.terminal_p95_seconds:.3f}s"
                )
        if budget.submit_p95_seconds is not None:
            submit_p95 = summary.submit.p95_seconds
            if submit_p95 is None:
                failures.append(f"{budget.case} submit p95 has no samples")
            elif submit_p95 > budget.submit_p95_seconds:
                failures.append(
                    f"{budget.case} submit p95 {submit_p95:.3f}s exceeds "
                    f"{budget.submit_p95_seconds:.3f}s"
                )
        if budget.platform_overhead_p95_seconds is not None:
            overhead_p95 = summary.platform_overhead.p95_seconds
            if overhead_p95 is None:
                failures.append(f"{budget.case} platform overhead p95 has no samples")
            elif overhead_p95 > budget.platform_overhead_p95_seconds:
                failures.append(
                    f"{budget.case} platform overhead p95 "
                    f"{overhead_p95:.3f}s exceeds "
                    f"{budget.platform_overhead_p95_seconds:.3f}s"
                )
        if budget.cold_terminal_seconds is not None:
            cold_sample = cold_by_case.get(budget.case)
            if cold_sample is None:
                failures.append(f"{budget.case} cold execution has no sample")
            elif cold_sample.terminal_seconds > budget.cold_terminal_seconds:
                failures.append(
                    f"{budget.case} cold terminal "
                    f"{cold_sample.terminal_seconds:.3f}s exceeds "
                    f"{budget.cold_terminal_seconds:.3f}s"
                )
        for phase, limit in (
            (BenchmarkPhase.RESUMED, budget.resumed_terminal_seconds),
            (BenchmarkPhase.REBUILT, budget.rebuilt_terminal_seconds),
        ):
            if limit is None:
                continue
            sample = lifecycle_by_phase.get((phase, budget.case))
            if sample is None:
                # Absent rather than slow: the hooks were not supplied, so the
                # phase never ran. Saying so beats a budget that silently
                # passes because nothing measured it.
                failures.append(f"{budget.case} {phase.value} has no sample")
            elif sample.terminal_seconds > limit:
                failures.append(
                    f"{budget.case} {phase.value} terminal "
                    f"{sample.terminal_seconds:.3f}s exceeds {limit:.3f}s"
                )
    return tuple(failures)


def _serializable_config(config: BenchmarkConfig) -> dict[str, Any]:
    """The config as recorded in the report, minus the parts that are code.

    `release_sandbox` and `destroy_sandbox` are callables the caller injects,
    and `asdict` carries them straight into a structure the report then tries
    to serialise as JSON. What a reader needs is whether the lifecycle phases
    ran at all, which is a boolean.
    """
    payload = {
        key: value
        for key, value in asdict(config).items()
        if key not in {"release_sandbox", "destroy_sandbox"}
    }
    payload["lifecycle_phases_enabled"] = (
        config.release_sandbox is not None or config.destroy_sandbox is not None
    )
    return payload


def write_report(report: BenchmarkReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


def _read_function_source(name: str, table: str) -> str:
    return f'''#input_type_name: ReadInput
#output_type_name: ReadResult
#function_name: {name}

import asyncio
import time

from pydantic import BaseModel, Field
from lemma_sdk import FunctionContext, Pod

class ReadInput(BaseModel):
    limit: int = Field(ge=1, le=1000)
    hold_ms: int = Field(default=0, ge=0, le=10000)

class ReadResult(BaseModel):
    rows_read: int
    checksum: int
    table_total: int
    call_ms: float

async def {name}(ctx: FunctionContext, data: ReadInput) -> ReadResult:
    if data.hold_ms:
        await asyncio.sleep(data.hold_ms / 1000)
    pod = Pod.from_env()
    started = time.perf_counter()
    result = pod.table("{table}").list(limit=data.limit)
    call_ms = (time.perf_counter() - started) * 1000
    rows = list(result.items)
    return ReadResult(
        rows_read=len(rows),
        checksum=sum(int(row["ordinal"]) for row in rows),
        table_total=int(result.total),
        call_ms=call_ms,
    )
'''


def _write_function_source(name: str, table: str) -> str:
    return f'''#input_type_name: WriteInput
#output_type_name: WriteResult
#function_name: {name}

import asyncio
import time

from pydantic import BaseModel, Field
from lemma_sdk import FunctionContext, Pod

class WriteInput(BaseModel):
    run_key: str
    rows: int = Field(ge=1, le=1000)
    hold_ms: int = Field(default=0, ge=0, le=10000)

class WriteResult(BaseModel):
    rows_written: int
    call_ms: float

async def {name}(ctx: FunctionContext, data: WriteInput) -> WriteResult:
    if data.hold_ms:
        await asyncio.sleep(data.hold_ms / 1000)
    pod = Pod.from_env()
    records = [
        {{"run_key": data.run_key, "ordinal": index, "payload": "a" * 64}}
        for index in range(data.rows)
    ]
    started = time.perf_counter()
    if data.rows == 1:
        pod.records.create("{table}", records[0])
        count = 1
    else:
        count = pod.records.bulk_create("{table}", records)
    call_ms = (time.perf_counter() - started) * 1000
    return WriteResult(
        rows_written=count,
        call_ms=call_ms,
    )
'''


def _noop_function_source(name: str) -> str:
    return f"""#input_type_name: NoopInput
#output_type_name: NoopResult
#function_name: {name}

import asyncio

from pydantic import BaseModel, Field
from lemma_sdk import FunctionContext

class NoopInput(BaseModel):
    value: int = 1
    hold_ms: int = Field(default=0, ge=0, le=10000)

class NoopResult(BaseModel):
    value: int

async def {name}(ctx: FunctionContext, data: NoopInput) -> NoopResult:
    del ctx
    if data.hold_ms:
        await asyncio.sleep(data.hold_ms / 1000)
    return NoopResult(value=data.value)
"""


class FunctionExecutionBenchmark:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        pod_id: str,
        config: BenchmarkConfig,
    ) -> None:
        self._client = client
        self._pod_id = pod_id
        self._config = config
        self._resources: BenchmarkResources | None = None

    async def run(self) -> BenchmarkReport:
        started = datetime.now(UTC)
        errors: list[str] = []
        cold: list[InvocationSample] = []
        pool_fill: list[InvocationSample] = []
        lifecycle: list[InvocationSample] = []
        samples: list[InvocationSample] = []
        summaries: list[CaseSummary] = []
        verified_sink_rows: dict[str, int] = {}
        resources: BenchmarkResources | None = None
        try:
            resources = await self.provision()
            cases = benchmark_cases(self._config.batch_rows)
            for case in cases:
                cold.append(
                    await self._invoke(
                        case,
                        resources.functions[case.name],
                        0,
                        phase=BenchmarkPhase.COLD,
                    )
                )
                fill_samples, _fill_wall = await self._run_case(
                    case,
                    resources.functions[case.name],
                    phase=BenchmarkPhase.POOL_FILL,
                    invocations=self._config.concurrency,
                )
                pool_fill.extend(fill_samples)
                case_samples, wall_seconds = await self._run_case(
                    case,
                    resources.functions[case.name],
                    phase=BenchmarkPhase.STEADY,
                    invocations=self._config.invocations,
                )
                samples.extend(case_samples)
                summaries.append(
                    summarize_case(
                        case,
                        case_samples,
                        concurrency=self._config.concurrency,
                        wall_seconds=wall_seconds,
                    )
                )

            lifecycle.extend(await self._run_lifecycle(cases, resources))

            verified_sink_rows = await self._verify_sink_rows(resources)
            executions_per_case = (
                1 + self._config.concurrency + self._config.invocations
            )
            expected_sink_rows = {
                resources.sink_tables[0]: executions_per_case
                * (1 + self._config.batch_rows),
                resources.sink_tables[1]: executions_per_case * self._config.batch_rows,
            }
            if verified_sink_rows != expected_sink_rows:
                errors.append(
                    "sink row verification failed: "
                    f"expected {expected_sink_rows}, found {verified_sink_rows}"
                )
            for sample in (*cold, *pool_fill, *lifecycle, *samples):
                if sample.status != "COMPLETED":
                    errors.append(
                        f"{sample.case}[{sample.index}] {sample.status}: "
                        f"{sample.error or 'unknown failure'}"
                    )
            errors.extend(
                evaluate_latency_budgets(
                    summaries,
                    self._config.latency_budgets,
                    cold=cold,
                    lifecycle=lifecycle,
                )
            )
        finally:
            cleanup_resources = resources or self._resources
            if self._config.cleanup and cleanup_resources is not None:
                await self.cleanup(cleanup_resources)

        if resources is None:
            raise RuntimeError("benchmark provisioning completed without resources")

        return BenchmarkReport(
            schema_version=3,
            provider=self._config.provider,
            started_at=started.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            config=_serializable_config(self._config),
            resources=asdict(resources),
            cold=tuple(cold),
            pool_fill=tuple(pool_fill),
            lifecycle=tuple(lifecycle),
            cases=tuple(summaries),
            samples=tuple(samples),
            verified_sink_rows=verified_sink_rows,
            errors=tuple(errors),
        )

    async def _run_lifecycle(
        self,
        cases: tuple[BenchmarkCase, ...],
        resources: BenchmarkResources,
    ) -> list[InvocationSample]:
        """Price the two states STEADY cannot see: resumed, and rebuilt.

        Both are measured on one case and one invocation each, because what is
        being measured is a step change, not a distribution: resuming a paused
        sandbox and building a new one differ by seconds, and a single sample
        separates them unambiguously. Spending the benchmark's time on repeats
        here would buy precision nobody needs and slow every run.

        Order matters. Release first, because releasing a sandbox that has just
        been destroyed measures nothing; and the resumed invocation leaves a
        running sandbox behind, which is exactly the state destroy needs.
        """
        case = next(
            (item for item in cases if item.name == self._config.lifecycle_case),
            None,
        )
        if case is None:
            return []

        samples: list[InvocationSample] = []
        function_name = resources.functions[case.name]
        for phase, disturb in (
            (BenchmarkPhase.RESUMED, self._config.release_sandbox),
            (BenchmarkPhase.REBUILT, self._config.destroy_sandbox),
        ):
            if disturb is None:
                continue
            disturb_started = time.perf_counter()
            await disturb()
            disturb_seconds = time.perf_counter() - disturb_started
            sample = await self._invoke(case, function_name, 0, phase=phase)
            samples.append(replace(sample, disturb_seconds=disturb_seconds))
        return samples

    async def provision(self) -> BenchmarkResources:
        suffix = uuid4().hex[:10]
        cases = benchmark_cases(self._config.batch_rows)
        resources = BenchmarkResources(
            suffix=suffix,
            source_tables=(f"fn_bench_src_a_{suffix}", f"fn_bench_src_b_{suffix}"),
            sink_tables=(f"fn_bench_sink_a_{suffix}", f"fn_bench_sink_b_{suffix}"),
            functions={case.name: f"fn_bench_{case.name}_{suffix}" for case in cases},
        )
        self._resources = resources
        for index, table in enumerate(
            (*resources.source_tables, *resources.sink_tables)
        ):
            await self._request(
                "POST",
                f"/pods/{self._pod_id}/datastore/tables",
                json={
                    "name": table,
                    "primary_key_column": "id",
                    "enable_rls": index % 2 == 0,
                    "columns": [
                        {"name": "id", "type": "UUID", "required": True, "auto": True},
                        {"name": "run_key", "type": "TEXT", "required": True},
                        {"name": "ordinal", "type": "INTEGER", "required": True},
                        {"name": "payload", "type": "TEXT", "required": True},
                    ],
                },
                expected=201,
            )
        seed = [
            {"run_key": "seed", "ordinal": index, "payload": "s" * 64}
            for index in range(self._config.batch_rows)
        ]
        for table in resources.source_tables:
            result = await self._request(
                "POST",
                f"/pods/{self._pod_id}/datastore/tables/{table}/records/bulk/create",
                json={"records": seed},
            )
            if int(result["count"]) != self._config.batch_rows:
                raise AssertionError(f"source seed count mismatch for {table}")

        for case in cases:
            name = resources.functions[case.name]
            table = self._table_for_case(case, resources)
            if case.operation == OperationKind.NOOP:
                source = _noop_function_source(name)
            elif case.operation == OperationKind.READ:
                source = _read_function_source(name, table)
            else:
                source = _write_function_source(name, table)
            await self._request(
                "POST",
                f"/pods/{self._pod_id}/functions",
                json={
                    "name": name,
                    "description": f"Function execution benchmark: {case.name}",
                    "type": case.function_kind.value,
                    "code": source,
                },
                expected=201,
            )
            tables = () if case.operation == OperationKind.NOOP else (table,)
            permission_ids = ["datastore.table.read", "datastore.record.read"]
            if case.operation == OperationKind.WRITE:
                permission_ids.append("datastore.record.write")
            await self._request(
                "PUT",
                f"/pods/{self._pod_id}/functions/{name}/permissions",
                json={
                    "grants": [
                        {
                            "resource_type": "datastore_table",
                            "resource_name": table,
                            "permission_ids": permission_ids,
                        }
                        for table in tables
                    ]
                },
            )
        return resources

    @staticmethod
    def _table_for_case(
        case: BenchmarkCase,
        resources: BenchmarkResources,
    ) -> str:
        index = 0 if case.function_kind == FunctionKind.API else 1
        if case.operation == OperationKind.READ:
            return resources.source_tables[index]
        if case.operation == OperationKind.WRITE:
            return resources.sink_tables[index]
        return ""

    async def cleanup(self, resources: BenchmarkResources) -> None:
        for function in resources.functions.values():
            await self._best_effort_delete(f"/pods/{self._pod_id}/functions/{function}")
        for table in (*resources.sink_tables, *resources.source_tables):
            await self._best_effort_delete(
                f"/pods/{self._pod_id}/datastore/tables/{table}"
            )

    async def _run_case(
        self,
        case: BenchmarkCase,
        function_name: str,
        *,
        phase: BenchmarkPhase,
        invocations: int,
    ) -> tuple[list[InvocationSample], float]:
        semaphore = asyncio.Semaphore(self._config.concurrency)

        async def invoke(index: int) -> InvocationSample:
            async with semaphore:
                return await self._invoke(
                    case,
                    function_name,
                    index,
                    phase=phase,
                )

        wall_started = time.perf_counter()
        results = await asyncio.gather(
            *(invoke(index) for index in range(1, invocations + 1))
        )
        return list(results), time.perf_counter() - wall_started

    async def _invoke(
        self,
        case: BenchmarkCase,
        function_name: str,
        index: int,
        *,
        phase: BenchmarkPhase,
    ) -> InvocationSample:
        hold_ms = (
            self._config.pool_fill_hold_ms if phase == BenchmarkPhase.POOL_FILL else 0
        )
        if case.operation == OperationKind.NOOP:
            input_data = {"value": index, "hold_ms": hold_ms}
        elif case.operation == OperationKind.READ:
            input_data = {
                "limit": case.rows_per_invocation,
                "hold_ms": hold_ms,
            }
        else:
            input_data = {
                "run_key": (
                    f"{self._config.provider}-{case.name}-"
                    f"{phase.value}-{index}-{uuid4().hex[:8]}"
                ),
                "rows": case.rows_per_invocation,
                "hold_ms": hold_ms,
            }
        started = time.perf_counter()
        run_id: str | None = None
        try:
            response_started = time.perf_counter()
            response = await self._client.post(
                f"/pods/{self._pod_id}/functions/{function_name}/runs",
                json={"input_data": input_data},
                follow_redirects=True,
            )
            submit_seconds = time.perf_counter() - response_started
            response.raise_for_status()
            run = response.json()
            run_id = str(run["id"])
            if run.get("status") not in TERMINAL_STATUSES:
                run = await self._wait_for_terminal(function_name, run_id)
            terminal_seconds = time.perf_counter() - started
            output = run.get("output_data")
            error = run.get("error")
            status = str(run.get("status"))
            if status == "COMPLETED":
                self._validate_output(case, output)
            queue_seconds, execution_seconds = self._server_timings(run)
            function_call_seconds = self._function_call_seconds(
                case.operation,
                output,
            )
            platform_overhead_seconds = self._platform_overhead_seconds(
                case=case.name,
                terminal_seconds=terminal_seconds,
                queue_seconds=queue_seconds,
                execution_seconds=execution_seconds,
                function_call_seconds=function_call_seconds,
            )
            return InvocationSample(
                case=case.name,
                phase=phase.value,
                index=index,
                run_id=run_id,
                status=status,
                submit_seconds=submit_seconds,
                terminal_seconds=terminal_seconds,
                queue_seconds=queue_seconds,
                execution_seconds=execution_seconds,
                function_call_seconds=function_call_seconds,
                platform_overhead_seconds=platform_overhead_seconds,
                output_data=output,
                error=error,
            )
        except Exception as exc:
            return InvocationSample(
                case=case.name,
                phase=phase.value,
                index=index,
                run_id=run_id,
                status="CLIENT_ERROR",
                submit_seconds=time.perf_counter() - started,
                terminal_seconds=time.perf_counter() - started,
                queue_seconds=None,
                execution_seconds=None,
                function_call_seconds=None,
                platform_overhead_seconds=None,
                output_data=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _wait_for_terminal(
        self, function_name: str, run_id: str
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._config.terminal_timeout_seconds
        # JOB submit returns PENDING by design. Do not immediately issue a read
        # that races the dispatcher and amplifies into a status-request herd.
        await asyncio.sleep(self._config.poll_interval_seconds)
        while time.monotonic() < deadline:
            run = await self._request(
                "GET",
                f"/pods/{self._pod_id}/functions/{function_name}/runs/{run_id}",
            )
            if run.get("status") in TERMINAL_STATUSES:
                return run
            await asyncio.sleep(self._config.poll_interval_seconds)
        raise TimeoutError(f"function run {run_id} did not become terminal")

    @staticmethod
    def _platform_overhead_seconds(
        *,
        case: str,
        terminal_seconds: float,
        queue_seconds: float | None,
        execution_seconds: float | None,
        function_call_seconds: float | None,
    ) -> float | None:
        if function_call_seconds is None:
            return None
        if (
            case.startswith("job_")
            and queue_seconds is not None
            and execution_seconds is not None
        ):
            # A JOB caller learns completion by polling, so client-observation
            # delay is not execution-platform overhead. Durable run timestamps
            # cover exactly creation -> claim -> terminal callback.
            return max(
                0.0,
                queue_seconds + execution_seconds - function_call_seconds,
            )
        # API execution is synchronous, so caller-observed terminal latency is
        # the contract and must include every backend/runtime round trip.
        return max(0.0, terminal_seconds - function_call_seconds)

    def _validate_output(
        self,
        case: BenchmarkCase,
        output: dict[str, Any] | None,
    ) -> None:
        if output is None:
            raise AssertionError("completed run has no output")
        if case.operation == OperationKind.NOOP:
            if "value" not in output:
                raise AssertionError("no-op function returned the wrong output")
        elif case.operation == OperationKind.READ:
            if int(output.get("rows_read", -1)) != case.rows_per_invocation:
                raise AssertionError("read function returned the wrong row count")
            if int(output.get("table_total", -1)) != self._config.batch_rows:
                raise AssertionError("read function returned the wrong table total")
        else:
            if int(output.get("rows_written", -1)) != case.rows_per_invocation:
                raise AssertionError("write function returned the wrong row count")

    async def _verify_sink_rows(self, resources: BenchmarkResources) -> dict[str, int]:
        totals: dict[str, int] = {}
        for table in resources.sink_tables:
            response = await self._request(
                "GET",
                f"/pods/{self._pod_id}/datastore/tables/{table}/records",
                params={"limit": 1},
            )
            totals[table] = int(response["total"])
        return totals

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected: int = 200,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code != expected:
            raise RuntimeError(
                f"{method} {path} returned {response.status_code}: "
                f"{response.text[:1000]}"
            )
        if not response.content:
            return {}
        return response.json()

    async def _best_effort_delete(self, path: str) -> None:
        try:
            response = await self._client.delete(path)
            if response.status_code not in {200, 204, 404}:
                response.raise_for_status()
        except Exception:
            return

    @staticmethod
    def _function_call_seconds(
        operation: OperationKind,
        output: dict[str, Any] | None,
    ) -> float | None:
        if output is None:
            return None
        if operation == OperationKind.NOOP:
            return 0.0
        try:
            milliseconds = float(output["call_ms"])
        except KeyError, TypeError, ValueError:
            return None
        return max(0.0, milliseconds / 1000)

    @staticmethod
    def _server_timings(run: dict[str, Any]) -> tuple[float | None, float | None]:
        def parsed(name: str) -> datetime | None:
            value = run.get(name)
            if not value:
                return None
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        created = parsed("created_at")
        started = parsed("started_at")
        completed = parsed("completed_at")
        queue = (started - created).total_seconds() if created and started else None
        execution = (
            (completed - started).total_seconds() if started and completed else None
        )
        return queue, execution
