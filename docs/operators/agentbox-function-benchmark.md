# AgentBox Function Execution Benchmark

The function benchmark is the repeatable full-path quality gate for the shared
per-pod function sandbox. It executes real API and JOB functions against either
Docker or E2B; no provider, function runner, SDK, gateway, queue, or database
boundary is mocked.

## Workload

Each run creates a fresh pod, two source tables, two sink tables, and four immutable
functions:

| Case | Type | Work performed per measured invocation |
| --- | --- | --- |
| `api_read` | API | Read 1,000 rows from each of two tables and verify a checksum |
| `api_write` | API | Write 1,000 rows split across two tables |
| `job_read` | JOB | Same two-table read through the durable background path |
| `job_write` | JOB | Same two-table write through the durable background path |

Every case receives one unmeasured warmup followed by five measured invocations at
client concurrency five. The harness verifies terminal outputs and the exact sink
row total, writes a JSON report, then destroys the tracked workspace and function
sandboxes by logical identity. E2B additionally uses a uniquely scoped allocation
label and exact provider-ID cleanup.

## Commands

From `lemma-backend`:

```bash
make benchmark-functions-docker
make benchmark-functions-e2b
```

The E2B command reads `E2B_API_KEY` from `lemma-backend/.env` or the process
environment. The Makefile pins the immutable workspace and function template/build
IDs that passed promotion. A candidate build can be tested without changing the
pin by overriding the four `AGENTBOX_E2B_*` variables.

Reports are written beneath:

```text
.benchmark-results/function-execution/<provider>/<timestamp>.json
```

The file contains configuration, immutable resource names, every invocation,
client-observed submit and terminal latency, server queue and execution latency,
sink verification, and all gate failures. Reports may be uploaded as CI evidence;
they are not committed.

## Default regression budgets

The test fails on any incorrect result, nonterminal run, missing row, provider error,
or p95 budget breach.

| Provider | Case | Terminal p95 | Submit p95 |
| --- | --- | ---: | ---: |
| Docker | `api_read` | 45 s | terminal response |
| Docker | `api_write` | 90 s | terminal response |
| Docker | `job_read` | 45 s | 2 s |
| Docker | `job_write` | 90 s | 2 s |
| E2B | `api_read` | 15 s | terminal response |
| E2B | `api_write` | 45 s | terminal response |
| E2B | `job_read` | 20 s | 2 s |
| E2B | `job_write` | 55 s | 2 s |

API submission is the terminal response by contract. JOB submission must remain
fast because it returns the durable `PENDING` run while execution continues.
Thresholds can be overridden per run, for example
`FUNCTION_BENCH_API_WRITE_TERMINAL_P95_SECONDS=60`; a scheduled lane must not relax
them without an explicit reviewed change.

These budgets are regression ceilings for this table-heavy workload, not the
smaller no-op platform-overhead SLOs in the AgentBox verification design.

## Initial parity evidence

The first complete parity run on 2026-07-23 passed all 20 measured invocations on
each provider at concurrency five and verified all writes.

| Provider | API read p95 | API write p95 | JOB read p95 | JOB write p95 |
| --- | ---: | ---: | ---: | ---: |
| Docker | 29.45 s | 59.10 s | 29.99 s | 62.04 s |
| E2B | 8.35 s | 27.70 s | 12.57 s | 35.30 s |

The Docker result includes local daemon and host contention and is not comparable to
a production isolation runtime. Trends should be compared only within the same
provider, runner class, profile digest, workload, and concurrency.

## Automation

`.github/workflows/agentbox-function-benchmark.yml` runs Docker nightly and E2B
weekly, supports either provider or both through manual dispatch, and publishes the
JSON reports under the tested commit SHA. Each lane first runs that provider's real
workspace/function conformance suite, including shell, PTY, files, stateful Python,
headful browser, lifecycle, and runner coverage. The E2B job uses the protected
`backend-protected-e2e` environment and fails when its credential is absent; it
never downgrades a required provider run to a skip.
