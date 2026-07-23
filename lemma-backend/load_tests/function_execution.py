"""Reusable full-path API/JOB function execution benchmark.

The benchmark provisions four isolated pod tables and four immutable functions,
warms the per-pod sandbox once, then drives each function with bounded client
concurrency. It intentionally uses only public Lemma HTTP APIs so the measured
path includes authorization, durable runs, AgentBox allocation, resident runtime
dispatch, runtime callbacks, delegated SDK table access, and terminal result
persistence.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import json
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


@dataclass(frozen=True, slots=True)
class LatencyBudget:
    case: str
    terminal_p95_seconds: float | None = None
    submit_p95_seconds: float | None = None
    platform_overhead_p95_seconds: float | None = None
    cold_terminal_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.case:
            raise ValueError("latency budget case is required")
        configured = (
            self.terminal_p95_seconds,
            self.submit_p95_seconds,
            self.platform_overhead_p95_seconds,
            self.cold_terminal_seconds,
        )
        if not any(value is not None for value in configured):
            raise ValueError("latency budget must configure at least one limit")
        if any(value is not None and value <= 0 for value in configured):
            raise ValueError("latency budget limits must be positive")


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    provider: str
    concurrency: int = 5
    invocations: int = 5
    source_rows_per_table: int = 1_000
    rows_per_write: int = 1_000
    # JOB completion is observed out of band. Poll slowly enough that the
    # observer does not become the workload under test, especially at higher
    # invocation concurrency.
    poll_interval_seconds: float = 0.5
    terminal_timeout_seconds: float = 180.0
    pool_fill_hold_ms: int = 750
    cleanup: bool = True
    latency_budgets: tuple[LatencyBudget, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("provider is required")
        if self.concurrency < 1 or self.invocations < 1:
            raise ValueError("concurrency and invocations must be positive")
        if self.source_rows_per_table < 1 or self.source_rows_per_table > 1_000:
            raise ValueError("source rows per table must be in 1..1000")
        if self.rows_per_write < 2 or self.rows_per_write > 1_000:
            raise ValueError("rows per write must be in 2..1000")
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
    case: str,
    function_kind: FunctionKind,
    operation: OperationKind,
    samples: list[InvocationSample],
    *,
    concurrency: int,
    wall_seconds: float,
) -> CaseSummary:
    completed = sum(sample.status == "COMPLETED" for sample in samples)
    return CaseSummary(
        case=case,
        function_kind=function_kind.value,
        operation=operation.value,
        concurrency=concurrency,
        completed=completed,
        failed=len(samples) - completed,
        success_rate=completed / len(samples) if samples else 0.0,
        wall_seconds=wall_seconds,
        invocations_per_second=(completed / wall_seconds if wall_seconds else 0.0),
        submit=_timings([sample.submit_seconds for sample in samples]),
        terminal=_timings([sample.terminal_seconds for sample in samples]),
        queue=_timings([sample.queue_seconds for sample in samples]),
        execution=_timings([sample.execution_seconds for sample in samples]),
        function_call=_timings([sample.function_call_seconds for sample in samples]),
        platform_overhead=_timings(
            [sample.platform_overhead_seconds for sample in samples]
        ),
    )


def evaluate_latency_budgets(
    cases: tuple[CaseSummary, ...] | list[CaseSummary],
    budgets: tuple[LatencyBudget, ...],
    *,
    cold: tuple[InvocationSample, ...] | list[InvocationSample] = (),
) -> tuple[str, ...]:
    """Return stable quality-gate failures for a benchmark report."""

    summaries = {summary.case: summary for summary in cases}
    cold_by_case = {sample.case: sample for sample in cold}
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
                failures.append(
                    f"{budget.case} platform overhead p95 has no samples"
                )
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
    return tuple(failures)


def write_report(report: BenchmarkReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


def _read_function_source(name: str, first_table: str, second_table: str) -> str:
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
    first_total: int
    second_total: int
    first_call_ms: float
    second_call_ms: float

async def {name}(ctx: FunctionContext, data: ReadInput) -> ReadResult:
    if data.hold_ms:
        await asyncio.sleep(data.hold_ms / 1000)
    pod = Pod.from_env()
    first_started = time.perf_counter()
    first = pod.table("{first_table}").list(limit=data.limit)
    first_call_ms = (time.perf_counter() - first_started) * 1000
    second_started = time.perf_counter()
    second = pod.table("{second_table}").list(limit=data.limit)
    second_call_ms = (time.perf_counter() - second_started) * 1000
    first_rows = list(first.items)
    second_rows = list(second.items)
    return ReadResult(
        rows_read=len(first_rows) + len(second_rows),
        checksum=sum(int(row["ordinal"]) for row in first_rows + second_rows),
        first_total=int(first.total),
        second_total=int(second.total),
        first_call_ms=first_call_ms,
        second_call_ms=second_call_ms,
    )
'''


def _write_function_source(name: str, first_table: str, second_table: str) -> str:
    return f'''#input_type_name: WriteInput
#output_type_name: WriteResult
#function_name: {name}

import asyncio
import time

from pydantic import BaseModel, Field
from lemma_sdk import FunctionContext, Pod

class WriteInput(BaseModel):
    run_key: str
    rows: int = Field(ge=2, le=1000)
    hold_ms: int = Field(default=0, ge=0, le=10000)

class WriteResult(BaseModel):
    rows_written: int
    first_count: int
    second_count: int
    first_call_ms: float
    second_call_ms: float

async def {name}(ctx: FunctionContext, data: WriteInput) -> WriteResult:
    if data.hold_ms:
        await asyncio.sleep(data.hold_ms / 1000)
    pod = Pod.from_env()
    first_size = data.rows // 2
    second_size = data.rows - first_size
    first = [
        {{"run_key": data.run_key, "ordinal": index, "payload": "a" * 64}}
        for index in range(first_size)
    ]
    second = [
        {{"run_key": data.run_key, "ordinal": index, "payload": "b" * 64}}
        for index in range(second_size)
    ]
    first_started = time.perf_counter()
    first_count = pod.records.bulk_create("{first_table}", first)
    first_call_ms = (time.perf_counter() - first_started) * 1000
    second_started = time.perf_counter()
    second_count = pod.records.bulk_create("{second_table}", second)
    second_call_ms = (time.perf_counter() - second_started) * 1000
    return WriteResult(
        rows_written=first_count + second_count,
        first_count=first_count,
        second_count=second_count,
        first_call_ms=first_call_ms,
        second_call_ms=second_call_ms,
    )
'''


def _noop_function_source(name: str) -> str:
    return f'''#input_type_name: NoopInput
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
'''


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
        samples: list[InvocationSample] = []
        summaries: list[CaseSummary] = []
        verified_sink_rows: dict[str, int] = {}
        resources: BenchmarkResources | None = None
        try:
            resources = await self.provision()
            cases = (
                ("api_noop", FunctionKind.API, OperationKind.NOOP),
                ("api_read", FunctionKind.API, OperationKind.READ),
                ("api_write", FunctionKind.API, OperationKind.WRITE),
                ("job_read", FunctionKind.JOB, OperationKind.READ),
                ("job_write", FunctionKind.JOB, OperationKind.WRITE),
            )
            for case, function_kind, operation in cases:
                cold.append(
                    await self._invoke(
                        case,
                        resources.functions[case],
                        0,
                        operation,
                        phase=BenchmarkPhase.COLD,
                    )
                )
                fill_samples, _fill_wall = await self._run_case(
                    case,
                    resources.functions[case],
                    operation,
                    phase=BenchmarkPhase.POOL_FILL,
                    invocations=self._config.concurrency,
                )
                pool_fill.extend(fill_samples)
                case_samples, wall_seconds = await self._run_case(
                    case,
                    resources.functions[case],
                    operation,
                    phase=BenchmarkPhase.STEADY,
                    invocations=self._config.invocations,
                )
                samples.extend(case_samples)
                summaries.append(
                    summarize_case(
                        case,
                        function_kind,
                        operation,
                        case_samples,
                        concurrency=self._config.concurrency,
                        wall_seconds=wall_seconds,
                    )
                )

            verified_sink_rows = await self._verify_sink_rows(resources)
            expected_total = (
                self._config.invocations * self._config.rows_per_write * 2
                + self._config.concurrency * 4
                + 4
            )
            actual_total = sum(verified_sink_rows.values())
            if actual_total != expected_total:
                errors.append(
                    f"sink row verification failed: expected {expected_total}, "
                    f"found {actual_total}"
                )
            for sample in (*cold, *pool_fill, *samples):
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
                )
            )
        finally:
            cleanup_resources = resources or self._resources
            if self._config.cleanup and cleanup_resources is not None:
                await self.cleanup(cleanup_resources)

        if resources is None:
            raise RuntimeError("benchmark provisioning completed without resources")

        return BenchmarkReport(
            schema_version=2,
            provider=self._config.provider,
            started_at=started.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            config=asdict(self._config),
            resources=asdict(resources),
            cold=tuple(cold),
            pool_fill=tuple(pool_fill),
            cases=tuple(summaries),
            samples=tuple(samples),
            verified_sink_rows=verified_sink_rows,
            errors=tuple(errors),
        )

    async def provision(self) -> BenchmarkResources:
        suffix = uuid4().hex[:10]
        resources = BenchmarkResources(
            suffix=suffix,
            source_tables=(f"fn_bench_src_a_{suffix}", f"fn_bench_src_b_{suffix}"),
            sink_tables=(f"fn_bench_sink_a_{suffix}", f"fn_bench_sink_b_{suffix}"),
            functions={
                "api_noop": f"fn_bench_api_noop_{suffix}",
                "api_read": f"fn_bench_api_read_{suffix}",
                "api_write": f"fn_bench_api_write_{suffix}",
                "job_read": f"fn_bench_job_read_{suffix}",
                "job_write": f"fn_bench_job_write_{suffix}",
            },
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
            for index in range(self._config.source_rows_per_table)
        ]
        for table in resources.source_tables:
            result = await self._request(
                "POST",
                f"/pods/{self._pod_id}/datastore/tables/{table}/records/bulk/create",
                json={"records": seed},
            )
            if int(result["count"]) != self._config.source_rows_per_table:
                raise AssertionError(f"source seed count mismatch for {table}")

        definitions = (
            (
                "api_noop",
                FunctionKind.API,
                _noop_function_source(resources.functions["api_noop"]),
            ),
            (
                "api_read",
                FunctionKind.API,
                _read_function_source(
                    resources.functions["api_read"], *resources.source_tables
                ),
            ),
            (
                "api_write",
                FunctionKind.API,
                _write_function_source(
                    resources.functions["api_write"], *resources.sink_tables
                ),
            ),
            (
                "job_read",
                FunctionKind.JOB,
                _read_function_source(
                    resources.functions["job_read"], *resources.source_tables
                ),
            ),
            (
                "job_write",
                FunctionKind.JOB,
                _write_function_source(
                    resources.functions["job_write"], *resources.sink_tables
                ),
            ),
        )
        for case, function_kind, source in definitions:
            name = resources.functions[case]
            await self._request(
                "POST",
                f"/pods/{self._pod_id}/functions",
                json={
                    "name": name,
                    "description": f"Function execution benchmark: {case}",
                    "type": function_kind.value,
                    "code": source,
                },
                expected=201,
            )
            tables = (
                ()
                if case.endswith("noop")
                else (
                    resources.source_tables
                    if case.endswith("read")
                    else resources.sink_tables
                )
            )
            permission_ids = ["datastore.table.read", "datastore.record.read"]
            if case.endswith("write"):
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

    async def cleanup(self, resources: BenchmarkResources) -> None:
        for function in resources.functions.values():
            await self._best_effort_delete(f"/pods/{self._pod_id}/functions/{function}")
        for table in (*resources.sink_tables, *resources.source_tables):
            await self._best_effort_delete(
                f"/pods/{self._pod_id}/datastore/tables/{table}"
            )

    async def _run_case(
        self,
        case: str,
        function_name: str,
        operation: OperationKind,
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
                    operation,
                    phase=phase,
                )

        wall_started = time.perf_counter()
        results = await asyncio.gather(
            *(invoke(index) for index in range(1, invocations + 1))
        )
        return list(results), time.perf_counter() - wall_started

    async def _invoke(
        self,
        case: str,
        function_name: str,
        index: int,
        operation: OperationKind,
        *,
        phase: BenchmarkPhase,
    ) -> InvocationSample:
        hold_ms = (
            self._config.pool_fill_hold_ms
            if phase == BenchmarkPhase.POOL_FILL
            else 0
        )
        if operation == OperationKind.NOOP:
            input_data = {"value": index, "hold_ms": hold_ms}
        elif operation == OperationKind.READ:
            input_data = {
                "limit": self._config.source_rows_per_table,
                "hold_ms": hold_ms,
            }
        else:
            input_data = {
                "run_key": (
                    f"{self._config.provider}-{case}-"
                    f"{phase.value}-{index}-{uuid4().hex[:8]}"
                ),
                "rows": (
                    2
                    if phase != BenchmarkPhase.STEADY
                    else self._config.rows_per_write
                ),
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
                self._validate_output(operation, output, phase=phase)
            queue_seconds, execution_seconds = self._server_timings(run)
            function_call_seconds = self._function_call_seconds(operation, output)
            platform_overhead_seconds = self._platform_overhead_seconds(
                case=case,
                terminal_seconds=terminal_seconds,
                queue_seconds=queue_seconds,
                execution_seconds=execution_seconds,
                function_call_seconds=function_call_seconds,
            )
            return InvocationSample(
                case=case,
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
                case=case,
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
        operation: OperationKind,
        output: dict[str, Any] | None,
        *,
        phase: BenchmarkPhase,
    ) -> None:
        if output is None:
            raise AssertionError("completed run has no output")
        if operation == OperationKind.NOOP:
            if "value" not in output:
                raise AssertionError("no-op function returned the wrong output")
        elif operation == OperationKind.READ:
            expected_rows = self._config.source_rows_per_table * 2
            expected_checksum = self._config.source_rows_per_table * (
                self._config.source_rows_per_table - 1
            )
            if int(output.get("rows_read", -1)) != expected_rows:
                raise AssertionError("read function returned the wrong row count")
            if int(output.get("checksum", -1)) != expected_checksum:
                raise AssertionError("read function returned the wrong checksum")
        else:
            expected_rows = (
                self._config.rows_per_write
                if phase == BenchmarkPhase.STEADY
                else 2
            )
            if int(output.get("rows_written", -1)) != expected_rows:
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
            milliseconds = float(output["first_call_ms"]) + float(
                output["second_call_ms"]
            )
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
