# AgentBox Verification and Rollout

**Status:** Docker and E2B implementation verified; Kubernetes rollout deferred

**Parent:** [AgentBox](README.md)

## 1. Purpose

AgentBox is accepted by behavior, failure semantics, and measured latency—not by
adapter method coverage alone. The executable suite and Docker/E2B case catalog live
in [Testing strategy](testing-strategy.md). This document defines acceptance gates,
security/performance evidence, breaking migration, rollback boundaries, and provider
enablement.

The initial implementation supports exactly two tested providers:

- Docker for local development and continuous conformance;
- E2B for managed production.

Kubernetes remains fully specified but is deferred until its later disposable-cluster
and strong-runtime suite exists. No adapter becomes eligible until its exact profile
digest and protocol version pass the complete required suite.

## 2. Test architecture

### 2.1 Layers

| Layer | Purpose | External resources |
| --- | --- | --- |
| Domain unit | State transitions, deadlines, retry decisions, capacity math | None |
| Port contract | Every lifecycle/execution/files/session semantic | Fake adapter/runtime |
| Fault adapter | Deterministic unknown, 429, disconnect, delayed state | None |
| Runtime contract | Workspace and function runtime protocol | Local subprocess/container |
| Provider conformance | Real adapter against real provider | Docker/E2B initially |
| Backend integration | Workspace tools and function dispatcher through AgentBox | PostgreSQL + AgentBox |
| End-to-end | Agent/workflow/API/JOB behaviors | Full test stack |
| Security | Isolation, credential, network, resource abuse | Provider-specific environment |
| Performance | Warm/resume/cold latency and capacity behavior | Production-shaped environment |
| Chaos | Crashes, lost responses/callbacks, partitions, duplicate events | Fault injection + real provider |

### 2.2 Common fixtures

Every provider suite consumes the same profile release manifest and produces the
same result format. Fixtures create unique workload/logical/allocation IDs and
record exact provider resources at creation time. Cleanup uses exact IDs in `finally`
and a provider-scoped metadata sweep as a last-resort leak detector.

Real-provider tests must never rely on broad name deletion. A failed cleanup emits
the exact resource IDs and blocks production conformance publication.

### 2.3 Deterministic fault adapter

Implement an in-memory/provider-simulator adapter capable of injecting a failure at
every protocol boundary:

```text
create rejected before acceptance
create accepted but response lost
object visible only after delay
duplicate objects for one allocation token
429 with numeric/date Retry-After
connect/read temporarily unavailable
process rejected before start
process started but acknowledgment lost
process tag visible only after delay
stream disconnect after arbitrary sequence
release/delete accepted but response lost
duplicated, reordered, and delayed lifecycle events
```

The adapter records every provider call so tests assert the number and identity of
create/start/release/destroy operations.

## 3. AgentBox domain and state tests

### 3.1 Logical identity

- `(WORKSPACE, id)` and `(FUNCTION, id)` may coexist without state/metadata collision.
- Provider IDs never appear in public API responses.
- Profile mismatch causes drain/replacement, not in-place mutation.
- Allocation epoch increments exactly when a different physical allocation becomes
  current.
- A stale session/process cannot attach to a newer epoch.

### 3.2 Create-at-most-once

- Concurrent ensure calls singleflight on one allocation token.
- Manager restart between allocation commit and provider call dispatches at most
  once for that token.
- Lost create response leaves the allocation `UNKNOWN` and does not call create
  again.
- Matching allocation-token event/list observation binds the exact provider ID.
- Multiple provider objects with one token quarantine extras rather than selecting
  nondeterministically.
- List omission never clears a durable provider binding.

Acceptance invariant:

```text
provider_create_calls(allocation_token) <= 1
```

### 3.3 Process operation deduplication

- Same operation ID and request hash returns the same process.
- Same operation ID with any different non-secret field is `OPERATION_CONFLICT`.
- Environment/stdin values are absent from durable rows and logs.
- Lost start acknowledgment becomes `UNKNOWN_DISPATCH` and is resolved by the same
  provider tag/process; no second start occurs.
- Stale-epoch input/resize/terminate is rejected.
- Output reconnect resumes from sequence and reports truncation gaps.

### 3.4 Deadlines and errors

- Expired request performs zero provider operations.
- Every nested call receives a timeout no greater than remaining deadline.
- No component resets a deadline after a retry/reconnect.
- Typed provider errors map to one documented error and retry disposition.
- A caller cannot turn `WAIT` or `DO_NOT_RETRY` into an internal blind replay.
- `Retry-After` blocks every manager replica for the provider scope.

### 3.5 Distributed admission

- PostgreSQL transactions prevent concurrent over-reservation.
- Interactive/latency reserved capacity cannot be consumed by batch requests.
- Expired un-dispatched reservations return capacity.
- Ambiguous creates retain capacity until resolved/destroyed.
- Reconciliation repairs intentionally corrupted counters from durable allocation
  rows.
- A provider 429 opens one scope circuit and prevents a retry storm.

### 3.6 SQLAlchemy state and migrations

- One SQLAlchemy repository/unit-of-work contract passes against SQLite and real
  PostgreSQL.
- PostgreSQL concurrency proves row locking, skip-locked worker claims, conditional
  fencing, and capacity invariants across manager replicas.
- Alembic fresh-install and last-shipped-schema upgrade paths both reach the exact
  expected head and pass repository smoke tests.
- Lifecycle/services/providers contain no raw SQL or direct engine/session access.
- Tests create state through repositories/test builders and never mutate a private
  connection owned by AgentBox.

## 4. Portable workspace conformance

Run the following unchanged against Docker and E2B for the initial release. The same
case IDs become mandatory for Kubernetes before that adapter is enabled.

### 4.1 Sessions and Python

- Create multiple sessions with distinct cwd/environment.
- Variables, imports, functions, and working directory persist across Python calls
  in the same active session.
- Sessions execute concurrently; calls in one session serialize.
- Restart clears only that session's Python state.
- Execution timeout terminates/resets only the timed-out session.
- Session deletion terminates descendants and removes dynamic credential state.
- Environment is restored after each call and cannot leak into another session.

### 4.2 Commands and PTY

- Foreground success, nonzero exit, timeout, stdout/stderr separation.
- Yield after configured duration returns a process reference.
- Background process remains running after caller disconnect.
- Reconnect receives output after a sequence cursor.
- stdin writes and EOF work.
- Process listing is scoped to current sandbox/epoch and reports command/cwd/TTY.
- Termination kills child and descendant process group.
- PTY supports create, binary input, output stream, resize, disconnect, reconnect,
  normal exit, and forced termination.
- Output limits and truncation metadata work under high-volume output.

### 4.3 Filesystem

- Stat/list/read/write/move/delete text and binary files.
- Range read and streamed large write without whole-body base64 conversion.
- Atomic write leaves original or complete new file after injected interruption.
- Expected-digest conflict prevents lost update.
- Path traversal and symlink escape outside allowed root fail closed.
- Concurrent write/read behavior is documented and deterministic.
- File metadata and content survive workspace release/resume.

### 4.4 Workspace lifecycle

- Five minutes of configured inactivity triggers quiescence/release.
- Active foreground operation prevents release until terminal or release deadline.
- Release terminates sessions/processes and revokes port grants.
- Resume preserves `/workspace` but callers recreate nonportable session state.
- Repeated release/resume does not create duplicate resources.
- Docker-volume profile replacement preserves files while changing allocation epoch.
- E2B-native profile replacement fences and removes the old exact sandbox, then
  publishes a fresh storage generation.
- Activity before the configured total-inactivity deadline resumes the exact paused
  workspace.
- Retention expiry removes compute and storage but the next operation recreates
  the logical workspace with a fresh disk.
- Explicit delete prevents a late event/inventory item from resurrecting state.

### 4.5 Port access

- HTTP and WebSocket grants route only to the requested current-epoch port.
- Expired, revoked, wrong-audience, and stale-epoch grants fail.
- Raw provider credentials/addresses are absent from user responses.
- A workspace release invalidates outstanding grants.
- Function sandbox accepts only the profile-declared resident-runtime port and
  backend audience; every other port/audience is `UNSUPPORTED_CAPABILITY`.

## 5. Provider-specific conformance

### 5.1 Docker

- Tests use the asynchronous Engine API path; the `docker` CLI may be absent.
- Workspace volume survives container stop/start and container replacement.
- Permanent delete removes exact container and volume.
- Random loopback port mappings are reachable only through signed AgentBox proxy.
- Function container has no named volume or published port and is removed after idle.
- Manager socket/credentials are absent inside both profile containers.
- Run under documented CPU/memory/PID/output limits.

Docker conformance runs on every protected CI execution. It is not sufficient
evidence for hostile production isolation.

### 5.2 Kubernetes

This is a deferred provider-enablement program, not an initial release gate. When the
Kubernetes implementation milestone begins, the following becomes mandatory before
any Kubernetes profile is enabled.

Disposable-cluster suite:

- dynamic StorageClass provisions one PVC per workspace;
- deleting/replacing the Pod preserves the bound PVC and files;
- permanent delete removes exact Pod/PVC;
- watches handle resource-version expiration without creating again;
- `409 AlreadyExists` validates the exact allocation token;
- workspace runtime is inaccessible without AgentBox runtime credential;
- function Pod has no PVC, Service, Ingress, or service-account token;
- function Pod deletion loses all cache and a later cold run still succeeds;
- NetworkPolicy denies cross-sandbox and private/control destinations.

Run core Kubernetes conformance against a reproducible kind/k3d/minikube cluster
with dynamic PVC support. Run production isolation/security cases separately on a
cluster with the selected gVisor/Kata `RuntimeClass` and dedicated sandbox node
pool. Until both suites pass, Kubernetes is documentation-only and cannot be
selected in production configuration.

### 5.3 E2B

- Workspace template static services are ready immediately after create without
  post-create runtime bootstrap.
- Full-memory pause plus auto-resume restores native command/filesystem access.
- AgentBox quiescence removed contexts/processes/dynamic credentials before pause.
- File operations and commands auto-resume without a preceding list/status call.
- Command, PID/tag reconnect, stdin, list, kill, PTY, and code contexts satisfy the
  portable suite.
- Secured app grant works and raw traffic token does not escape AgentBox.
- Lost create response resolves through allocation metadata/webhook with one create.
- Duplicate lifecycle deliveries are idempotent.
- Function template exposes no unauthenticated public traffic. Its resident
  function HTTP service is reachable only through E2B's secured TLS gateway and an
  AgentBox signed port grant.
- Function sandbox is killed, not paused, after five idle minutes.
- Long JOB sets one timeout past deadline and completes without heartbeat.
- Provider 429 honors retry-after and does not cause adapter-local create retries.
- Cleanup exact-kills every billable test sandbox, including paused workspaces.

The E2B suite is credential-gated and records project plan/quota, template build IDs,
and incurred resource identifiers. It is blocking on trusted protected merge/release
CI for the exact candidate SHA and on a scheduled production-canary cadence. In that
required lane, missing credentials/template/quota are infrastructure failure rather
than a skipped pass.

## 6. Function execution tests

Run the same full backend function suite through AgentBox Docker and E2B profiles.
It becomes a Kubernetes gate only when that deferred adapter is enabled.

### 6.1 Revision and artifact

- Source/dependency/runtime/builder changes produce different revision/artifact
  digests.
- A function cannot become ready before artifact and schema completion.
- Build failure leaves prior active revision unchanged.
- Manifest/file tampering fails before user import.
- Unsupported ABI/wheel fails activation, not invocation.
- Runtime never calls pip/package index during invocation.
- Warm cache hit and cold cache miss return identical output/schema/error behavior.

### 6.2 API and JOB behavior

- API returns terminal result synchronously within deadline.
- JOB returns pending and completes through callback/outbox.
- API commits its run and dispatches directly; it never enters the worker queue.
- JOB uses the durable backend queue. Both modes use the same atomic backend run
  start, protocol-v2 runtime endpoint, and per-pod sandbox.
- Five API invocations overlap through isolated warm revision workers without a
  four-slot or execution-unit admission model. Higher worker counts are exercised
  by runtime stress tests rather than treated as a public admission promise.
- Concurrent JOBs use the same bounded resident worker pool. The bound is a private
  process-safety guard, not a public capacity promise.
- Under JOB saturation, API dispatch begins without waiting for the backend JOB
  worker because the paths are independent.
- Queue time counts against run deadline.
- A backend restart before JOB start permits queue redelivery. After start, a
  redelivery observes `RUNNING`; the same attempt is not re-created. A single
  ambiguous HTTP response may be retried through the exact same AgentBox grant and
  is deduplicated by `function_run_id`.

### 6.3 Identity and permissions

- One delegated function-session bearer is cached for five minutes by
  user/pod/function/revision/workload/scope inputs and used for both invocation
  authentication, exact-revision artifact download, the SDK context, and JOB
  terminal reporting. Grants remain live backend authorization data.
- The bearer is function and revision scoped. It cannot select another function,
  another revision, or a workspace path; the backend alone constructs and starts
  durable runs before invoking the exact pod sandbox.
- API terminal data returns on the invocation response. JOB terminal callback
  replay can only acknowledge the already-durable terminal state.
- Sandbox argv/env/files contain no user/provider/cloud/object-store credential.
- The delegated function session is limited to pod, function, revision, principal,
  scope, and live grants. Exact run identity is carried separately in the
  backend-constructed envelope and callback path.
- Function-principal grants and invoking-user audit/RLS attribution remain correct.
- Replayed/stolen/expired/revoked delegated sessions fail closed at backend
  artifact, SDK, and callback boundaries.
- Cross-pod artifact/input/result/callback access is denied.

### 6.4 Results and events

- Input/output schemas validate exact active revision.
- Duplicate JOB terminal callbacks are idempotent.
- A late callback cannot update an already-terminal run.
- Terminal transaction emits one completion outbox event.
- Missing outbox event is repaired without repeating execution.
- Agent tools, workflows, schedules, and direct API observe consistent terminal data.

### 6.5 Timeout and cancellation

- API 120-second and JOB 600-second defaults flow as absolute deadlines.
- Runtime kills child and grandchildren at timeout.
- Cancel before backend run start makes the run terminal and starts no code.
- Cancel after start targets the exact run's revision-worker process group.
- Failed termination reports `termination_confirmed=false` and remains reconciled.
- Late success after cancel/timeout cannot alter public terminal state.

### 6.6 Unknown and replay

Fault inject at every boundary:

- provider create accepted, response lost;
- invocation accepted, response lost;
- external side effect completed, JOB terminal callback lost;
- sandbox disappears during artifact download or execution;
- backend/gateway database connection fails during callback.

Assertions:

- one provider create per allocation token and at most one user-code execution per
  `function_run_id`;
- the only automatic invocation retry is one exact-grant retry after an ambiguous
  HTTP outcome, deduplicated by the runtime's `function_run_id` registry;
- direct API response, JOB callback, or deadline reconciliation makes the same run
  terminal;
- a client retry creates a new run and may repeat external side effects, just as a
  retry after an ordinary mid-function failure can;
- zero duplicate side effects caused by platform replay of one run.

## 7. Security testing

Execute malicious workspace/function fixtures that attempt:

- `/proc` environment, memory, FD, and sibling-process inspection;
- path traversal, symlink race, device/special file access;
- Docker/containerd/Kubernetes/provider API access;
- cloud metadata and private/link-local network access;
- DNS rebinding and redirect to denied destinations;
- direct-internet and proxy-bypass attempts, arbitrary HTTPS names, non-443
  destinations, and expired/wrong-run capabilities;
- cross-workspace and cross-pod filesystem/process/network access;
- fork, PID, CPU, memory, disk, log, output, and decompression bombs;
- terminal ANSI/OSC/control-sequence injection;
- native-wheel and import-time malicious code;
- runtime-control API access from user code;
- delegated-session misuse, cross-function/revision replay, and JOB callback
  forgery.

Launch requires:

- Docker explicitly rejected in hostile production configuration;
- Kubernetes tests passing on the exact production RuntimeClass/profile before any
  later Kubernetes enablement;
- E2B tests passing on the exact template build/account network policy;
- no reusable credential present in function sandbox snapshots, environment, argv,
  files, logs, crashes, or captured diagnostics.

## 8. Performance methodology and gates

Measure and report p50/p95/p99 for each provider/profile:

```text
admission wait
allocation create or workspace resume
provider readiness
resident invocation acknowledgment
Python context creation/execution
first output
backend run start
artifact fetch/verify/cache
revision worker acquisition
user duration
terminal persistence (direct API response or JOB callback)
end-to-end API/JOB completion
```

Test dimensions:

- warm, resumed/recreated workspace, and cold allocation;
- function empty cache and warm cache;
- no dependencies, small pure-Python dependencies, and native wheels;
- user duration 0 ms, 100 ms, 1 s, 30 s, maximum;
- input/output 1 KiB through configured large-object threshold;
- full-stack concurrency one and five, plus higher-count runtime stress;
- pod counts 1, 10, and production quota-sized;
- API/JOB mixes including JOB saturation;
- create burst at/above provider rate limit.

Initial launch gates:

| Measure | Gate |
| --- | ---: |
| Warm no-op function platform overhead p95 | ≤ 2 s |
| Cold no-dependency function p95 | ≤ 8 s |
| E2B workspace resume p95 | ≤ 2 s |
| Warm workspace command control overhead p95 | ≤ 500 ms, excluding command |
| API start under JOB saturation | ≤ 2 s |
| Provider creates per allocation token | ≤ 1 |
| Duplicate side effects caused by platform replay | 0 |
| Platform-caused eligible run success | ≥ 99.9% monthly canary |

Seven consecutive development days and a production canary window must pass before
default enablement. Performance claims always identify provider, profile digest,
region/cluster, account quota, and sample size.

## 9. Observability and operator acceptance

Dashboards must distinguish workspace/function and warm/resume/cold paths. Required
views:

- provider active/reserved capacity and create tokens;
- provider 429 rate and scope blocked time;
- allocation states, age, idle duration, and profile digest;
- ambiguous create/unknown process backlog and oldest age;
- workspace release/resume/delete latency and retained storage;
- function JOB queue age, API latency, resident worker use, and allocation reuse;
- function deadline, cancel, artifact, invocation-response, and callback failures;
- cost/runtime by provider, workload, pod/user, and profile.

Required alerts:

- unknown create/process older than reconciliation SLO;
- provider scope blocked beyond retry-after tolerance;
- capacity counter mismatch;
- function API queue age or warm overhead above gate;
- retained workspace past deletion deadline;
- function allocation idle beyond cleanup grace;
- exact cleanup failure or orphan growth;
- late callback mutation or suspected duplicate invocation;
- credential/network security test regression.

Operator tools must inspect logical sandbox, physical allocations, process intents,
function run/queue state, and provider event history by public correlation ID
without revealing secrets.

## 10. Breaking migration

The AgentBox API is deployed atomically with the generated client and all backend
callers. There is no live compatibility facade for the experimental API.

### Phase 0: design and baselines

- Approve this complete design set.
- Capture current Docker/E2B workspace and API/JOB latency/failure baselines.
- Inventory current production workspace data assumptions and function dependency/
  egress behavior.
- Confirm E2B quota/template/network policy and Docker/E2B provider capacity
  reservations.

### Phase 1: protocol and state foundation

- Implement typed models and errors, SQLAlchemy 2.x repositories/unit of work, Alembic
  schema, lifecycle operations, operation wait/notification, distributed admission,
  and deterministic fault adapter.
- Generate the breaking AgentBox client.
- Implement all hermetic domain/port tests before real providers.

### Phase 2: workspace runtime and Docker

- Build the private workspace runtime and workspace/function images.
- Implement Docker Engine adapter and real conformance.
- Migrate backend workspace tools/file manager/browser/app builder to the canonical API in an
  isolated integration branch/environment.

### Phase 3: backend function plane on Docker

- Add immutable revision/build pipeline, direct API and durable JOB run/session
  state, runtime gateway, slim runtime, callbacks, and
  reconciliation.
- Pass full-stack Docker API/JOB, priority, cancellation, ambiguity, and cleanup
  cases before adding another provider.

### Phase 4: E2B

- Build separate workspace/function templates.
- Implement native lifecycle, command/PTY/files/code-context/process-tag access,
  webhook reconciliation, and distributed quota behavior.
- Pass credential-gated workspace/browser and full backend API/JOB conformance,
  chaos, cleanup, and latency gates.

### Phase 5: mandatory suite hardening

- Complete the case manifest in [Testing strategy](testing-strategy.md) for Docker
  and E2B, including PostgreSQL, security, performance, and soak lanes.
- Switch API/JOB/agent/workflow/schedule paths from workspace function execution to
  the new per-pod function plane.
- Verify that the retired in-sandbox HTTP execution server, its port, and its
  result registry are absent after the new end-to-end tests pass.

### Phase 6: atomic development cutover

- Drain experimental AgentBox sessions/jobs.
- Delete and recreate the AgentBox-owned database/schema; no experimental rows are
  transformed, copied, or adopted.
- Remove experimental Docker containers/volumes and E2B sandboxes by exact managed
  metadata. Keep a machine-readable deletion ledger and verify every exact ID is
  absent.
- Deploy AgentBox, generated client, backend workspace callers, function
  dispatcher/gateway, and profile release manifest as one coordinated release.
- Create fresh workspace storage on first use. Existing workspace files and live
  Python/process state are intentionally not migrated.
- Rollback is allowed only before a replacement-plane function run is claimed or
  after all replacement-plane runs are terminal. No claimed run falls back to the
  experimental executor.

### Phase 7: production canary and default

- Start with internal/test users and synthetic/read-only functions.
- Expand by explicit user/pod allowlist and bounded provider/spend capacity.
- Never shadow-execute side-effecting functions.
- Hold each expansion until errors, unknowns, latency, security, cleanup, and cost
  remain within gates.
- After seven stable days, remove the experimental runtime/function code.

### Phase 8: later Kubernetes provider

- Implement async watches, per-workspace PVC, workspace Pod, stateless function Pod,
  exec launcher, NetworkPolicy, and exact cleanup from the existing adapter design.
- Add disposable kind/k3d/minikube conformance plus a separate production
  gVisor/Kata cluster security lane.
- Run the complete shared workspace/browser/API/JOB catalog; enable Kubernetes only
  after independent provider verification.

## 11. Rollback rules

- Workspace rollback does not restore experimental workspace data. Any rollback
  creates another fresh workspace after replacement operations are drained.
- A function run never switches execution systems after its operation is claimed or
  claim is uncertain.
- Unclaimed queued runs may be drained and resubmitted only through an explicit
  migration transaction preserving run identity and deadline.
- The initial database operation is a destructive AgentBox-owned schema reset. Later
  changes use normal reviewed Alembic migrations.
- Provider templates/images remain pinned and available for the full rollback and
  forensic-retention window.

## 12. Documentation and release evidence

Before implementation starts, this design set must have:

- no references to the removed v1 architecture/function draft;
- valid relative links and renderable Mermaid diagrams;
- consistent names, states, timeouts, retention, capacity, and security invariants;
- citations to current official E2B, Kubernetes, and Docker behavior;
- explicit unresolved product decisions, if any, rather than implicit defaults.

Every implementation release publishes:

- release/profile/template/image digests;
- schema and protocol versions;
- conformance test run IDs;
- performance report;
- security report and approved exceptions;
- provider quotas/capacity configuration;
- migration and rollback checklist;
- exact known deviations from this design.

The initial implementation is complete only when the documentation describes
observed shipped behavior and every mandatory acceptance case passes against Docker
and E2B. Kubernetes is complete only after its later independent verification program
passes the same portable catalog plus Kubernetes-specific gates.
