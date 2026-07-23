"""Reusable full-path API/JOB function execution benchmark.

The benchmark provisions four isolated pod tables and four immutable functions,
warms the per-pod sandbox once, then drives each function with bounded client
concurrency. It intentionally uses only public Lemma HTTP APIs so the measured
path includes authorization, durable runs/attempts, AgentBox allocation,
sandbox process execution, runtime callbacks, delegated SDK table access, and
terminal result persistence.
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
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class LatencyBudget:
    case: str
    terminal_p95_seconds: float
    submit_p95_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.case:
            raise ValueError("latency budget case is required")
        if self.terminal_p95_seconds <= 0:
            raise ValueError("terminal p95 budget must be positive")
        if self.submit_p95_seconds is not None and self.submit_p95_seconds <= 0:
            raise ValueError("submit p95 budget must be positive")


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    provider: str
    concurrency: int = 5
    invocations: int = 5
    source_rows_per_table: int = 1_000
    rows_per_write: int = 1_000
    poll_interval_seconds: float = 0.1
    terminal_timeout_seconds: float = 180.0
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
    index: int
    run_id: str | None
    status: str
    submit_seconds: float
    terminal_seconds: float
    queue_seconds: float | None
    execution_seconds: float | None
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
    completed: int
    failed: int
    success_rate: float
    wall_seconds: float
    invocations_per_second: float
    submit: TimingSummary
    terminal: TimingSummary
    queue: TimingSummary
    execution: TimingSummary


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    schema_version: int
    provider: str
    started_at: str
    finished_at: str
    config: dict[str, Any]
    resources: dict[str, Any]
    warmup: tuple[InvocationSample, ...]
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
    wall_seconds: float,
) -> CaseSummary:
    completed = sum(sample.status == "COMPLETED" for sample in samples)
    return CaseSummary(
        case=case,
        function_kind=function_kind.value,
        operation=operation.value,
        completed=completed,
        failed=len(samples) - completed,
        success_rate=completed / len(samples) if samples else 0.0,
        wall_seconds=wall_seconds,
        invocations_per_second=(completed / wall_seconds if wall_seconds else 0.0),
        submit=_timings([sample.submit_seconds for sample in samples]),
        terminal=_timings([sample.terminal_seconds for sample in samples]),
        queue=_timings([sample.queue_seconds for sample in samples]),
        execution=_timings([sample.execution_seconds for sample in samples]),
    )


def evaluate_latency_budgets(
    cases: tuple[CaseSummary, ...] | list[CaseSummary],
    budgets: tuple[LatencyBudget, ...],
) -> tuple[str, ...]:
    """Return stable quality-gate failures for a benchmark report."""

    summaries = {summary.case: summary for summary in cases}
    failures: list[str] = []
    for budget in budgets:
        summary = summaries.get(budget.case)
        if summary is None:
            failures.append(f"latency budget references missing case {budget.case}")
            continue
        terminal_p95 = summary.terminal.p95_seconds
        if terminal_p95 is None:
            failures.append(f"{budget.case} terminal p95 has no samples")
        elif terminal_p95 > budget.terminal_p95_seconds:
            failures.append(
                f"{budget.case} terminal p95 {terminal_p95:.3f}s exceeds "
                f"{budget.terminal_p95_seconds:.3f}s"
            )
        if budget.submit_p95_seconds is None:
            continue
        submit_p95 = summary.submit.p95_seconds
        if submit_p95 is None:
            failures.append(f"{budget.case} submit p95 has no samples")
        elif submit_p95 > budget.submit_p95_seconds:
            failures.append(
                f"{budget.case} submit p95 {submit_p95:.3f}s exceeds "
                f"{budget.submit_p95_seconds:.3f}s"
            )
    return tuple(failures)


def write_report(report: BenchmarkReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


def _read_function_source(name: str, first_table: str, second_table: str) -> str:
    return f'''#input_type_name: ReadInput
#output_type_name: ReadResult
#function_name: {name}

from pydantic import BaseModel, Field
from lemma_sdk import FunctionContext, Pod

class ReadInput(BaseModel):
    limit: int = Field(ge=1, le=1000)

class ReadResult(BaseModel):
    rows_read: int
    checksum: int
    first_total: int
    second_total: int

async def {name}(ctx: FunctionContext, data: ReadInput) -> ReadResult:
    pod = Pod.from_env()
    first = pod.table("{first_table}").list(limit=data.limit)
    second = pod.table("{second_table}").list(limit=data.limit)
    first_rows = list(first.items)
    second_rows = list(second.items)
    return ReadResult(
        rows_read=len(first_rows) + len(second_rows),
        checksum=sum(int(row["ordinal"]) for row in first_rows + second_rows),
        first_total=int(first.total),
        second_total=int(second.total),
    )
'''


def _write_function_source(name: str, first_table: str, second_table: str) -> str:
    return f'''#input_type_name: WriteInput
#output_type_name: WriteResult
#function_name: {name}

from pydantic import BaseModel, Field
from lemma_sdk import FunctionContext, Pod

class WriteInput(BaseModel):
    run_key: str
    rows: int = Field(ge=2, le=1000)

class WriteResult(BaseModel):
    rows_written: int
    first_count: int
    second_count: int

async def {name}(ctx: FunctionContext, data: WriteInput) -> WriteResult:
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
    first_count = pod.records.bulk_create("{first_table}", first)
    second_count = pod.records.bulk_create("{second_table}", second)
    return WriteResult(
        rows_written=first_count + second_count,
        first_count=first_count,
        second_count=second_count,
    )
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
        warmup: list[InvocationSample] = []
        samples: list[InvocationSample] = []
        summaries: list[CaseSummary] = []
        verified_sink_rows: dict[str, int] = {}
        resources: BenchmarkResources | None = None
        try:
            resources = await self.provision()
            cases = (
                ("api_read", FunctionKind.API, OperationKind.READ),
                ("api_write", FunctionKind.API, OperationKind.WRITE),
                ("job_read", FunctionKind.JOB, OperationKind.READ),
                ("job_write", FunctionKind.JOB, OperationKind.WRITE),
            )
            for case, function_kind, operation in cases:
                warmup.append(
                    await self._invoke(
                        case,
                        resources.functions[case],
                        0,
                        operation,
                        warmup=True,
                    )
                )
                case_samples, wall_seconds = await self._run_case(
                    case,
                    resources.functions[case],
                    operation,
                )
                samples.extend(case_samples)
                summaries.append(
                    summarize_case(
                        case,
                        function_kind,
                        operation,
                        case_samples,
                        wall_seconds=wall_seconds,
                    )
                )

            verified_sink_rows = await self._verify_sink_rows(resources)
            expected_total = (
                self._config.invocations * self._config.rows_per_write * 2 + 4
            )
            actual_total = sum(verified_sink_rows.values())
            if actual_total != expected_total:
                errors.append(
                    f"sink row verification failed: expected {expected_total}, "
                    f"found {actual_total}"
                )
            for sample in (*warmup, *samples):
                if sample.status != "COMPLETED":
                    errors.append(
                        f"{sample.case}[{sample.index}] {sample.status}: "
                        f"{sample.error or 'unknown failure'}"
                    )
            errors.extend(
                evaluate_latency_budgets(summaries, self._config.latency_budgets)
            )
        finally:
            cleanup_resources = resources or self._resources
            if self._config.cleanup and cleanup_resources is not None:
                await self.cleanup(cleanup_resources)

        if resources is None:
            raise RuntimeError("benchmark provisioning completed without resources")

        return BenchmarkReport(
            schema_version=1,
            provider=self._config.provider,
            started_at=started.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            config=asdict(self._config),
            resources=asdict(resources),
            warmup=tuple(warmup),
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
                resources.source_tables
                if case.endswith("read")
                else resources.sink_tables
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
    ) -> tuple[list[InvocationSample], float]:
        semaphore = asyncio.Semaphore(self._config.concurrency)

        async def invoke(index: int) -> InvocationSample:
            async with semaphore:
                return await self._invoke(
                    case, function_name, index, operation, warmup=False
                )

        wall_started = time.perf_counter()
        results = await asyncio.gather(
            *(invoke(index) for index in range(1, self._config.invocations + 1))
        )
        return list(results), time.perf_counter() - wall_started

    async def _invoke(
        self,
        case: str,
        function_name: str,
        index: int,
        operation: OperationKind,
        *,
        warmup: bool,
    ) -> InvocationSample:
        input_data = (
            {"limit": self._config.source_rows_per_table}
            if operation == OperationKind.READ
            else {
                "run_key": (
                    f"{self._config.provider}-{case}-"
                    f"{'warmup' if warmup else index}-{uuid4().hex[:8]}"
                ),
                "rows": 2 if warmup else self._config.rows_per_write,
            }
        )
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
                self._validate_output(operation, output, warmup=warmup)
            queue_seconds, execution_seconds = self._server_timings(run)
            return InvocationSample(
                case=case,
                index=index,
                run_id=run_id,
                status=status,
                submit_seconds=submit_seconds,
                terminal_seconds=terminal_seconds,
                queue_seconds=queue_seconds,
                execution_seconds=execution_seconds,
                output_data=output,
                error=error,
            )
        except Exception as exc:
            return InvocationSample(
                case=case,
                index=index,
                run_id=run_id,
                status="CLIENT_ERROR",
                submit_seconds=time.perf_counter() - started,
                terminal_seconds=time.perf_counter() - started,
                queue_seconds=None,
                execution_seconds=None,
                output_data=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _wait_for_terminal(
        self, function_name: str, run_id: str
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._config.terminal_timeout_seconds
        while time.monotonic() < deadline:
            run = await self._request(
                "GET",
                f"/pods/{self._pod_id}/functions/{function_name}/runs/{run_id}",
            )
            if run.get("status") in TERMINAL_STATUSES:
                return run
            await asyncio.sleep(self._config.poll_interval_seconds)
        raise TimeoutError(f"function run {run_id} did not become terminal")

    def _validate_output(
        self,
        operation: OperationKind,
        output: dict[str, Any] | None,
        *,
        warmup: bool,
    ) -> None:
        if output is None:
            raise AssertionError("completed run has no output")
        if operation == OperationKind.READ:
            expected_rows = self._config.source_rows_per_table * 2
            expected_checksum = self._config.source_rows_per_table * (
                self._config.source_rows_per_table - 1
            )
            if int(output.get("rows_read", -1)) != expected_rows:
                raise AssertionError("read function returned the wrong row count")
            if int(output.get("checksum", -1)) != expected_checksum:
                raise AssertionError("read function returned the wrong checksum")
        else:
            expected_rows = 2 if warmup else self._config.rows_per_write
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
