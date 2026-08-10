# Sandbox function execution benchmark

The function benchmark is the repeatable full-path quality gate for the shared
per-pod function sandbox. It executes real API and JOB functions against either
Docker or E2B; no provider, function runner, SDK, gateway, queue, or database
boundary is mocked.

## Workload

Each run creates a fresh pod, two source tables, two sink tables, and five immutable
functions:

| Case | Type | Work performed per measured invocation |
| --- | --- | --- |
| `api_noop` | API | Return immediately; isolates platform overhead |
| `api_read` | API | Read 1,000 rows from each of two tables and verify a checksum |
| `api_write` | API | Write 1,000 rows split across two tables |
| `job_read` | JOB | Same two-table read through the durable background path |
| `job_write` | JOB | Same two-table write through the durable background path |

Every case has three explicit phases:

1. one cold invocation, recorded separately;
2. one concurrent pool-fill wave that holds each invocation for 750 ms, proving the
   requested worker concurrency is actually resident;
3. measured steady-state invocations after that pool is warm.

The harness defaults to five measured invocations at concurrency five. It verifies
terminal outputs and the exact sink row total, writes a schema-versioned JSON
report, then destroys the tracked workspace and function sandboxes by logical
identity. E2B additionally uses a uniquely scoped allocation label and exact
provider-ID cleanup.

## Commands

From `lemma-backend`:

```bash
make benchmark-functions-docker
make benchmark-functions-e2b
```

The E2B command reads `E2B_API_KEY` from `lemma-backend/.env` or the process
environment. It uses exact immutable workspace and function template/build IDs.
A candidate build is tested without changing any E2B template, alias, or account
setting by overriding the four `E2B_*` variables.

The local synthetic backend is exposed to E2B through a temporary ngrok tunnel by
default. Set `FUNCTION_BENCH_TUNNEL=cloudflared` to test through Cloudflare, or
`FUNCTION_BENCH_PUBLIC_URL` to use an already-running test endpoint. The temporary
tunnel is test transport only and does not modify E2B configuration.

Reports are written beneath:

```text
.benchmark-results/function-execution/<provider>/<timestamp>.json
```

The file contains configuration, immutable resource names, cold and pool-fill
samples, every steady invocation, client-observed submit and terminal latency,
function-reported Lemma SDK call time, derived platform overhead, sink
verification, and all gate failures. Reports may be uploaded as CI evidence; they
are not committed.

## Default regression budgets

The test fails on any incorrect result, nonterminal run, missing row, provider error,
or p95 budget breach.

| Measure | Default gate |
| --- | ---: |
| Warm API no-op terminal p95 | 2 s |
| Cold API no-op terminal | 8 s |
| Platform overhead p95 for every case | 2 s |
| JOB submission p95 | 2 s |

API submission is the terminal response by contract. JOB submission must remain
fast because it returns the durable `PENDING` run while execution continues.
Thresholds can be overridden per run, for example
`FUNCTION_BENCH_API_WRITE_PLATFORM_OVERHEAD_P95_SECONDS=3`; a scheduled lane must
not relax them without an explicit reviewed change. Read/write total latency is
reported but the quality gate subtracts the function's measured Lemma API call
time, so a datastore regression is visible separately from executor overhead.

## Current parity evidence

On 2026-07-23 the current implementation passed Docker and E2B at concurrency one
and five. The concurrency-five Docker run completed all 25 invocations with API
no-op/read/write platform p95 of 0.27/0.33/0.43 s and JOB read/write p95 of
0.49/0.66 s. The concurrency-five E2B run through ngrok completed all 25
invocations with API no-op/read/write platform p95 of 1.47/1.63/1.59 s and JOB
read/write p95 of 1.77/1.40 s.

Provider totals are not directly comparable: Docker includes local daemon/host
contention, while E2B includes its secured traffic gateway. Trends must be compared
within the same provider, runner class, profile digest, workload, and concurrency.

## Automation

`.github/workflows/sandbox-function-benchmark.yml` runs Docker nightly and E2B
weekly at concurrency five, supports either provider or both through manual
dispatch, and publishes distinct JSON reports under the tested commit SHA. Each
lane first runs that provider's real workspace/function conformance suite,
including shell, PTY, files, stateful Python, headful browser, lifecycle, and
runner coverage. The protected E2B lane selects the account-free Cloudflare
transport explicitly; local acceptance may select ngrok to distinguish tunnel
instability from executor behavior. The E2B job uses the protected
`backend-protected-e2e` environment and fails when its credential is absent; it
never downgrades a required provider run to a skip.
