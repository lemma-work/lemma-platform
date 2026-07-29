# Function Execution

**Status:** Protocol v2 implemented for Docker and E2B; Kubernetes deferred

## 1. Decision

One public `function_run_id` represents one user-requested execution. The
backend owns authorization, the durable run, immutable artifacts, deadlines,
cancellation and terminal events. AgentBox owns the provider-neutral sandbox,
allocation lifecycle and the API-key-authenticated proxy to its private runtime
port.

Each pod has at most one active `FUNCTION` sandbox. It runs a resident
`lemma-function-runtime` service that caches immutable artifacts and imported
revision workers.

The executor is deliberately mostly stateless:

- it accepts an invocation envelope plus the existing delegated function token;
- API execution returns its terminal report directly;
- JOB execution reports its terminal result with the same function token;
- cold artifact and schema access use that same function token;
- cancellation is admitted by the trusted AgentBox manager route;
- there is no separate callback or compilation capability.

Functions inside one pod mutually trust one another. Different pods never share
a sandbox, artifact cache, worker, token or process. This trust boundary is
intentional: the runtime supervisor and user workers currently share UID
`10001`, so a fixed secret inside the sandbox would not create a security
boundary.

## 2. Ownership

| Component | Owns |
| --- | --- |
| Function domain | definitions, grants, schemas, active revision, runs and events |
| Artifact builder | deterministic dependency resolution and immutable archives |
| Backend dispatcher | run start, runtime invocation and direct API completion |
| Durable worker | asynchronous JOB dispatch until runtime acceptance |
| Runtime gateway | authenticated artifact reads and JOB terminal reports |
| AgentBox | allocation, lifecycle, trusted runtime proxy and provider reconciliation |
| Resident runtime | run deduplication, artifact cache and revision-worker pools |
| Revision worker | one imported revision and isolated per-call SDK context |

AgentBox does not understand functions, JOBs, artifacts, run statuses or
delegated tokens. The backend does not import Docker or E2B provider SDKs.

## 3. Durable state

The execution plane uses the existing function and run rows:

```text
functions
  revision_hash nullable

function_runs
  revision_hash nullable
  deadline_at nullable
  job_id nullable
```

There is no attempts table, execution ticket, callback digest, result registry
or sandbox-local durable queue.

The public state machine is:

```text
PENDING -> RUNNING -> COMPLETED
                   -> FAILED
                   -> CANCELLED

PENDING ----------> FAILED
        ----------> CANCELLED
```

The backend conditionally changes `PENDING` to `RUNNING` immediately before
dispatch. API results and JOB callbacks conditionally change only `RUNNING` to a
terminal state. Repeated terminal reports are acknowledged as duplicates and do
not emit another transition. A terminal state is never overwritten.

## 4. Immutable revisions and schema extraction

`revision_hash` is the SHA-256 of a deterministic archive containing the exact
source, prebuilt locked dependencies, entrypoint and Pydantic model names,
runtime ABI, artifact format and builder identity.

Creating a run copies the active hash onto the run, so later updates cannot alter
an existing execution:

```text
artifacts/<sha256>.zip
revisions/<sha256>/function.py
```

The builder writes the artifact before schema extraction. After the DRAFT
function ID and revision are known, the backend mints a normal delegated function
token for the acting user and asks the pod's resident function runtime to inspect
that exact artifact.

The runtime checks its artifact cache before downloading. On a miss it calls:

```http
GET /internal/function-runtime/functions/{function_id}/artifacts/{revision_hash}
Authorization: Bearer <delegated function token>
```

The backend uses canonical delegated authentication and requires:

- FUNCTION actor ID equals the path function ID;
- authenticated pod owns the function;
- the deterministic session ID matches the acting user, function and requested
  revision;
- the returned artifact has the requested digest.

The runtime verifies the digest again. It imports the revision in a serving
worker whose readiness response includes the Pydantic schemas, then leaves that
worker idle in the revision pool. The first function execution can reuse the
already-imported worker. Schema inspection never creates or mounts a user
workspace and never passes the delegated token into user import code.

## 5. Invocation protocol v2

The backend calls a stable AgentBox route for the pod using the existing manager
API key. AgentBox resolves the current active allocation, extends its activity
lease and proxies to private port `8090`. The manager key is stripped before the
request enters the sandbox. The backend invokes:

```http
POST /trusted/function-runtimes/{pod_id}/functions/{function_id}/runs/{function_run_id}
X-API-Key: <AgentBox manager key>
X-AgentBox-Activity-Until: <execution deadline plus JOB callback grace>
Authorization: Bearer <delegated function token>
If-Match: "sha256:<revision_hash>"
X-Lemma-Gateway-Url: <allow-listed backend URL>
Content-Type: application/json

{
  "protocol_version": 2,
  "input": {},
  "config": null,
  "identity": {
    "user_id": "...",
    "user_email": "...",
    "pod_id": "...",
    "function_id": "...",
    "function_name": "...",
    "organization_id": "..."
  },
  "lemma_base_url": "...",
  "deadline_at": "..."
}
```

JOB execution additionally sends:

```http
Prefer: respond-async
```

The backend has already authorized and persisted the run before this request.
The manager key authenticates only the trusted backend-to-AgentBox hop. The
activity horizon prevents idle cleanup during a long JOB and is accepted only
on that authenticated route. AgentBox strips both private headers before the
sandbox. The runtime treats the bearer as the function's delegated SDK
credential and uses it for cold artifact retrieval or JOB completion; it does
not claim or introspect the run before warm execution.

The gateway URL is routing metadata, not an execution credential. Its host is
allow-listed so local ngrok/E2B development can use the same runtime source as
production.

## 6. API execution

1. Authorize, validate and persist a `PENDING` run with its revision and deadline.
2. Resolve the AgentBox endpoint, delegated token and organization concurrently.
3. In a short transaction, conditionally persist `PENDING -> RUNNING` and capture
   the complete immutable runtime context.
4. POST the v2 envelope directly to the resident runtime.
5. Runtime resolves the warm artifact and worker, executes and returns a terminal
   report.
6. Backend conditionally persists `RUNNING -> COMPLETED/FAILED` and returns that
   entity directly.

There is no Redis hop, runtime claim, runtime terminal callback or final database
reread on the synchronous API path.

If AgentBox reports that no active allocation can be resolved before forwarding
the request, the backend reruns readiness once and safely retries. Runtime run-ID
deduplication
joins the active task or returns its cached report. The backend never resolves a
different allocation after an ambiguous response. A second unconfirmed API
response is best-effort cancelled and marked failed.

## 7. JOB execution

1. Persist the immutable `PENDING` run and deterministic queue `job_id`.
2. Return `PENDING` to the caller.
3. The durable worker resolves the endpoint, identity and a function token whose
   real issuer expiry covers `deadline_at + 60 seconds`.
4. The backend conditionally persists `PENDING -> RUNNING`.
5. POST the same v2 envelope with `Prefer: respond-async`.
6. Runtime registers the deduplicated task and immediately returns `202`.
7. Runtime executes and reports:

```http
POST /internal/function-runtime/runs/{function_run_id}:terminal
Authorization: Bearer <same delegated function token>
```

The terminal route uses canonical authentication. It checks user, pod, function,
standard session ID and persisted revision before completing the run.

Terminal delivery retries only transport errors, throttling and 5xx responses,
bounded by the fixed 60-second callback grace. Authentication or state rejection
is not refreshed. The periodic reconciler handles callback loss and sandbox
death.

The configured JOB deadline defaults to 600 seconds and has a hard maximum of
3,000 seconds. Dispatch fails before `RUNNING` if a freshly minted token cannot
cover the deadline and callback grace. Longer jobs require a future isolated
runtime service identity or backend polling; refresh tokens and global sandbox
keys are not supported.

## 8. Runtime state and deduplication

The resident runtime holds only opportunistic, bounded state:

- a run-ID task/result registry;
- a content-addressed artifact cache;
- an LRU of revision-worker pools keyed by `(function_id, revision_hash)`;
- active run-to-worker mappings.

The deduplication signature covers function ID, run ID, exact revision, gateway,
the complete invocation envelope and synchronous/asynchronous mode. It does not
depend on token bytes, so an equivalent rotated token can recover the same
operation.

- An exact active API duplicate joins the task.
- An exact active JOB duplicate returns `202`.
- An exact completed duplicate returns its cached report or acknowledgement.
- Reusing a run ID with a different envelope is rejected.
- Active entries are never evicted; completed entries are bounded to 4,096.

Each revision worker imports one immutable revision, executes one call at a time,
binds fresh SDK `ContextVar` state, captures bounded output and stays warm.
Concurrent calls use separate workers up to the sandbox-wide safety ceiling.

Sandbox-local state is never authoritative and disappears with the allocation.

## 9. Delegated-token policy

The backend caches delegated function sessions for five minutes with
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

Five minutes is cache retention, not token validity. Each cached entry also stores
the issuer's actual access-token expiry. A caller may require
`min_validity_until`; an insufficient cached token is discarded and freshly
minted. Token plaintext is held only in process memory.

The token cannot create a public run: only the authenticated backend API creates
and starts durable run rows. Artifact and terminal routes reject other users,
pods, functions and revisions.

The same token is intentionally shared by concurrent runs for one
user/function/revision. Code that learns a sibling `run_id` could report its
terminal result. This is accepted under the documented same-pod mutual-trust
model. Preventing it requires a real supervisor/user-code isolation boundary,
not a fixed key in the current sandbox.

## 10. Cancellation and reconciliation

The backend first conditionally marks the public run `CANCELLED`, then calls:

```http
POST /functions/{function_id}/runs/{function_run_id}:cancel
```

The call carries the AgentBox manager key but no delegated function bearer.
AgentBox strips the manager key before proxying. The trusted route resolves the
current allocation; the runtime matches both function and run IDs, cancels the
task and kills/discards its worker process group.

Cancellation addresses the stable route for an existing logical allocation without
calling `ensure_sandbox`, so it never creates a sandbox. If the allocation is
gone, its work is already gone. A late callback is acknowledged as a terminal
duplicate and cannot overwrite `CANCELLED`.

Every run snapshots `deadline_at`. The reconciler:

- fails expired `PENDING` runs;
- fails expired synchronous `RUNNING` runs;
- gives asynchronous `RUNNING` runs the 60-second callback grace before failure.

## 11. I/O and failure rules

No SQLAlchemy session or pooled PostgreSQL connection remains open during:

- AgentBox calls or readiness waits;
- delegated-token minting;
- runtime HTTP;
- artifact/object-store I/O;
- organization lookup;
- sleeps or callback waits.

All orchestration uses:

```text
short database transaction
-> close
-> external I/O
-> short database transaction
```

There is no automatic business-level retry. AgentBox may repeat idempotent ensure
operations, the backend may retry one identical ambiguous invocation, and the
runtime may retry an idempotent terminal callback. Functions that mutate external
systems must use `function_run_id` or another application-level idempotency key.

## 12. Providers and verification

Docker and E2B run the same runtime source and protocol:

- Docker uses the function container command.
- E2B uses the template start command and waits for port `8090`.
- Function sandboxes have no persistent volume or auto-resume contract.
- Caches are opportunistic and disappear with the allocation.

Kubernetes must eventually run the same profile on an approved sandbox
`RuntimeClass`, without PVC, ingress or a user-visible service-account token.

Required verification covers typed unit tests, PostgreSQL transition races,
Docker and E2B API/JOB execution, exact token boundaries, schema prewarming,
revision reuse, cancellation, timeout, callback retry/loss, sandbox death,
dependency-heavy functions and production-shaped latency benchmarks.
