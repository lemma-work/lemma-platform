# The sandbox runtime Sandbox Protocol

**Status:** Implemented and verified for Docker and E2B; Kubernetes deferred

**Parent:** [the sandbox runtime](README.md)

**Test contract:** [Testing strategy](testing-strategy.md)

## 1. Purpose

This document defines the sandbox runtime's provider-neutral model, internal ports, durable
state, canonical breaking HTTP/WebSocket API, error semantics, admission behavior,
and reconciliation algorithms. Provider-specific mappings are defined in
[Provider adapters](provider-adapters.md).

The protocol separates four resources that the current implementation conflates:

1. a stable logical sandbox requested by Lemma;
2. logical workspace storage, when the workload has portable file persistence;
3. a physical allocation created by a provider;
4. an individual process or Python context inside that allocation.

No provider-generated ID is accepted as a public logical sandbox ID.

## 2. Common types

The examples are normative Python-like definitions. Concrete implementation may use
Pydantic/dataclasses, but field names and semantics must remain stable.

```python
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

LogicalId = NewType("LogicalId", UUID)
AllocationId = NewType("AllocationId", UUID)
OperationId = NewType("OperationId", UUID)


class WorkloadKind(StrEnum):
    WORKSPACE = "workspace"
    FUNCTION = "function"


class SandboxDesiredState(StrEnum):
    PRESENT = "present"
    RELEASED = "released"       # workspace only
    DELETED = "deleted"


class AllocationState(StrEnum):
    RESERVED = "reserved"
    PROVISIONING = "provisioning"
    UNKNOWN = "unknown"
    ACTIVE = "active"
    QUIESCING = "quiescing"
    RELEASED = "released"
    DRAINING = "draining"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    ERROR = "error"


class ProcessState(StrEnum):
    RESERVED = "reserved"
    STARTING = "starting"
    UNKNOWN = "unknown"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class RetryDisposition(StrEnum):
    WAIT = "wait"
    SAFE_SAME_OPERATION = "safe_same_operation"
    DO_NOT_RETRY = "do_not_retry"
```

```python
class SandboxKey(BaseModel):
    workload_kind: WorkloadKind
    logical_id: UUID


class SandboxProfileRef(BaseModel):
    name: str
    digest: str                 # sha256:<hex>


class SandboxHandle(BaseModel):
    key: SandboxKey
    desired_state: SandboxDesiredState
    allocation_state: AllocationState | None
    allocation_id: UUID | None
    allocation_epoch: int
    profile: SandboxProfileRef
    ready: bool
    operation_id: UUID | None
    retry_after_ms: int | None


class ProcessRef(BaseModel):
    operation_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    provider_process_id: str | None
    state: ProcessState
    started_at: datetime | None
    deadline_at: datetime
```

`allocation_epoch` increments whenever the logical sandbox receives a different
physical allocation or a provider release cannot preserve process identity. A
session or process bound to an older epoch is stale and must fail with
`ALLOCATION_CHANGED`; it must never be redirected to the new allocation.

## 3. Profile model

The sandbox runtime loads an immutable profile registry at startup. A profile contains:

```python
class SandboxProfile(BaseModel):
    ref: SandboxProfileRef
    workload_kind: WorkloadKind
    provider_artifacts: dict[str, ProviderArtifactRef]
    runtime_abi: str
    resource_class: str
    capabilities: set[Capability]
    filesystem_policy: FilesystemPolicy
    network_policy: NetworkPolicy
    lifecycle_policy: LifecyclePolicy
```

`ProviderArtifactRef` is exact and immutable:

- Docker: OCI image digest;
- Kubernetes: OCI image digest plus Pod template digest;
- E2B: template ID/build ID pinned by profile publication.

The sandbox runtime refuses an ensure request when:

- the profile does not exist;
- the kind does not match;
- its provider artifact is absent;
- the configured provider lacks a mandatory capability;
- a production deployment selects an unsafe runtime/network combination.

Profiles contain only static, non-secret configuration. Dynamic credentials belong
to sessions or process input and are never part of a logical sandbox record.

## 4. Hexagonal ports

### 4.1 `SandboxLifecyclePort`

```python
class SandboxLifecyclePort(Protocol):
    async def ensure(
        self,
        key: SandboxKey,
        profile: SandboxProfileRef,
        *,
        deadline_at: datetime,
        admission_class: AdmissionClass,
    ) -> SandboxHandle: ...

    async def inspect(self, key: SandboxKey) -> SandboxHandle | None: ...

    async def release(
        self,
        key: SandboxKey,
        *,
        deadline_at: datetime,
    ) -> SandboxHandle: ...

    async def destroy(
        self,
        key: SandboxKey,
        *,
        delete_storage: bool,
        deadline_at: datetime,
    ) -> DestroyResult: ...
```

Rules:

- `ensure` is idempotent for `(key, profile.digest)`.
- `release` is supported only for workspace profiles.
- Function cleanup always uses `destroy(delete_storage=False)`.
- Workspace permanent deletion uses `destroy(delete_storage=True)`.
- A profile change marks the current allocation `DRAINING`; it does not alter it.
- An accepted asynchronous transition returns an operation reference, not a fake
  `RUNNING` state.

### 4.2 `ProcessExecutionPort`

```python
class ProcessExecutionPort(Protocol):
    async def start(
        self,
        key: SandboxKey,
        request: StartProcessRequest,
    ) -> ProcessRef: ...

    async def inspect(
        self, key: SandboxKey, operation_id: UUID
    ) -> ProcessResult: ...

    async def connect(
        self, key: SandboxKey, operation_id: UUID, *, after_seq: int
    ) -> ProcessStream: ...

    async def send_input(
        self, key: SandboxKey, operation_id: UUID, data: bytes
    ) -> None: ...

    async def resize(
        self, key: SandboxKey, operation_id: UUID, cols: int, rows: int
    ) -> None: ...

    async def list(self, key: SandboxKey) -> list[ProcessRef]: ...

    async def terminate(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        grace_seconds: float,
    ) -> TerminateResult: ...
```

```python
class StartProcessRequest(BaseModel):
    operation_id: UUID
    command: str | None = None
    argv: list[str] | None = None
    cwd: str
    env: dict[str, str] = {}
    stdin: bytes | None = None
    tty: TerminalSize | None = None
    background: bool = False
    output_limit_bytes: int
    deadline_at: datetime
```

Exactly one of `command` and `argv` is supplied. Workspace shell tools normally use
`command`; trusted platform launchers use `argv` to avoid shell interpretation.

`operation_id` identifies the intention to start one process within a live manager
incarnation. The sandbox runtime keeps a bounded in-memory record and a request fingerprint;
neither the request nor its fingerprint is persisted or logged. Reusing the same
ID with a different fingerprint is
`OPERATION_CONFLICT`. Reusing it with the same hash returns the same process while
that routing record is available; it does not start a second process.

Environment values and initial stdin are transmitted to the adapter but are not
stored. Logs must never print env values or stdin.

`background=False` is a client convenience, not a different provider primitive.
If a response is lost after the provider may have started the process, the sandbox runtime
returns `UNKNOWN_DISPATCH` and does not replay it. A process remains inspectable
only while its manager routing record and allocation incarnation remain live.

Streams are sequence-numbered. `after_seq` permits reconnect without requiring an
adapter to replay unlimited output. The sandbox runtime stores only a bounded output tail; the
response reports `truncated_before_seq` when earlier output is unavailable.

### 4.3 `PythonSessionPort`

```python
class PythonSessionPort(Protocol):
    async def create(
        self, key: SandboxKey, session_id: str, request: CreateSessionRequest
    ) -> SessionRef: ...

    async def execute(
        self,
        key: SandboxKey,
        session_id: str,
        request: ExecutePythonRequest,
    ) -> PythonResult: ...

    async def restart(self, key: SandboxKey, session_id: str) -> SessionRef: ...

    async def delete(self, key: SandboxKey, session_id: str) -> bool: ...
```

```python
class CreateSessionRequest(BaseModel):
    cwd: str
    env: dict[str, str] = {}
    deadline_at: datetime


class ExecutePythonRequest(BaseModel):
    operation_id: UUID
    code: str
    output_limit_bytes: int
    deadline_at: datetime
```

Sessions are workspace-only. Calls within one session serialize to preserve Python
state; different sessions may execute concurrently. Timeout, explicit restart,
session deletion, or allocation-epoch change resets only that session.

Environment is applied for the duration of an execution and restored afterward. A
session response returns environment key names, never values.

### 4.4 `FilesystemPort`

```python
class FilesystemPort(Protocol):
    async def stat(self, key: SandboxKey, path: str) -> FileStat: ...
    async def list(self, key: SandboxKey, path: str) -> list[FileStat]: ...
    async def open_read(
        self, key: SandboxKey, path: str, byte_range: ByteRange
    ) -> AsyncIterator[bytes]: ...
    async def write_stream(
        self, key: SandboxKey, path: str, data: AsyncIterable[bytes]
    ) -> FileStat: ...
    async def move(self, key: SandboxKey, source: str, destination: str) -> None: ...
    async def delete(self, key: SandboxKey, path: str, recursive: bool) -> bool: ...
```

Paths are absolute and must resolve below the profile's allowed roots. Workspace
callers use `/workspace`; function runtime internals may additionally use a private
ephemeral cache root. Symlink resolution is checked at the adapter boundary.

Writes use a temporary sibling file, fsync when supported, and atomic rename. The
API streams binary bytes and never base64-encodes through a shell command. Range
reads and a configured maximum transfer size support large files without buffering
them entirely in the sandbox runtime memory. The default transfer bound is 256 MiB and may be
configured up to 2 GiB. Docker streams through the private workspace runtime. E2B
downloads through its native async reader and uploads through a bounded spooled
file because the E2B SDK accepts file-like upload bodies. A failed or oversized
upload never replaces the destination.

### 4.5 `PortAccessPort`

```python
class PortAccessPort(Protocol):
    async def grant(
        self,
        key: SandboxKey,
        *,
        port: int,
        protocol: Literal["http", "websocket"],
        audience: str,
        ttl_seconds: int,
    ) -> PortAccessGrant: ...
```

Workspace profiles support caller-facing application grants. Function profiles
support only the fixed private resident-runtime port declared by the immutable
profile, and only the backend service audience may request that grant. The adapter
returns an opaque grant with URL, expiry, allocation ID, and epoch. Provider
addresses, traffic tokens, and credentials remain behind the sandbox runtime's signed proxy.
Grants are invalid after allocation replacement, workspace release, or function
destruction.

### 4.6 Internal provider ports

`ProviderInventoryPort` supports exact-ID inspection plus background inventory.
`ProviderAdmissionPort` reserves and releases provider-level allocation capacity.
Neither is available over the public manager API.

## 5. Canonical API

All routes require `X-API-Key` plus service identity at the network layer. Requests
accept `X-Request-ID` for tracing. Mutation bodies contain `deadline_at`; the sandbox runtime
rejects expired requests without provider activity.

Base path:

```text
/sandboxes/{workload_kind}/{logical_id}
```

### 5.1 Lifecycle routes

| Method and route | Behavior |
| --- | --- |
| `PUT /sandboxes/{kind}/{id}` | Ensure exact profile; `200` active or `202` accepted |
| `GET /sandboxes/{kind}/{id}` | Read durable logical/allocation state only |
| `POST /sandboxes/workspace/{id}:release` | Quiesce and release workspace compute |
| `DELETE /sandboxes/{kind}/{id}` | Permanent logical deletion; workspace also deletes storage |
| `GET /operations/{operation_id}` | Inspect asynchronous lifecycle operation |
| `GET /operations/{operation_id}:wait` | Bounded long-poll until change or caller deadline |

Ensure body:

```json
{
  "profile": {"name": "workspace-python-v1", "digest": "sha256:..."},
  "admission_class": "interactive",
  "deadline_at": "2026-07-22T12:30:00Z"
}
```

`GET` never calls provider inventory or waits for readiness. `:wait` waits on an
The sandbox runtime state notification; clients do not poll every second.

### 5.2 Process routes

| Method and route | Behavior |
| --- | --- |
| `POST .../processes` | Start one manager-local operation ID |
| `GET .../processes` | List bounded process handles for current manager/allocation |
| `GET .../processes/{operation_id}` | Inspect status and bounded result tail |
| `GET .../processes/{operation_id}/stream` | WebSocket output stream/reconnect |
| `POST .../processes/{operation_id}:input` | Send binary/text stdin |
| `POST .../processes/{operation_id}:resize` | Resize a PTY |
| `DELETE .../processes/{operation_id}` | Terminate exact process/process group |

`POST` returns:

- `201` when the provider acknowledged the process;
- `200` for an identical existing operation;
- `UNKNOWN_DISPATCH` with `DO_NOT_RETRY` if dispatch may have occurred;
- a retryable typed error only when the provider definitively rejected the start
  before execution.

### 5.3 Python-session routes

| Method and route | Behavior |
| --- | --- |
| `PUT .../sessions/{session_id}` | Create or return same current-epoch session |
| `POST .../sessions/{session_id}:python` | Execute stateful Python |
| `POST .../sessions/{session_id}:restart` | Reset Python state, retaining cwd/config |
| `DELETE .../sessions/{session_id}` | Terminate and forget the session |

Session IDs are DNS-safe, bounded strings. They are not authorization tokens.

### 5.4 Filesystem routes

```text
GET    .../files:stat?path=/workspace/a.txt
GET    .../files?path=/workspace
GET    .../files:content?path=/workspace/a.txt
PUT    .../files:content?path=/workspace/a.txt
POST   .../files:move
DELETE .../files?path=/workspace/a.txt&recursive=false
```

Read/write bodies use `application/octet-stream`. Metadata is carried in response
headers and typed JSON for `stat`/`list`. Conditional writes accept an optional
expected content digest. Upload and download bodies are consumed incrementally;
disconnects close the upstream stream and incomplete upload temporaries are removed.

### 5.5 Port-access route

```text
POST .../ports/{port}:access
POST .../sandboxes/function/{logical_id}/runtime:lease
```

The signed-access request contains protocol and TTL and remains the user-facing
port mechanism. Function runtime execution instead uses the manager-key-protected
lease endpoint. It resolves only the current active allocation at immutable port
`8090`, binds the response to allocation ID/epoch and profile, protects activity
through the requested bounded horizon, and returns the provider URL plus opaque
request headers and absolute expiry. It never forwards an invocation.

The trusted backend caches a lease only by logical pod plus immutable profile and
never beyond its expiry. The manager key is used only to obtain or refresh the
lease. The delegated function bearer remains in `Authorization` on the subsequent
direct runtime request and is validated by the runtime/backend.

## 6. Durable persistence model

PostgreSQL stores only lifecycle state that can support reconciliation. The
normative rationale and failure contract are in
[Lifecycle state model](lifecycle-state-model.md).

| Table | Durable responsibility |
| --- | --- |
| `sandboxes` | Logical desired state, immutable profile selection, resource generation, current allocation epoch, activity protection, and maintenance claim |
| `allocations` | Exact physical provider allocation, generation/epoch, lifecycle state, and admission ownership |
| `allocation_create_attempts` | Write-ahead dispatch state used to reconcile an ambiguous provider create without issuing it twice |
| `workspace_storage` | Logical storage ownership and deletion; may be allocation-bound for sandbox-native providers |
| `provider_admission` | Provider-scope capacity reservations and token-bucket state |

`resource_generation` is a desired-state fence. Any delayed create, ready, release,
or destroy completion must match the current generation before it can publish
state. `allocation_epoch` is a data-plane fence. A process, Python session, port
grant, or filesystem operation from an older epoch cannot be redirected to the
replacement.

Process handles, Python sessions/results, output buffers, and stream cursors are
bounded manager memory. They deliberately disappear across manager or allocation
incarnations. The schema therefore has no `processes`, `sessions`, or
`python_executions` tables.

The sandbox runtime uses SQLAlchemy 2.x async mappings and short-lived units of work.
Transactions end before provider I/O. ORM rows never escape the persistence
adapter. Alembic migrations define the shared PostgreSQL schema and the equivalent
single-manager SQLite test schema.

## 7. Lifecycle algorithms

### 7.1 Ensure

1. Validate key, profile, provider capabilities, and deadline.
2. Lock the logical key transactionally.
3. For a workspace, ensure the logical storage row exists and is compatible with
   the selected provider before allocating compute.
4. Return the current active matching allocation if present.
5. Return the existing provisioning/unknown operation if the same profile is in
   flight.
6. If the profile changed, mark the old allocation draining; do not send new work.
7. Reserve provider capacity for the request's admission class.
8. Insert allocation and create-attempt rows with a unique allocation token.
9. Commit, then issue exactly one provider create call carrying the token in
   provider metadata/labels.
10. On acknowledgment, bind the exact provider ID, run provider readiness, and
   atomically publish the allocation as current.
11. On definitive rejection, release reservation and fail.
12. On ambiguous transport/provider outcome, mark `UNKNOWN`, retain reservation,
    return `AMBIGUOUS_CREATE`, and reconcile later.

No caller retry can create a second provider object for the same attempt.

### 7.2 Workspace release

Every accepted workspace data-plane operation updates logical `last_used_at`. A
distributed idle worker claims workspaces idle for five minutes and invokes the same
release operation as an explicit caller; provider timeout is not the portable idle
scheduler.

1. Lock logical key and set allocation `QUIESCING`.
2. Reject new sessions/processes with `SANDBOX_QUIESCING`.
3. Wait for foreground operations until the release deadline.
4. Terminate remaining managed processes and Python contexts.
5. Clear dynamic session data and provider port grants.
6. Invoke provider release.
7. Mark `RELEASED`, record the configured hard-expiry deadline relative to the last
   accepted activity, and release active provider capacity.

If the deadline expires before safe quiescence, release fails and leaves the
allocation active or explicitly degraded; it never pauses behind an active process.

At hard expiry, the sandbox runtime destroys the exact allocation and workspace storage but
retains a recreatable logical workspace. The next ensure creates a new allocation
and storage generation. An explicit delete is different: it permanently tombstones
the logical workspace and future ensure returns `SANDBOX_NOT_FOUND`.

### 7.3 Function idle destruction

The sandbox runtime updates `last_used_at` on signed resident-runtime access. A background
cleanup worker selects current `FUNCTION` allocations with no active port lease and
five minutes of idle time, marks them `DESTROYING`, and calls exact provider
deletion. The backend/runtime deadline extension protects an active long JOB.
There is no release/suspend transition.

### 7.4 Permanent destroy

Destroy records a logical tombstone before provider mutation. Every known exact
allocation is deleted. For workspaces, the exact `sandbox_workspace_storage`
resource is then deleted using its durable provider ID or delete token. The logical
tombstone remains until compute and storage are confirmed absent, preventing late
webhook/inventory observations from resurrecting the sandbox.

## 8. Provider readiness

Readiness means the provider can serve the capabilities required by the selected
profile. It is profile-specific rather than a universal port-8080/8090 check.

- Docker/Kubernetes workspace: allocation running plus private workspace runtime
  ready and authenticated.
- E2B workspace: exact sandbox connected plus one native command/filesystem smoke
  operation; the template already contains build-time-ready static processes.
- Function: exact allocation plus resident runtime `/healthz` on the fixed private
  profile port with the expected runtime ABI.

Readiness is performed once per allocation publication and after an explicit
degraded repair. Normal operations do not repeat it.

E2B template start/ready commands execute during template build and are captured in
the sandbox snapshot, so create-time environment variables cannot be dependencies
of template startup. See [E2B start and ready commands](https://e2b.dev/docs/template/start-ready-command).

## 9. Admission and 429 handling

The sandbox runtime has generic admission classes:

```text
interactive  - workspace creation/resume
latency      - latency-sensitive stateless allocation
batch        - background stateless allocation
```

Provider scope configuration reserves concurrent and creation capacity between
classes. This admission protects provider-wide sandbox creation and
active-allocation quotas; it does not impose an invocation concurrency model.

On a provider 429:

1. parse and bound `Retry-After`;
2. atomically set the provider scope's `blocked_until`;
3. return `RATE_LIMITED` with `WAIT` and the same retry time;
4. prevent other replicas from calling create until that time;
5. retain already-dispatched ambiguous reservations.

There is no adapter-local exponential loop around create. Waiting happens before
dispatch under the caller's deadline. Official E2B plan limits include bounded
concurrent sandboxes and creation rates, so these are scheduling inputs rather than
exceptional failures. See [E2B billing and limits](https://e2b.dev/docs/billing).

## 10. Error contract

```json
{
  "error": {
    "code": "AMBIGUOUS_CREATE",
    "message": "Provider acceptance is not yet known",
    "retry_disposition": "wait",
    "retry_after_ms": 1000,
    "operation_id": "...",
    "allocation_id": "...",
    "provider_code": "...",
    "request_id": "..."
  }
}
```

Normative mapping:

| Code | HTTP | Disposition | Meaning |
| --- | ---: | --- | --- |
| `CAPACITY_EXHAUSTED` | 429 | `WAIT` | the sandbox runtime admission has no eligible capacity |
| `RATE_LIMITED` | 429 | `WAIT` | Provider scope is blocked until retry-after |
| `PROVISIONING` | 409/202 | `WAIT` | Matching allocation is still becoming ready |
| `AMBIGUOUS_CREATE` | 503 | `WAIT` | Create may have been accepted; do not recreate |
| `UNKNOWN_DISPATCH` | 409 | `DO_NOT_RETRY` | Work may have started; do not replay the same operation |
| `PROCESS_NOT_RUNNING` | 410 | `DO_NOT_RETRY` | The manager/PID handle is gone; do not replay stdin |
| `DEADLINE_EXCEEDED` | 504 | `DO_NOT_RETRY` | Caller deadline expired |
| `UNSUPPORTED_CAPABILITY` | 422 | `DO_NOT_RETRY` | Profile/provider cannot perform requested operation |
| `PROVIDER_UNAVAILABLE` | 503 | contextual | Definitive pre-dispatch failure or unavailable read |
| `ALLOCATION_CHANGED` | 409 | `DO_NOT_RETRY` | Session/process belongs to a stale epoch |
| `PROCESS_NOT_RUNNING` | 410 | `DO_NOT_RETRY` | Manager handle or exact provider process no longer exists |
| `OPERATION_CONFLICT` | 409 | `DO_NOT_RETRY` | Operation ID reused with different request |
| `FILE_NOT_FOUND` | 404 | `DO_NOT_RETRY` | Filesystem path definitively does not exist |
| `FILE_CONFLICT` | 409 | `DO_NOT_RETRY` | Digest precondition, destination, or directory constraint failed |
| `INVALID_REQUEST` | 4xx | `DO_NOT_RETRY` | Filesystem or request validation was definitively rejected |

`SAFE_SAME_OPERATION` is allowed only when the sandbox runtime proves the provider did not
accept a request or the exact provider operation is intrinsically idempotent. It
always reuses the same allocation/operation ID.

Provider transport failures, timeouts, rate limits, and unavailable allocations must
never be collapsed into `FILE_NOT_FOUND`. Adapters translate native provider/runtime
errors into typed filesystem port failures; the service boundary then emits the
portable codes above. Definitive payload limits preserve their meaningful HTTP status
(`413`, `422`, or `507`) while exposing `INVALID_REQUEST`.

## 11. Reconciliation

Reconciliation is background repair, never a prerequisite for a warm request.

Inputs:

- durable logical sandbox, allocation, create-attempt, storage, and admission state;
- exact provider-ID inspection;
- allocation-token metadata/label queries for unknown creates;
- signed E2B lifecycle webhooks;
- periodic provider inventory as leak detection.

Rules:

- Webhook delivery is deduplicated by provider event/delivery ID.
- A list omission does not prove deletion.
- Only exact-ID delete/not-found postconditions make deletion terminal.
- An allocation-token query may bind one exact provider object; duplicate objects
  are quarantined and exact-deleted after review/grace.
- Unknown process dispatch is not replayed or promoted into durable lifecycle
  state. The caller receives a typed non-retryable ambiguity.
- Stale profile allocations drain and destroy after bounded activity protection
  expires.
- Orphans are quarantined before destruction and cannot be adopted merely because
  their logical ID matches.

E2B lifecycle webhooks are authenticated, retried, and may be duplicated. Handlers
must verify signatures and deduplicate delivery IDs. See
[E2B lifecycle webhooks](https://e2b.dev/docs/sandbox/lifecycle-events-webhooks).

## 12. Observability

Every event carries:

```text
request_id
workload_kind
logical_id
allocation_id
allocation_epoch
provider_name
provider_id (internal telemetry only)
profile_digest
operation_id (when applicable)
```

Required stage timings:

- admission wait;
- provider create dispatch and acknowledgment;
- readiness;
- release/resume/destroy;
- process start acknowledgment;
- first output and completion;
- reconciliation latency.

Required counters/gauges:

- logical and physical sandboxes by kind/state/provider/profile;
- provider capacity/reservations/create tokens;
- provider 429s and blocked duration;
- ambiguous creates and unknown process dispatches;
- release/destroy failures;
- stale epochs and operation conflicts;
- warm/resumed/cold request classification;
- retained workspace storage and function warm allocations.
