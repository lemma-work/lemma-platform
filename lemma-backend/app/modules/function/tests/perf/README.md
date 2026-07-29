# Function execution benchmark

This is the canonical repeatable benchmark for the complete Lemma function path.
It uses public Lemma APIs and measures authorization, durable run creation,
AgentBox readiness/lease acquisition, runtime dispatch, delegated SDK data access,
JOB callback persistence, and client-visible completion.

There is one reusable harness:

- `load_tests/function_execution.py`

and one provider-parameterized pytest entrypoint:

- `app/modules/function/tests/perf/test_function_execution_benchmark.py`

The Docker and E2B Make targets run that same entrypoint. Do not add a separate
provider benchmark script; extend the shared cases or fixture instead.

## Cases

Every data case performs exactly **one Lemma SDK datastore request** per function
invocation. `FUNCTION_BENCH_BATCH_ROWS` defaults to 1,000.

| Case | Mode | Rows | SDK operation | Table |
| --- | --- | ---: | --- | --- |
| `api_noop` | API | 0 | none | none |
| `api_read_single` | API | 1 | one `table.list(limit=1)` | RLS-enabled source |
| `api_write_single` | API | 1 | one `records.create` | RLS-enabled sink |
| `api_read_batch` | API | batch size | one `table.list(limit=batch size)` | RLS-enabled source |
| `api_write_batch` | API | batch size | one `records.bulk_create` | RLS-enabled sink |
| `job_read_batch` | JOB | batch size | one `table.list(limit=batch size)` | non-RLS source |
| `job_write_batch` | JOB | batch size | one `records.bulk_create` | non-RLS sink |

Each case has three phases:

1. one cold invocation;
2. a pool-fill phase with `FUNCTION_BENCH_CONCURRENCY` overlapping invocations;
3. the reported steady phase with `FUNCTION_BENCH_INVOCATIONS` invocations.

The summary table and latency budgets use the steady phase. Timing percentiles use
successful invocations only; reliability is reported separately as completed and
failed counts.

## Metrics

- `submit`: caller time until the initial API response. For API functions this is
  also the terminal response; for JOB functions it is queue acceptance.
- `terminal`: caller time until a terminal run is observed.
- `queue`: durable run `created_at` to `started_at`.
- `execution`: durable run `started_at` to `completed_at`.
- `function_call`: time measured inside the function around the single SDK
  datastore request.
- `platform_overhead`: API terminal time minus the in-function datastore call; for
  JOBs, durable queue plus execution time minus the in-function call, excluding
  client polling delay.

Reports include `rows_per_invocation` and `sdk_calls_per_invocation`, so results
remain interpretable if the batch size changes.

## Run on Docker

Docker must be running:

```bash
make benchmark-functions-docker \
  FUNCTION_BENCH_INVOCATIONS=20 \
  FUNCTION_BENCH_CONCURRENCY=5 \
  FUNCTION_BENCH_BATCH_ROWS=1000
```

The fixture builds disposable local images, provisions isolated functions/tables,
and removes its containers and resources on teardown.

## Run on real E2B

Set `E2B_API_KEY` without putting it on the command line. Pin all four immutable
profile identifiers:

```bash
make benchmark-functions-e2b \
  FUNCTION_BENCH_INVOCATIONS=20 \
  FUNCTION_BENCH_CONCURRENCY=5 \
  FUNCTION_BENCH_BATCH_ROWS=1000 \
  FUNCTION_BENCH_TUNNEL=ngrok \
  AGENTBOX_E2B_WORKSPACE_TEMPLATE=<template-id> \
  AGENTBOX_E2B_WORKSPACE_BUILD_ID=<build-id> \
  AGENTBOX_E2B_FUNCTION_TEMPLATE=<template-id> \
  AGENTBOX_E2B_FUNCTION_BUILD_ID=<build-id>
```

The report records the provider, configuration, resource names, cold/pool-fill
samples, every steady sample, case summaries, sink-row verification, and errors.
By default reports are written under:

```text
.benchmark-results/function-execution/<provider>/<timestamp>.json
```

The report directory is intentionally untracked. Copy the relevant summary and
immutable E2B IDs into the pull request that changes function execution.
