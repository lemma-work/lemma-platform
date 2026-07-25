# Function Execution

**Status:** Implemented and verified for Docker and E2B; Kubernetes deferred

## 1. Decision

One public `function_run_id` represents one user-requested execution. Lemma does
not create attempts, fences, tickets, execution units, a separate idempotency key,
or an indeterminate public status.

The backend owns function definitions, authorization, immutable executable
artifacts, public runs, durable JOB submission, deadlines, cancellation, callbacks,
and domain events. AgentBox owns the provider-neutral sandbox and its lifecycle.

Each pod receives at most one active `FUNCTION` sandbox. That sandbox runs a
resident private `lemma-function-runtime` HTTP service. The service keeps immutable
function revisions and Python workers warm so an invocation does not start a new
runtime or re-import the function on every call.

Functions belonging to one pod mutually trust one another. Different pods never
share a sandbox, artifact cache, worker, token, or process.

## 2. Boundaries

| Component | Owns |
| --- | --- |
| Function domain | definitions, grants, schemas, current revision, runs and events |
| Artifact builder | dependency resolution and immutable executable archive |
| API path | synchronous dispatch and terminal response |
| Durable worker | asynchronous JOB/deferred dispatch until runtime acceptance |
| Runtime gateway | claim validation, artifact download, terminal callback |
| AgentBox | allocation, lifecycle, signed port access and provider reconciliation |
| Resident runtime | run deduplication, artifact cache, revision-worker pools |
| Revision worker | one loaded Python revision and isolated per-call SDK context |

AgentBox does not understand functions, JOBs, artifacts, run statuses, tokens, or
callbacks. The backend does not import Docker or E2B SDKs.

## 3. Persistent model

The function execution plane adds only these fields:

```text
functions
  revision_hash nullable

function_runs
  revision_hash nullable
  deadline_at nullable
```

Existing run fields retain the input, output, status, logs, error, timestamps and
optional background `job_id`.

There is no `function_revisions`, `function_execution_requests`,
`function_run_attempts`, ticket, fence, lease, capacity, callback-digest or result
registry table.

The public run state machine is:

```text
PENDING -> RUNNING -> COMPLETED
                   -> FAILED
                   -> CANCELLED

PENDING ----------> FAILED
        ----------> CANCELLED
```

`function_runs` is the authoritative execution record:

- runtime claim atomically changes only `PENDING` to `RUNNING`;
- terminal callback changes only `RUNNING` to a terminal state;
- a terminal callback repeated with the same `function_run_id` is acknowledged as a
  duplicate and does not emit another transition;
- a sandbox recreated after a crash cannot claim a run already marked `RUNNING` or
  terminal.

## 4. Revision identity and activation

`revision_hash` is the sole executable revision identifier. It is the SHA-256 of a
deterministic immutable artifact containing:

- exact source;
- locked prebuilt dependencies;
- entrypoint and Pydantic model names;
- runtime ABI and artifact format;
- builder identity.

Builder and runtime metadata live in the artifact manifest, not in database
columns. Grants and general `updated_at` metadata are not executable identity and
are not included in the revision hash.

The current function row holds the active hash. Creating a run copies that hash
onto the run, so a later function update cannot alter an existing execution.

Activation is content-addressed:

```text
artifacts/<sha256>.zip
revisions/<sha256>/function.py
```

Schema extraction and artifact construction complete outside a database
transaction. Only after the artifact and immutable source exist does a short
transaction update schemas, source path, revision hash and `READY` status. A failed
update may leave unreferenced immutable files for later cleanup, but it cannot
overwrite the source of the still-active revision.

Dependencies are resolved with `uv` before the function becomes `READY`.
Invocation never runs `pip install`, `uv pip install`, or arbitrary package-manager
commands.

## 5. Invocation protocol

AgentBox creates short-lived authenticated access to the private runtime port. The
backend invokes:

```http
POST /functions/{function_id}/runs/{function_run_id}
Authorization: Bearer <delegated function token>
If-Match: "sha256:<revision_hash>"
X-Lemma-Gateway-Url: <trusted runtime gateway URL>
X-Lemma-Run-Token: <run-scoped control capability>
Content-Type: application/json

{"input": {...}}
```

Asynchronous JOB or deferred execution additionally sends:

```http
Prefer: respond-async
```

The authorization bearer is the only credential that can authorize execution.
The run token cannot authorize execution; it is a deterministic backend control
capability that lets cancellation win even before claim returns. The runtime
requires the claim response to contain the same token before it accepts the run.

The runtime forwards the bearer and exact run data to:

```http
POST /internal/function-runtime/runs/{function_run_id}:claim
```

The gateway authenticates the delegated function session and verifies:

- user, pod and function identity;
- deterministic session identity;
- exact revision hash and input;
- `PENDING` run state;
- absolute deadline.

Claim atomically persists `RUNNING` and returns:

- immutable artifact URL and hash;
- deterministic run callback capability;
- exact input and configuration;
- user, pod and function identity;
- delegated SDK token and Lemma base URL;
- absolute deadline.

Dynamic invocation state is installed through Python `ContextVar` bindings. Tokens,
inputs, configuration and identity are not written to process-global environment
variables.

## 6. API execution

1. Authorize and validate the call.
2. Create a `PENDING` run with its revision snapshot and deadline in a short
   transaction.
3. Close the transaction.
4. Ensure `(FUNCTION, pod_id)` through AgentBox.
5. Resolve the cached delegated function token.
6. POST directly to the resident runtime without publishing a worker event.
7. Runtime claims, executes and commits the terminal callback.
8. Runtime returns the terminal report.
9. Backend reads and returns the durable terminal run.

The API request never enters Redis or the backend worker queue.

If the runtime response is lost, the backend first reconciles the durable run. A
terminal callback, or a committed asynchronous `RUNNING` claim, wins. When the
run is still unconfirmed after a transport failure, the backend may retry the
same immutable operation exactly once with the same run ID, revision, input and
session capability. Runtime run-ID deduplication and the atomic
`PENDING -> RUNNING` claim prevent duplicate execution. A second unconfirmed
response is best-effort cancelled and marked failed; HTTP error responses are
never retried.

## 7. JOB and deferred execution

1. Create the `PENDING` run and transactional execution-requested domain event.
2. Return `PENDING` to the caller.
3. The outbox publishes and the durable worker receives `function_run_id`.
4. The worker ensures the pod sandbox and invokes the same runtime endpoint with
   `Prefer: respond-async`.
5. The runtime returns `202 Accepted` only after claim committed
   `PENDING -> RUNNING`.
6. The backend worker ends immediately after authenticated acceptance.
7. The resident runtime continues execution and its terminal callback completes the
   run.

The backend worker does not hold a task, database connection, AgentBox connection,
or polling loop for the duration of a long JOB.

Delivery may be duplicated by the durable queue. Runtime run-ID deduplication and
the atomic `PENDING -> RUNNING` claim still permit only one execution. No delivery
after a committed claim can create a replacement execution.

## 8. Resident runtime and caching

The function profile starts:

```text
lemma-function-runtime serve --host 0.0.0.0 --port 8090
```

The runtime holds:

- a bounded run-ID deduplication registry;
- a content-addressed artifact cache;
- an LRU of revision-worker pools keyed by `(function_id, revision_hash)`;
- a map from active `function_run_id` to its exact worker.

Each Python worker:

- imports one immutable revision once;
- processes one invocation at a time;
- receives a fresh typed request over a private framed pipe;
- binds a fresh SDK `ContextVar`;
- captures bounded stdout and stderr;
- returns a typed terminal result;
- remains warm for another invocation of the same revision.

Concurrent invocations use separate worker processes. The pool grows on demand up
to a high runtime process-safety ceiling. The initial ceiling is 32 live workers
per pod sandbox and is validated by benchmark rather than exposed as user-visible
capacity.

The revision cache bound controls how many distinct revision pools stay warm; it is
not an invocation concurrency limit. The initial name is
`max_cached_revisions`, default 16.

There is no durable sandbox-local queue or result registry. Backend JOB durability
ends at runtime acceptance; after that, the backend run row and terminal callback
are authoritative.

## 9. Tokens and authorization

The backend caches delegated function tokens for five minutes with asynchronous
single-flight, keyed by:

```text
user_id
pod_id
function_id
revision_hash
workload_name
scope
delegated-token mode
```

The cache is in-process and never stores a human refresh token. Multiple backend
replicas may each mint one token. No database transaction remains open during
minting.

The same warm revision worker may serve different users. The delegated token and
identity are per request and live only in the invocation context. They are never
cached as worker globals.

After claim, the runtime receives a callback capability derived as an HMAC of
`function_run_id`. It is not persisted. It authorizes only artifact download,
terminal callback and cancellation for that run. Artifact access requires the run
to remain `RUNNING` and before its deadline. Terminal authentication remains
restart-stable so an already-committed callback can be acknowledged idempotently;
durable run-state conditions prevent it from mutating a cancelled, failed or
completed run.

## 10. Cancellation and deadlines

Cancellation targets `/runs/{function_run_id}:cancel` with the run callback
capability. The runtime cancels the exact task and kills/discards its worker process
group, preventing descendants from surviving cancellation. The backend then marks
the public run `CANCELLED`. A late callback cannot change terminal state.

Every run snapshots an absolute `deadline_at`. Runtime and worker timeouts use that
same deadline.

A periodic database reconciler marks expired `PENDING` or `RUNNING` runs `FAILED`.
This covers backend termination, sandbox death and callback loss without replaying
user code.

## 11. Failure and retry policy

There is no automatic function execution retry.

- AgentBox may wait or repeat an intrinsically idempotent logical `ensure`; AgentBox
  itself guarantees one provider create per allocation token.
- The runtime may retry an identical terminal callback for a short bounded period
  because terminal transition is idempotent.
- The backend never repeats an invocation POST after an ambiguous response.
- The durable worker may be redelivered before claim; the run claim prevents a
  second execution.
- A client retry creates a new `function_run_id` and therefore a new execution.

Functions that need business-level exactly-once effects must use an idempotency key
understood by the external system they mutate.

## 12. Database and I/O rule

No SQLAlchemy session or pooled PostgreSQL connection may remain open during:

- AgentBox calls;
- provider readiness waits;
- delegated-token minting;
- runtime HTTP;
- artifact/object-store I/O;
- organization lookup;
- sleeps or callback waits.

Every orchestration path follows:

```text
short database transaction
-> close
-> external I/O
-> short database transaction
```

Tests instrument unit-of-work lifetime and real PostgreSQL concurrency to enforce
this rule.

## 13. Provider behavior

Docker and E2B use the same invocation protocol and runtime source.

- Docker starts the resident runtime as the function container command.
- E2B starts the same command as the template start command and waits for port 8090.
- Function sandboxes have no persistent volume and no auto-resume contract.
- Artifact and worker caches are opportunistic and disappear with the allocation.
- AgentBox destroys an idle function allocation; correctness never depends on its
  filesystem after destruction.

Kubernetes remains a later adapter. It must run the same function profile and
protocol on an approved sandbox `RuntimeClass`, without PVC, Service, ingress or
service-account token.

## 14. Verification evidence

The implementation is accepted for Docker and E2B only after the same source
passes:

- typed unit and repository state tests;
- no-database-over-external-I/O tests;
- real Docker workspace and function conformance;
- real E2B workspace and function conformance;
- API and JOB execution with the same pod allocation;
- warm revision reuse and revision replacement;
- two-user context isolation;
- compiled dependency execution;
- concurrency one and five through the full stack, plus runtime stress tests at
  higher worker counts;
- cancellation, timeout, duplicate callback, lost response and sandbox death;
- warm no-op and 1,000-row read/write performance gates;
- exact provider-resource cleanup.

The current implementation branch passed this matrix on 2026-07-23. Docker and
E2B each passed real workspace/function conformance and the API/JOB benchmark with
1,000-row reads and writes at concurrency five. The E2B run used a temporary ngrok
tunnel solely to expose the local synthetic backend; it did not change an E2B
template, alias, account setting, or deployed build.
