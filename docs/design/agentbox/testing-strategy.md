# AgentBox Testing Strategy

**Status:** Implementation specification; Docker/E2B release verification in progress

**Parent:** [AgentBox](README.md)

**Acceptance and rollout:** [Verification and rollout](verification-and-rollout.md)

## 1. Purpose and release boundary

AgentBox is execution infrastructure, so a provider adapter is not accepted because
its mocked methods pass. The initial AgentBox release is accepted only when the
same observable workspace and function journeys pass against real Docker and real
E2B using the exact images/templates, generated client, backend dispatcher, runtime
gateway, and database schema intended for deployment.

The initial mandatory provider matrix is:

| Provider | Workspace Python/shell/files | Workspace browser/apps | API functions | JOB functions | Release status |
| --- | ---: | ---: | ---: | ---: | --- |
| Docker | Required | Required | Required | Required | Passed on current PR head |
| E2B | Required | Required | Required | Required | Blocking on trusted merge/release CI |
| Kubernetes | Designed, not initially required | Designed, not initially required | Designed, not initially required | Designed, not initially required | Deferred until dedicated cluster program |

A Docker-only pass cannot promote an E2B profile. An AgentBox-only process test
cannot promote the backend function plane. An E2B workspace pass cannot stand in for
an E2B function pass. All four required columns must have evidence for the exact
release SHA and profile digests.

This document owns suite topology, harness behavior, case catalog, and CI execution.
[Verification and rollout](verification-and-rollout.md) owns performance thresholds,
security launch requirements, migration, and promotion decisions.

### 1.1 Current implementation evidence

As of 2026-07-23, the following has been executed from the implementation branch:

| Suite | Result | What it proves |
| --- | ---: | --- |
| AgentBox unit/contract suite | 93 passed, 7 provider-gated skips | Typed SQLAlchemy state, API, adapters, runtimes, streaming files, fenced callback recovery, fault paths, exact cleanup reconciliation |
| Real Docker adapter suite | 5 passed | Workspace lifecycle/volume, shell, PTY, Python, files, browser/port access, private-network manager topology, function process, lost terminal response recovery, and duplicate-safe callback |
| Real E2B adapter suite | 2 passed against fresh candidate builds | Immutable create, Node/pnpm/uv/LiteParse, shell/stdin, PTY/resize, native files, Code Interpreter state, headful browser, pause/auto-resume, function process/cancel, exact deletion |
| Backend API/JOB benchmark | Passed on Docker and the prior E2B release | Ticket claim, immutable artifact verification, real runner/gateway/database execution, 1,000-row table reads/writes at concurrency five |
| Backend durability/chaos contracts | PostgreSQL lease suite 2 passed; focused dispatcher/gateway suite 3 passed | One-attempt restart takeover, lost/duplicate callback recovery, stale-fence rejection, sandbox-death termination, unknown-outcome fencing, no replay, and no DB lease across external I/O |
| Current-head repository CI | Passed | Linux AgentBox/PostgreSQL and real Docker conformance, backend unit/migrations/quality, install experience, frontend/SDKs, macOS/Windows packaging, dependency/image scans, Gitleaks, CodeQL, and language analyzers |

This is development evidence, not final release acceptance. The fresh E2B candidate
builds still require the complete API/JOB benchmark before promotion. Remaining
case-manifest, chaos, and performance gates stay blocking. Kubernetes remains
deferred.

## 2. Testing principles

1. **Behavior is provider-neutral.** Portable cases have one case ID and assertion
   set parameterized over Docker and E2B. Provider-only cases are additive.
2. **The real public boundary matters.** Provider conformance enters through the
   canonical AgentBox API/client. Full-stack cases enter through Lemma workspace tools or
   public function-run APIs. Tests do not invoke a provider adapter as a shortcut.
3. **Functions are tested where API/JOB semantics live.** The backend dispatcher,
   queue, attempts, tickets, runtime gateway, callbacks, and outbox are part of every
   full-stack function test. AgentBox remains a generic sandbox/process service.
4. **No in-sandbox function server is accepted.** Port 8080/8090 health or an
   AgentBox-local result registry is never evidence for function correctness.
5. **No test-body retries.** Mandatory correctness/conformance cases run once. A
   failed assertion remains a failure; rerunning the whole CI job is not an
   automatic flake policy.
6. **Deterministic fixtures before public internet.** Shell, Python, browser, file,
   callback, and egress fixtures are owned by the test stack and content-addressed.
7. **Failures preserve evidence and still clean up.** Cleanup occurs in `finally`
   from an exact resource ledger. Diagnostics are redacted and attached before
   cleanup.
8. **Unknown outcomes are asserted, not hidden.** Fault injection must prove call
   counts, fences, terminal state, and absence of duplicated side effects.
9. **Performance is distributional.** Correctness tests avoid fragile single-sample
   wall-clock assertions. Dedicated benchmark lanes enforce p95/p99 gates.
10. **A skip is not a pass.** In required Docker/E2B CI lanes, missing daemon,
    credentials, template, quota, or release manifest fails infrastructure setup.

## 3. Suite ownership and topology

### 3.1 AgentBox repository suites

Proposed structure:

```text
agentbox/tests/
  unit/                    domain policies, errors, deadlines, admission
  state/                   SQLAlchemy repositories, UoW, Alembic, concurrency
  runtime/                 private workspace runtime and function launcher contract
  contract/                portable lifecycle/process/Python/files/ports cases
  providers/docker/        real Docker-only behavior and cleanup
  providers/e2b/           real E2B-only behavior and cleanup
  chaos/                   fault-boundary and manager-restart cases
  performance/             provider benchmarks and soak scenarios
  support/                 manifests, harnesses, resource ledger, fixtures
```

AgentBox owns proof that the canonical API lifecycle and generic ports behave identically. It
does not manufacture API/JOB domain semantics inside these tests.

### 3.2 Backend full-stack suites

Proposed structure:

```text
lemma-backend/app/modules/workspace/tests/agentbox/
  contract/                backend workspace adapter/client behavior
  full_stack/              tools, browser, files through real AgentBox

lemma-backend/app/modules/function/tests/agentbox/
  domain/                  queue, attempts, capacity, tickets, fences
  full_stack/              API and JOB through real AgentBox/provider
  chaos/                   callback loss, restarts, ambiguous execution
```

One CI orchestration job starts PostgreSQL, Redis/worker dependencies, backend API,
runtime gateway, AgentBox, and the selected provider harness. It builds/loads a
ready immutable function revision through the real build path, then calls the public
backend endpoint. Fake AgentBox remains valuable for backend unit tests but cannot
satisfy a provider release gate.

### 3.3 Markers and selection

Every case declares orthogonal markers:

```text
unit | state_contract | runtime_contract | provider_contract | full_stack
provider_docker | provider_e2b | provider_kubernetes
workspace | function_api | function_job
chaos | security | performance | soak
```

Provider parameterization is centralized in the harness. Individual portable test
bodies never branch on provider name. A provider deviation must be represented as a
documented capability decision, not `if provider == ...` inside assertions.

## 4. Shared harness design

### 4.1 Release manifest fixture

Every real-provider run consumes one immutable manifest containing:

```text
git SHA
AgentBox protocol/client versions
database/Alembic revision
workspace runtime protocol version
function runtime ABI and builder digest
Docker workspace/function image digests
E2B workspace/function template build IDs
test fixture bundle digest
E2B SDK version and provider project/region class
```

The harness refuses mutable Docker tags, unspecified E2B template aliases, or a
generated client from another protocol revision. The manifest is copied into the
test report.

### 4.2 Provider harness port

Test infrastructure implements a provider-harness interface independent of the
production adapter:

```python
class ProviderTestHarness(Protocol):
    async def preflight(self, manifest: ReleaseManifest) -> PreflightReport: ...
    async def start_stack(self, manifest: ReleaseManifest) -> RunningStack: ...
    async def list_scoped_resources(self, run_scope: str) -> list[ResourceRef]: ...
    async def force_fault(self, fault: FaultSpec) -> FaultHandle: ...
    async def collect_diagnostics(self, resource: ResourceRef) -> DiagnosticBundle: ...
    async def cleanup_exact(self, resources: list[ResourceRef]) -> CleanupReport: ...
```

This harness prepares and observes tests; it does not replace calls through the
AgentBox client. Its resource ledger records provider ID as soon as observable and
also records logical key, allocation token, operation ID, profile digest, and
creation timestamp.

### 4.3 Docker harness

- Require a reachable Docker Engine and supported API version.
- Build exact workspace/function images from the checkout and resolve their content
  digests before tests.
- Start AgentBox with PostgreSQL for protected conformance. SQLite is used only by
  the separate local-state lane.
- Attach function containers only to the isolated test data-plane network where the
  runtime and egress gateways are reachable by service identity; the host's
  loopback address is never assumed to work from a sandbox.
- Never mount the Docker socket inside either sandbox profile.
- Use the Engine API for assertions/cleanup; a CLI may prepare the CI daemon but is
  not part of production adapter behavior.
- Capture container inspect/events/log tails, volume identity, resource limits, and
  absence of published function ports.
- Delete only ledgered container/volume IDs and fail the lane if anything remains.

### 4.4 E2B harness

- Use a dedicated CI project with bounded concurrency/spend and least-privilege
  secret access.
- Preflight exact template build IDs, SDK version, quota, secured access, lifecycle,
  and network policy before creating the first test sandbox.
- Run the runtime gateway, controlled egress gateway, and artifact fixture behind
  ephemeral publicly reachable TLS test endpoints because an E2B sandbox cannot
  call CI localhost. Endpoints are uniquely namespaced to the run, accept only
  attempt-bound capabilities, expose no AgentBox management API, and are removed
  after the suite.
- Add unique `managed-by`, environment, run-scope, allocation-token, and case-ID
  metadata to every sandbox.
- Record the exact sandbox ID from create response, lifecycle event, or reconciliation
  result; never infer identity from a name.
- Kill every exact running or paused sandbox in `finally`, then perform one
  run-scope metadata sweep. A nonempty sweep is a failed test run.
- Redact API/access/traffic tokens from pytest representations, logs, exceptions,
  HTTP captures, and uploaded artifacts.
- Configure the E2B function network allowlist to those exact gateway endpoints and
  prove a direct connection to the backend database, AgentBox manager, metadata, or
  arbitrary internet fails.
- In the required CI lane, unavailable credentials/quota/templates are an
  infrastructure failure. Local developer runs may explicitly deselect E2B tests.

### 4.5 Full-stack fixture bundle

The content-addressed bundle contains:

- deterministic HTML with links, inputs, buttons, downloadable content, and DOM
  mutation for browser assertions;
- a local HTTP server and WebSocket echo server started through the process port;
- Python snippets for state, exceptions, concurrent execution, timeout, and process
  descendants;
- binary and large-file fixtures with expected digests;
- prebuilt no-op, echo, datastore/file, controlled-egress, timeout-tree,
  non-idempotent-side-effect, and deterministic-failure function artifacts;
- gateway endpoints that count ticket claims, starts, logs, callbacks, outbox
  events, and externally visible test side effects.

No invocation performs `pip install`. A dependency-bearing fixture is built before
its revision becomes `READY`, and its sandbox is denied package-index access during
execution.

## 5. State and lifecycle test foundation

### 5.1 SQLAlchemy repository contract

Run the same repository/unit-of-work contract against:

- temporary `sqlite+aiosqlite` for fast local semantics; and
- real PostgreSQL with async psycopg for production semantics.

The contract covers every model, relationship, uniqueness constraint, enum/check,
timestamp, serialization boundary, transaction rollback, compare-and-set, and
repository mapper. Tests interact through repositories or a documented test-state
builder; they never access `_conn`, execute ad-hoc SQL against private tables, or
mock an `AsyncSession` call chain.

PostgreSQL-only concurrency cases prove:

- logical-key row locking and concurrent ensure singleflight;
- admission cannot over-reserve across manager replicas;
- `SKIP LOCKED` cleanup/reconciliation claims do not duplicate work;
- allocation/process unique keys resolve races deterministically;
- stale epoch/fence compare-and-set updates affect zero rows;
- deadlock/serialization failures map to bounded typed retry at the transaction
  boundary, never to provider create/start replay.

### 5.2 Migration tests

Alembic CI creates a database from empty and upgrades it to head, then separately
restores the last shipped AgentBox schema fixture and upgrades it to head. Both paths
run repository smoke tests and downgrade is not required. CI also verifies:

- one linear head unless an explicitly reviewed merge revision exists;
- production startup rejects a behind/ahead schema;
- migrations contain no durable secret values;
- large-table changes have an online/backfill plan before production rollout;
- SQLite local schema and PostgreSQL schema expose the same domain-required fields.

### 5.3 Deterministic state-machine tests

Model/property tests generate valid and invalid lifecycle sequences over ensure,
release, resume, profile replacement, destroy, callbacks, manager restart, and late
provider observations. Invariants include one current allocation, monotonic epoch,
no resurrection after tombstone, and at most one create dispatch per allocation
token.

## 6. Portable workspace case catalog

Every case below is mandatory on both Docker and E2B.

### 6.1 Python sessions

| ID | Scenario | Required assertions |
| --- | --- | --- |
| `WS-PY-001` | Stateful session | Variables, imports, functions, cwd persist across calls while active |
| `WS-PY-002` | Session isolation | Two concurrent sessions have distinct variables, cwd, environment, and failures |
| `WS-PY-003` | Error recovery | User exception returns structured error and next execution succeeds |
| `WS-PY-004` | Restart/delete | Restart clears only target state; delete prevents reconnect and kills descendants |
| `WS-PY-005` | Timeout/interrupt | Infinite execution is stopped, session is reset/degraded deterministically, sibling survives |
| `WS-PY-006` | Concurrency | Different sessions overlap; executions in one stateful context serialize |
| `WS-PY-007` | Output limits | stdout/stderr/result limits and truncation metadata are independent and bounded |
| `WS-PY-008` | Credential hygiene | Dynamic values work in-session, never enter durable rows/logs, and are invalid after release |

### 6.2 Shell, background process, and PTY

| ID | Scenario | Required assertions |
| --- | --- | --- |
| `WS-SH-001` | Foreground | argv/cwd/env, stdout/stderr, exit 0 and nonzero are exact |
| `WS-SH-002` | Yield | Yield returns before completion with stable operation/process reference |
| `WS-SH-003` | Background/reconnect | Process survives client disconnect; cursor reconnect receives ordered output |
| `WS-SH-004` | stdin/EOF | Multiple writes and EOF reach the exact process once |
| `WS-SH-005` | List/inspect | Only current logical sandbox and epoch processes are visible |
| `WS-SH-006` | Terminate tree | TERM/grace/KILL removes child and grandchildren before acknowledgment |
| `WS-SH-007` | Timeout | Deadline stops the process tree and reports typed timeout |
| `WS-SH-008` | Deduplication | Same operation ID joins; conflicting payload rejects; lost acknowledgment starts once |
| `WS-SH-009` | PTY lifecycle | Create, binary input/output, resize, disconnect/reconnect, exit, terminate |
| `WS-SH-010` | High output | Bounded buffers expose sequence/truncation gap without manager memory growth |
| `WS-SH-011` | Parallel operations | Shell/Python/file operations overlap without session/cwd contamination |
| `WS-SH-012` | Stale epoch | Input/reconnect/terminate from pre-replacement epoch fails closed |

### 6.3 Browser and application access

| ID | Scenario | Required assertions |
| --- | --- | --- |
| `WS-BR-001` | Browser boot | Profile contains working Chromium/browser tooling without request-time install |
| `WS-BR-002` | DOM interaction | Open deterministic page, snapshot, locate, click/type, observe mutation and URL |
| `WS-BR-003` | Artifact capture | HTML/markdown have expected content digest; screenshot decodes with expected dimensions/landmarks |
| `WS-BR-004` | HTTP port grant | Signed short-lived access reaches only requested current-epoch local server |
| `WS-BR-005` | WebSocket grant | Authenticated upgrade, bidirectional frames, expiry, and revocation work |
| `WS-BR-006` | Isolation | Wrong user/audience/port/epoch and raw provider URL/token access fail |
| `WS-BR-007` | Release/resume | Browser/process/grants are quiesced; files persist; a fresh browser session works after resume |

Browser correctness uses local deterministic content. A separate nonblocking smoke
may visit an approved public HTTPS fixture to detect provider CA/DNS regressions, but
public-site availability never decides core correctness.

### 6.4 Files and lifecycle

| ID | Scenario | Required assertions |
| --- | --- | --- |
| `WS-FS-001` | CRUD | stat/list/read/write/move/delete text and binary data |
| `WS-FS-002` | Streaming | Range read and large streamed write avoid base64/whole-body buffering |
| `WS-FS-003` | Atomic/conflict | Interrupted atomic write and expected-digest conflict preserve valid content |
| `WS-FS-004` | Boundary | traversal, symlink escape, special devices and disallowed roots fail closed |
| `WS-LC-001` | Idle release | Five-minute logical idle path quiesces processes/sessions before provider release |
| `WS-LC-002` | Resume | `/workspace` digest is preserved; nonportable session/process references are stale |
| `WS-LC-003` | Profile replacement | Files migrate/reuse correctly, epoch increments, failed replacement leaves old current |
| `WS-LC-004` | Permanent delete | Exact compute/storage disappear and late events cannot resurrect them |
| `WS-LC-005` | Retention | Activity before seven days resumes; expiry permanently deletes |
| `WS-LC-006` | Concurrent lifecycle | Ensure/release/destroy races converge without duplicate provider create/delete |

## 7. Provider-specific workspace cases

### 7.1 Docker

- Replacement containers mount the exact logical workspace volume.
- Stop/start and container replacement both preserve file digests.
- Runtime listens only on the private path expected by the manager; user code lacks
  its credential and the Docker socket.
- Signed app proxy binds loopback/random port and exposes no raw host/container
  address to the backend.
- Exact permanent delete removes the ledgered container and volume.

### 7.2 E2B

- Full-memory pause and auto-resume reconnect the same sandbox ID without a list or
  duplicate create.
- AgentBox-directed idle release quiesces code contexts, commands, PTYs, browser,
  dynamic values, and port grants before pause.
- A provider safety auto-pause observed after manager outage is scrubbed before the
  allocation is republished.
- Native commands, tags, stdin, PTY reconnect/resize, files, code contexts, and
  secured traffic satisfy the portable cases.
- Profile replacement copies/verifies the complete allowed manifest before atomic
  bind; injected copy failure keeps the original current.
- Every running or paused sandbox created by the run is exact-killed and the final
  metadata sweep is empty.

## 8. Function case catalog

Every full-stack case is mandatory with `FUNCTION/pod_id` on both Docker and E2B.

### 8.1 Revision and runtime readiness

| ID | Scenario | Required assertions |
| --- | --- | --- |
| `FN-BLD-001` | Ready revision | Source, lock, ABI, builder produce immutable signed artifact before `READY` |
| `FN-BLD-002` | Dependency fixture | Dependency is prebuilt; invocation has no package-manager/index activity |
| `FN-BLD-003` | Tamper/ABI | Digest, manifest, attestation, or ABI mismatch fails before user import |
| `FN-BLD-004` | Cache equivalence | Empty and warm artifact cache return identical result/log/error semantics |

### 8.2 API functions

| ID | Scenario | Required assertions |
| --- | --- | --- |
| `FN-API-001` | Cold no-op | Public API waits for durable terminal success and returns exact output |
| `FN-API-002` | Warm reuse | Same pod reuses allocation/cache without sharing attempt temp state |
| `FN-API-003` | Four-way overlap | Four standard invocations consume eight units and overlap safely |
| `FN-API-004` | Backend resources | Scoped datastore/file/connector operations traverse runtime gateway correctly |
| `FN-API-005` | Errors/schema/logs | User exception, invalid input/output, bounded logs and redaction are stable |
| `FN-API-006` | Timeout/cancel | Absolute deadline/cancel kills descendants before fenced terminal state |
| `FN-API-007` | Egress | Declared HTTPS succeeds; undeclared/private/direct/proxy-bypass fails |
| `FN-API-008` | Pod isolation | Different pod uses different allocation/cache/process/files/capability |

### 8.3 JOB functions

| ID | Scenario | Required assertions |
| --- | --- | --- |
| `FN-JOB-001` | Submission | Public call returns `PENDING`; durable queue/attempt survive requester exit |
| `FN-JOB-002` | Completion | Worker dispatch, callback, run terminal state and one outbox event agree |
| `FN-JOB-003` | Long run | Run survives beyond workspace/function idle observations without heartbeat |
| `FN-JOB-004` | Worker restart | Lease expiry/reclaim resumes orchestration without duplicate claimed execution |
| `FN-JOB-005` | Three-way saturation | Three standard JOBs consume six units; fourth remains queued |
| `FN-JOB-006` | API reserve | A standard API attempt starts within gate while JOB capacity is saturated |
| `FN-JOB-007` | Cancel | Queued cancellation starts no code; running cancellation targets exact process tree |
| `FN-JOB-008` | Failure | Deterministic user/platform failure persists stable public status/log/error |

### 8.4 Shared API/JOB correctness

- API is always selected before queued JOB at the same pod; running JOBs are never
  force-preempted.
- Both kinds use the same backend queue, attempt/fence model, runtime gateway, and
  per-pod sandbox. No sandbox-local queue/result registry exists.
- The single-use ticket is sent through stdin, can be claimed once, is hashed at
  rest, and cannot authorize another attempt/pod/revision.
- argv, environment, writable files, provider inspection, and diagnostics contain
  no reusable human/provider/object-store credential.
- Function allocation is destroyed after five idle minutes; the next cold attempt
  succeeds with no required state from the old allocation.

## 9. Fault, restart, and ambiguity matrix

An injectable boundary wraps the production provider adapter/runtime-gateway
transports without replacing the real provider. It can drop a response after the
real side effect, delay visibility, return a normalized provider 429, disconnect a
stream, or suppress/duplicate callbacks.

Mandatory cases on Docker and E2B:

| ID | Injected boundary | Required result |
| --- | --- | --- |
| `CH-CREATE-001` | Create accepted, response lost | One provider create; allocation `UNKNOWN` then exact reconciliation |
| `CH-CREATE-002` | Provider 429/retry-after | Distributed scope waits; no adapter-local retry storm |
| `CH-START-001` | Process started, acknowledgment lost | One operation start; tag/runtime record reconciles same process |
| `CH-TICKET-001` | Claim succeeds, started callback lost | No non-idempotent replay; inspection/reconciliation determines outcome |
| `CH-CALLBACK-001` | Terminal callback duplicated | One terminal transition, unit release, and outbox event |
| `CH-CALLBACK-002` | Terminal callback lost | Reconcile exact attempt; non-idempotent unresolved becomes `INDETERMINATE` |
| `CH-RESTART-001` | AgentBox exits after intent commit | Restart dispatches/reconciles according to recorded dispatch state, never blindly creates |
| `CH-RESTART-002` | Backend worker exits after ticket claim | No unsafe replay; lease/fence and runtime inspection converge |
| `CH-DEATH-001` | Sandbox dies during run | Exact attempt terminal/indeterminate policy; capacity repaired; later allocation is fresh |
| `CH-FENCE-001` | Old callback after new fence | Stale callback changes no public/domain state |

The non-idempotent fixture performs a visible gateway-counted side effect. Every
ambiguity test asserts the side-effect count is at most one, not merely that one
database row exists.

Natural provider rate-limit abuse is not part of routine CI. Provider-specific 429
parsing is unit-tested from captured/constructed official response shapes, while the
distributed behavior is tested deterministically through the injectable boundary.

## 10. Security, performance, and soak suites

Security cases in [Verification and rollout](verification-and-rollout.md) run on
both real providers. Docker proves configuration and control boundaries but remains
explicitly unsuitable as hostile multi-tenant isolation.

Performance lanes collect stage spans rather than parsing logs. Correctness cases
emit samples but do not fail on one slow call. Promotion benchmarks use sufficient
warm/cold/resume samples to enforce the documented p95 gates with provider, region,
quota, profile digest, and sample count attached.

The executable mixed API/JOB table workload, commands, report schema, current
regression budgets, and initial Docker/E2B parity evidence are maintained in the
[function execution benchmark runbook](../../operators/agentbox-function-benchmark.md).
Its five-sample p95 gate is an early regression detector; it does not replace the
larger promotion distributions required by this design.

Nightly soak scenarios:

- repeated workspace command/Python/file/browser operations across concurrent users;
- 100 release/resume cycles per provider with file-manifest verification;
- sustained mixed API/JOB load across multiple pod sandboxes and allocation churn;
- manager/backend restarts during load;
- E2B create/pause/resume/kill lifecycle under bounded account capacity;
- zero leaked Docker containers/volumes or E2B running/paused sandboxes at end.

Soak counts are configurable for cost, but the release report records actual counts
and duration. A shorter run cannot be labeled as the required soak gate.

## 11. CI and promotion lanes

| Lane | Trigger | Contents | Blocking |
| --- | --- | --- | ---: |
| `agentbox-hermetic` | Every relevant PR | unit, state SQLite, contracts, deterministic faults, backend fake integration | Yes |
| `agentbox-postgres` | Every relevant PR | real PostgreSQL repository/migration/concurrency contracts | Yes |
| `agentbox-docker` | Protected PR/merge queue | real Docker portable + provider + full-stack API/JOB | Yes |
| `agentbox-e2b` | Trusted protected PR/merge queue | real E2B portable + provider + full-stack API/JOB | Yes |
| `agentbox-security` | Nightly and release candidate | malicious fixtures on Docker/E2B | Release blocking |
| `agentbox-performance` | Nightly and release candidate | documented latency/capacity distributions | Release blocking |
| `agentbox-soak` | Nightly and release candidate | lifecycle/load/restart/leak soak | Release blocking |

“Relevant PR” includes AgentBox, generated client, workspace adapter/tools, function
domain/dispatcher/gateway/runtime, database models/migrations, images/templates,
build pipeline, or these suites. A release candidate reruns all lanes on one exact
SHA/manifest even if individual PR results exist.

Untrusted forks cannot receive E2B credentials. Their code is not merge-eligible
until a trusted merge-queue run executes the exact candidate SHA in the protected E2B
environment. The required E2B job must fail rather than call `pytest.skip` when its
preflight configuration is absent.

Quarantine is not allowed for a mandatory case. A case may move out of the blocking
suite only with a documented design change proving the behavior is no longer
required. Infrastructure outages may be classified separately, but they do not
publish conformance or permit provider-profile promotion.

## 12. Kubernetes deferred program

The Kubernetes adapter remains specified in
[Provider adapters](provider-adapters.md), but the initial release does not claim it
as supported. A later milestone will add:

1. a reproducible kind/k3d/minikube lane with dynamic PVC provisioning for portable
   lifecycle and functional conformance;
2. a separate real cluster with the production gVisor/Kata RuntimeClass, network
   policy, dedicated nodes, and storage class for security/isolation evidence;
3. the same full-stack workspace/browser/API/JOB case IDs used by Docker/E2B; and
4. Kubernetes-specific watch, Pod UID, PVC, ServiceAccount, NetworkPolicy, and
   exact-cleanup fault cases.

Kubernetes cannot be enabled by manually checking a few Pod tests or by inheriting
Docker evidence. Its profile becomes eligible only after the complete later matrix
passes and [Verification and rollout](verification-and-rollout.md) is updated with
its release evidence.

## 13. Current-suite migration

The retired suite supplied behavioral inputs for the canonical tests:

- runtime session tests seed `WS-PY-*` and `WS-SH-*` cases;
- real local-provider tests seed Docker workspace/lifecycle/browser coverage;
- real E2B tests seed E2B lifecycle/browser/process coverage;
- backend browser-runtime tests seed the full-stack browser journey;
- backend function E2E tests seed API/JOB/result/grant/concurrency journeys.

The canonical suite uses the provider-neutral API, immutable profiles, and shared
case harness. Tests for the retired in-sandbox HTTP execution server, request-time
package installation, in-sandbox result polling/registry, heartbeat behavior,
Podman, Daytona, or direct SQLite `_conn` mutation were deleted rather than ported.

Maintain a case manifest mapping every mandatory ID to its
test node ID for Docker and E2B. The experimental suite may run as migration
protection only on historical branches; it cannot satisfy an acceptance gate.

## 14. Evidence and quality bar

Every blocking run publishes a machine-readable report containing:

```text
case ID and pytest node ID
provider and profile/build digests
pass/fail/infrastructure classification
allocation and operation correlation IDs (secured artifact)
provider create/start call counts from fault wrapper
stage latency samples
redaction audit result
cleanup ledger and final empty-sweep result
database/Alembic revision
```

The suite itself has tests for fixture cleanup, secret redaction, manifest mismatch,
fault-wrapper call counting, and missing required-provider configuration. A green
report with incomplete cleanup, skipped mandatory cases, mutable profiles, or an
unmatched case manifest is invalid.
