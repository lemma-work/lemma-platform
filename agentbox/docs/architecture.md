# AgentBox architecture and operations

## Responsibility boundaries

AgentBox separates provider lifecycle from runtime transport:

```text
lemma-backend
    |
    | authenticated manager API
    v
AgentBox lifecycle manager ---- SQLite or PostgreSQL state
    |
    | create / observe / suspend / delete / resolve endpoint
    v
provider adapter: Docker | Podman | Kubernetes | E2B | Daytona
    |
    | provider-authenticated HTTP and WebSocket endpoint
    v
lemma-agentbox-runtime
    +-- stateful Python session kernels
    +-- shell and TTY process groups
    +-- bounded function executor
    +-- browser and application ports
```

`SandboxLifecycleProvider` is the required provider contract. Optional narrow
protocols advertise suspension, bootstrap, endpoint-cache invalidation,
capabilities, capacity, and exact-instance orphan purge. The core HTTP and
WebSocket proxies consume `SandboxEndpoint`, including short-lived headers,
query parameters, subprotocols, expiry, and provider instance identity. They do
not import cloud SDKs.

Third-party providers can be registered programmatically, through the
`agentbox.providers` entry-point group, or by an explicit `module:factory`
provider value.

## Logical identity and lifecycle

The public `sandbox_id` is the stable, user-scoped logical identity. Durable
state separately records the provider-native ID and observed instance ID.
`PUT /sandboxes/{sandbox_id}` is an idempotent ensure: it resumes or recreates
compute as required and does not expose the provider ID to callers.

AgentBox resumes the exact provider-native instance when the adapter supports
stable stop/start identity. It never interprets an eventually consistent 404
or empty inventory as permission to create a replacement.

E2B and Daytona do not expose a documented idempotency key or client-selected
sandbox ID. Before their create call, AgentBox therefore persists an at-most-
once create intent and sends its allocation ID as `agentbox-generation`
metadata. Recovery filters by that exact token and then records the provider-
assigned ID. An unknowable create remains fenced and returns a retryable error;
it cannot create again merely because inventory is temporarily empty. This
chooses availability loss over duplicate user sandboxes. Active attempt
allocations are released only by an exact suspend/delete/purge operation, never
because a periodic inventory omitted them. Orphan deletion likewise keeps a
durable tombstone until deletion of the exact provider ID is confirmed.
Inventory-only `provider:<id>` capacity rows are discovery bookkeeping, not
create intent. They can never authorize adoption or protect a stray generation
from orphan cleanup; only an attempt-token allocation or the record's exact
provider ID can do that. One attempt token is not assumed to identify one
provider object: if duplicate native IDs carry the same token, only the native
ID durably bound to the sandbox/allocation is accepted and every other exact ID
is retained as an orphan. Allocation and orphan evidence from each provider
snapshot is published in one state-store transaction, serialized with
lifecycle claims; snapshots for a sandbox with a live claim are skipped.
Environment replacement exact-purges the durable ID, every allocation ID, and
every currently observed generation before publishing new environment state.
An exact E2B `DELETE /sandboxes/{sandboxID}` response of `404` is terminal: the
provider-generated ID is already absent, so the tombstone may be cleared. This
rule does not apply to list, status, connect, or endpoint-route misses, which
remain ambiguous under eventual consistency.
Daytona follows the same postcondition through an exact-ID lookup/delete; a
missing exact ID during permanent purge is terminal, while label-list absence
alone is never used as deletion proof.

E2B's structured gateway 502 body is not authenticated against sandbox code.
Provider lifecycle creation is therefore never replayed after dispatch. Normal
side-effecting app requests are retried only when a manager-owned pre-routing
failure proves they were not dispatched. Function admission is the deliberate
exception: it is a short POST keyed by a durable `run_id`, and the executor
joins duplicate admissions instead of running the function twice. Lemma polls
that run to preserve the synchronous API-function contract without holding a
long E2B route open for the full execution. Sandbox response headers cannot
grant replay permission.

Cloud bootstrap publishes a sandbox as ready only after both the runtime on
port 8080 and the function executor on port 8090 pass local and authenticated
provider-route health checks. A healthy runtime alone is not sufficient for
the AgentBox contract.

Suspension and permanent deletion have intentionally different meanings:

- `POST /sandboxes/{sandbox_id}/suspend` releases idle compute and retains the
  logical mapping. Adapters use each provider's release/resume primitive;
  capabilities report whether provider identity and filesystem survive that
  release, and provider-side retention limits still apply.
- Kubernetes cannot suspend a pod. Its release operation deletes compute; a
  later ensure creates a new pod/provider generation behind the same logical
  ID. Filesystem preservation must therefore come from an external volume, not
  the provider abstraction.
- `DELETE /sandboxes/{sandbox_id}` is a permanent purge. It removes exact
  provider generations and the durable mapping. When a cloud create outcome is
  still unknowable, DELETE retains a tombstone and returns a retryable error
  until the matching generation token can be observed and purged. Backend stop
  flows and routine idle cleanup use suspension instead.

Lifecycle claims prevent two manager revisions from creating or deleting the
same logical sandbox concurrently. Provider allocations give E2B and Daytona a
durable, environment-scoped capacity limit and double as create-intent tokens.
Reconciliation adopts only an exact native ID or matching generation token,
corrects allocations, and purges unknown orphans only after the configured
grace period.

## Provider capabilities

| Provider | Stable ID after release | Filesystem after release | Private-egress isolation | Authenticated HTTP/WS |
|---|---:|---:|---:|---:|
| E2B | yes | yes | yes | yes |
| Daytona | yes | yes | only with network/domain allowlists | yes |
| Docker / Podman | yes | yes | no built-in guarantee | no provider credential |
| Kubernetes | no (logical ID remains stable) | no built-in guarantee | yes, when cluster policy is installed | cluster-internal transport |

Capabilities are diagnostic facts, not permission to weaken a deployment. A
provider with insufficient network isolation should be rejected for untrusted
workloads unless the operator makes an explicit, reviewed exception.

## Workspace and ephemeral runtime state

`/workspace` is the fixed user-project root. Conversation work uses
`/workspace/c/{date}/{slug}`; function source and code-hash dependency caches
use `/workspace/pods/{pod_id}/functions/...`. The Lemma backend mirrors only the
path suffix into the separate pod filesystem (`/workspace/...` -> `/me/...`);
it does not treat `/me` and the sandbox disk as the same storage system.

E2B, Daytona, and Docker/Podman preserve `/workspace` across their release and
resume operations. Kubernetes does not preserve it without an external volume.
Browser credentials are not project data: Chromium profiles, agent-browser
session state, locks, and caches live under `/tmp` and are cleared whenever the
runtime starts after a compute restart. Never place browser credentials or
profiles under `/workspace`.

## State backend and secret boundary

SQLite is the default and suits a single local manager:

```bash
AGENTBOX_STATE_DB_PATH=/data/agentbox-manager/state.db
```

Use PostgreSQL for cloud deployments and overlapping revisions:

```bash
AGENTBOX_STATE_DATABASE_URL=postgresql://agentbox:...@postgres/agentbox
```

Schema migrations are versioned and additive. PostgreSQL migration application
uses an advisory transaction lock. Durable records include desired/observed
generations, provider identity, activity leases, lifecycle claims, allocations,
and orphan observations.

Sandbox environment is durable provider/template configuration, so it is
filtered through `AGENTBOX_STATE_DURABLE_ENV_KEYS` (default:
`LEMMA_BASE_URL`). Do not add tokens to that allowlist. Session environment is
dynamic: values such as `LEMMA_TOKEN`, user ID, pod ID, and organization ID are
sent to the runtime for the active session while state stores only their key
names. Function-invocation credentials are passed to the isolated child over a
private pipe rather than command arguments, files, or the child's initial
environment.

## Idle policy and retention

Runtime sessions and active operations keep a sandbox alive through heartbeats
and renewable activity leases. Cleanup never suspends a sandbox with an active
lease or operation.

The deployment policy for Lemma dev/prod is a three-minute idle window:

```bash
AGENTBOX_SESSION_IDLE_TIMEOUT_SECONDS=180
AGENTBOX_SANDBOX_IDLE_TIMEOUT_SECONDS=180
AGENTBOX_CLEANUP_INTERVAL_SECONDS=15
```

The local session default remains five minutes for compatibility; sandbox
compute defaults to the same three-minute idle window shown above. Suspended
state is retained for seven days by default, then permanently purged if it has
not been resumed:

```bash
AGENTBOX_SUSPENDED_RETENTION_SECONDS=604800
AGENTBOX_RECONCILE_INTERVAL_SECONDS=60
AGENTBOX_ORPHAN_GRACE_SECONDS=120
```

E2B and Daytona provider safety timers are backstops, not the primary idle
authority. Keep Daytona auto-stop disabled so a long function is not stopped
merely because the provider saw no external activity; its one-hour archive and
seven-day delete settings can remain leak protection.

## Cloud provider configuration

Every cloud provider requires an environment-scoped owner. Use different
values for dev and production so inventory and capacity never collide:

```bash
AGENTBOX_PROVIDER_OWNER=lemma
AGENTBOX_ENVIRONMENT=dev
```

### E2B

```bash
AGENTBOX_PROVIDER=e2b
E2B_API_KEY=...
E2B_SANDBOX_TEMPLATE=template-id
E2B_SANDBOX_MAX_ACTIVE=20
E2B_SANDBOX_TIMEOUT_SECONDS=3600
E2B_SANDBOX_ADMISSION_WAIT_SECONDS=60
E2B_SANDBOX_RETRY_AFTER_SECONDS=15
E2B_SANDBOX_CREATE_RATE_PER_SECOND=1
E2B_SANDBOX_CREATE_MAX_IN_FLIGHT=1
E2B_REQUEST_TIMEOUT_SECONDS=20
E2B_SANDBOX_STATUS_RETRY_SECONDS=15
E2B_RUNTIME_BOOTSTRAP_TIMEOUT_SECONDS=120
```

E2B uses provider-default egress and its native restricted-public-traffic mode,
so runtime and function ports require the E2B traffic token. AgentBox does not
apply the Docker/Kubernetes private-range policy to E2B-managed infrastructure;
`E2B_ALLOW_INTERNET_ACCESS` controls public internet access. Provider rate
limits honor `Retry-After` before returning a structured retryable error.
Control-plane requests are independently bounded so the one-hour sandbox
lifetime cannot be mistaken for an HTTP request timeout. Active AgentBox
heartbeats renew that lifetime through a throttled provider lease; idle
suspension preserves the E2B filesystem until retention purges it.

### Daytona

```bash
AGENTBOX_PROVIDER=daytona
DAYTONA_API_KEY=...
DAYTONA_SANDBOX_IMAGE=registry.example/lemma-agentbox-runtime@sha256:...
# Alternatively set DAYTONA_SANDBOX_SNAPSHOT, never both.
DAYTONA_SANDBOX_MAX_ACTIVE=20
DAYTONA_SANDBOX_AUTO_STOP_MINUTES=0
DAYTONA_SANDBOX_AUTO_ARCHIVE_MINUTES=60
DAYTONA_SANDBOX_AUTO_DELETE_MINUTES=10080
DAYTONA_NETWORK_ALLOW_LIST=203.0.113.10/32
DAYTONA_DOMAIN_ALLOW_LIST=api.example.com
```

Daytona cannot currently express "allow all public destinations but deny every
private destination." AgentBox therefore fails closed unless at least one
network/domain allowlist is configured. `AGENTBOX_ALLOW_UNSAFE_PRIVATE_EGRESS`
is an explicit escape hatch for reviewed trusted-only deployments; do not set
it for arbitrary user code.

### Docker, Podman, and Kubernetes

Docker/Podman use `AGENTBOX_RUNTIME_IMAGE`, `AGENTBOX_STORAGE_ROOT`, optional
`AGENTBOX_NETWORK`, and the CPU/memory limits in `agentbox.config.Settings`.
Podman additionally accepts `LOCAL_PODMAN_MACHINE_NAME`.

Kubernetes uses `AGENTBOX_NAMESPACE`, runtime class and node selector settings,
image pull policy, and resource request/limit settings. Private-egress isolation
depends on the cluster's network policy; the provider does not create a policy
for the operator.

## Runtime concurrency and cancellation

Each runtime session owns a persistent Python child process. Calls in the same
session are serialized to preserve notebook state; different sessions overlap.
Environment, cwd, stdout, and stderr are process-local. Timeout or session
deletion terminates that session's process group and resets only its Python
state. Shell and TTY commands also use distinct process groups so termination
does not affect another session.

Function API and JOB calls share one bounded admission controller. Configure
the per-sandbox ceiling and queue explicitly:

```bash
AGENTBOX_FUNCTION_MAX_CONCURRENCY=8
AGENTBOX_FUNCTION_MAX_QUEUED=32
```

Queueing counts against the requested timeout. Duplicate run IDs join or return
the original execution rather than repeating a side effect. When the queue is
full, the executor returns HTTP 429 with `Retry-After`. Each invocation runs in
its own process group with isolated output and environment; timeout,
cancellation, and job deletion terminate the group before returning terminal
state. Declared Python packages install into code-hash-specific directories with
atomic readiness markers instead of mutating one shared user site.

## Verification

Public CI installs every optional provider/state extra, runs Ruff, exercises all
non-e2e tests against a real PostgreSQL service, runs the AgentBox client suite,
and runs the real Docker provider contract against a runtime image built from
the current checkout. Podman and provider-account smoke tests remain manual or
approval-gated because they require an additional daemon or consume
E2B/Daytona resources.
