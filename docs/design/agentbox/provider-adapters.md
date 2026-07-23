# AgentBox Provider Adapters

**Status:** Proposed design; Docker and E2B implemented, Kubernetes deferred

**Parent:** [AgentBox](README.md)

**Protocol:** [Sandbox protocol](sandbox-protocol.md)

**Provider test contract:** [Testing strategy](testing-strategy.md)

## 1. Purpose

This document maps the portable AgentBox ports onto Docker, Kubernetes, and E2B.
Adapters are allowed to use different data-plane transports. They are not allowed to
change public semantics, invent their own retry policy, persist secrets, or expose a
provider ID to callers.

The implementation target is one adapter package per provider with explicit
lifecycle, process, Python, filesystem, port-access, inventory, and admission
components. An aggregate adapter may delegate several ports to one component, but
one monolithic provider class is not required or preferred.

## 2. Capability matrix

| Capability | Docker | Kubernetes | E2B |
| --- | --- | --- | --- |
| Workspace files survive release | Named volume | Per-workspace PVC | Native sandbox persistence |
| Process/Python state survives release | No contract | No | Not exposed as a contract |
| Function persistent storage | Forbidden | Forbidden | Forbidden |
| Native foreground/background exec | Engine exec | Pods exec | Commands API |
| Native reconnectable process handle | Partial | No | Yes |
| Native PTY reconnect | Partial | Connection-scoped | Yes |
| Native stateful Python contexts | No | No | Yes |
| Native file API | Archive API is insufficient | No | Yes |
| Workspace control runtime required | Yes | Yes | No |
| Provider-native auto-resume | No | No | Yes, workspace only |
| Strong production isolation | No | With approved RuntimeClass | Managed microVM boundary |
| Public ingress for functions | Disabled | Disabled | Disabled |
| Provider create rate admission | Configured local limit | Configured pool/resource limit | E2B project limit |

The portable contract is the intersection required by the selected profile, not the
lowest capability of every provider. Provider-only capabilities are implementation
optimizations and cannot become implicit caller requirements.

## 3. Shared adapter rules

### 3.1 Metadata and naming

Every physical allocation carries:

```text
managed-by=agentbox
environment=<environment>
owner=<deployment owner>
workload-kind=workspace|function
logical-id=<uuid>
allocation-id=<uuid>
allocation-token=<uuid>
profile-digest=sha256:<hex>
```

Docker labels, Kubernetes labels/annotations, and E2B metadata contain the same
logical evidence. Values that exceed provider label restrictions move to annotations
or metadata, while the allocation token remains queryable.

Provider resource names derive from the workload kind plus a collision-resistant
suffix, not directly from untrusted input:

```text
ab-w-<logical-id-prefix>-<allocation-id-prefix>
ab-f-<logical-id-prefix>-<allocation-id-prefix>
```

The full UUIDs remain in metadata. Names are never used as proof of ownership.

### 3.2 Provider call discipline

- Each control-plane call is bounded by the remaining caller deadline and an
  adapter-specific maximum request timeout.
- Create is dispatched once per allocation token.
- Exact-ID inspect, connect, release, and delete may be retried only under the
  shared error contract.
- Inventory is paginated and runs only in reconciliation/operations paths.
- Provider response bodies and error headers are normalized once at the adapter
  boundary.
- Provider handles may be cached in process memory, but durable correctness uses
  allocation and provider IDs from PostgreSQL.
- Adapter shutdown closes SDK clients and streaming connections without destroying
  allocations.

### 3.3 Readiness

Readiness is profile-specific:

- workspace: generic execution, Python context, and filesystem access work;
- function: a native exec can start the immutable function launcher;
- port access is validated lazily when requested and does not block sandbox
  publication.

No adapter probes a workload-specific HTTP execution port. No adapter treats provider object state
alone as proof that required data-plane operations are ready.

### 3.4 Runtime artifacts

All providers consume artifacts from one release manifest:

```text
agentbox_release_id
workspace_profile_digest
function_profile_digest
workspace_runtime_protocol_version
function_runtime_abi
docker_workspace_image_digest
docker_function_image_digest
kubernetes_workspace_pod_template_digest
kubernetes_function_pod_template_digest
e2b_workspace_template_build_id
e2b_function_template_build_id
```

AgentBox starts only a profile whose provider artifact matches the configured
release manifest. Mutable image tags are rejected outside local development.

## 4. Private workspace runtime

Docker and Kubernetes use a small `agentbox-workspace-runtime`; E2B does not.

The runtime provides adapter-private equivalents of the process, Python-session,
filesystem, and health operations. It excludes function execution, queues, artifact
installation, provider lifecycle, browser proxying, and durable business state.

Requirements:

- one runtime per workspace allocation;
- authenticated private HTTP/2 or Connect transport plus WebSocket/binary stream for
  PTY/output;
- per-allocation random credential delivered at creation, read once, removed from
  process environment, and never inherited by children;
- request authentication before body buffering;
- user commands run as the unprivileged sandbox user;
- Python contexts are separate child processes, one per session;
- commands and PTYs are separate process groups;
- bounded output and sequence-numbered reconnect buffers;
- atomic filesystem operations constrained to allowed roots;
- clean shutdown terminates all managed descendants;
- no listening public interface and no provider credential.

Network policy permits the AgentBox manager to reach the runtime. User processes may
share the sandbox network namespace but do not possess its credential. Compromise of
the runtime affects that user's workspace, which is already the sandbox trust
boundary; it must not grant access to AgentBox, the provider API, another sandbox,
or host control sockets.

## 5. Docker adapter

### 5.1 Scope and client

Docker is for local development, conformance, and trusted single-tenant deployments.
It is not accepted as a hostile multi-tenant production isolation boundary.

Use an asynchronous Docker Engine HTTP client over the configured Unix socket or
explicit TLS endpoint. Do not invoke the `docker` CLI with subprocesses. The manager
must never mount the Docker socket into a user sandbox.

Docker's Engine API models exec as create followed by start and supports attached
stdin/stdout/PTY streams. See the
[Docker Engine API](https://docs.docker.com/reference/api/engine/).

### 5.2 Workspace allocation

Create:

1. reserve a provider allocation in AgentBox PostgreSQL;
2. create or reuse the named volume recorded for the logical workspace storage row;
3. create the container from the workspace image digest;
4. mount the volume at `/workspace` and no host project paths;
5. on the installed stack, attach the container to the manager's private network
   without publishing runtime or app ports; a standalone development fallback may
   publish required ports on `127.0.0.1` with random host ports;
6. apply CPU, memory, PID, and ephemeral-storage limits;
7. drop Linux capabilities and prohibit privilege escalation;
8. start the container and wait for the private workspace runtime;
9. run one authenticated process/filesystem readiness operation;
10. bind the exact container ID to the allocation and the exact volume ID to the
    logical workspace storage row.

The container entrypoint starts only static workspace services. Dynamic session
credentials are passed per runtime operation.

Release:

1. AgentBox performs portable quiescence through the workspace runtime;
2. revoke port grants;
3. stop the exact container with a bounded grace period;
4. retain the named volume and container metadata;
5. release active-compute admission.

Resume starts the exact stopped container when its image/profile still matches.
Otherwise AgentBox removes the old container, retains the volume, creates a new
container from the current profile, and increments the allocation epoch.

Permanent deletion removes the exact container and then the exact volume from the
logical workspace storage row. The tombstone is finalized only after both are
confirmed absent. Volume deletion never uses a broad name prefix.

### 5.3 Function allocation

- Create an ephemeral container from the function image digest.
- Mount only tmpfs/ephemeral writable paths; do not create or attach a named volume.
- Disable published ports.
- Start with an idle entrypoint that keeps the allocation ready for Engine exec but
  contains no workload-specific HTTP server.
- Launch `lemma-function-runtime execute` with an Engine exec instance.
- The trusted launcher detaches the invocation child, records its internal attempt
  reference, and returns an acknowledgment before the Engine exec stream closes.
- Inspect/cancel use `lemma-function-runtime inspect|cancel --operation-id ...`
  through exact exec operations when the original Engine exec handle is unavailable.
- Remove the container after five idle minutes or profile drain.

The operation ID is stored in AgentBox before exec create. Docker exec IDs and the
runtime's attempt reference are persisted after acknowledgment.

### 5.4 Files and port access

Workspace filesystem operations use the private workspace runtime, not shell/base64
commands. Docker archive APIs may be used for bulk import/export only after path and
symlink validation.

User port access goes through an AgentBox signed reverse proxy. On the installed
stack, the manager reaches the container IP over their shared private network and
the sandbox port is not host-published. A standalone development fallback may proxy
to a loopback-bound random host port. Raw host ports and Docker container addresses
are never returned to the backend or user.

### 5.5 Inventory and recovery

List containers and volumes by exact `managed-by`, owner, and environment labels.
Unknown-create recovery matches the allocation token and then records the exact
container ID. A container with matching logical ID but a different token is never
adopted.

## 6. Kubernetes adapter

### 6.1 Scope and client

Use `kubernetes_asyncio` for API operations, watches, exec streams, and cancellation.
Do not wrap the synchronous Kubernetes client in worker threads. Watch a specific
resource version for Pod/PVC state transitions instead of polling every second.

Kubernetes Pods are disposable. A replacement Pod has a different UID and is a new
physical allocation even when the logical ID remains unchanged. See
[Kubernetes Pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/).

Production function and untrusted workspace profiles require an approved sandbox
`RuntimeClass`, initially gVisor or Kata, on a dedicated tainted node pool. Plain
runc is permitted only for disposable development/conformance clusters.

### 6.2 Workspace storage

Create one dynamically provisioned PVC per logical workspace:

```text
access mode: ReadWriteOnce by default
mount: /workspace
owner labels: workload kind, logical ID, environment
deletion policy: retained across Pod deletion; deleted on logical permanent delete
```

The PVC lifecycle is independent of an individual Pod, which is the required
portable filesystem guarantee. See
[Kubernetes persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/).

PVC creation is its own exact resource operation and survives unknown Pod creation.
The adapter never substitutes a different workspace's PVC by name or label.

### 6.3 Workspace Pod

The Pod template contains:

- one workspace container using an immutable image digest;
- `/workspace` PVC mount;
- ephemeral `/tmp` and runtime-state volumes;
- no Docker/container runtime socket;
- no writable hostPath;
- `automountServiceAccountToken: false`;
- nonprivileged security context, seccomp, capability drop, PID/resource limits;
- startup/readiness probe for the private workspace runtime;
- labels for network policy and exact allocation metadata;
- required `RuntimeClass`, node selector, and tolerations in production.

AgentBox connects to the private runtime through the Pod IP on the cluster network.
A default-deny NetworkPolicy permits ingress only from the AgentBox manager identity
and egress according to the workspace profile. No Service or Ingress is created for
the control runtime.

Release quiesces the runtime and deletes the exact Pod while retaining the PVC. The
allocation becomes released only after the Pod UID is absent. Resume creates a new
Pod against the same PVC and increments the allocation epoch. Permanent deletion
deletes the Pod and then the exact PVC.

### 6.4 Function Pod

The function Pod differs deliberately:

- no PVC;
- no workspace runtime control service;
- no Service, Ingress, public port, or service-account token;
- immutable function image digest;
- ephemeral writable root/cache with strict quota;
- production sandbox RuntimeClass and dedicated node pool;
- default-deny ingress;
- direct egress only to the Lemma runtime gateway and controlled egress gateway;
  the egress gateway enforces revision-declared public HTTPS destinations.

The Pod remains alive waiting for native exec. The adapter starts a short launcher
using the Kubernetes exec WebSocket transport. The launcher validates the operation
ID, detaches `lemma-function-runtime execute`, writes attempt status under a private
ephemeral runtime directory, and exits only after acknowledgment. Execution then
continues independently of the exec connection and reports through the runtime
gateway.

Inspect/cancel run trusted `lemma-function-runtime inspect|cancel` commands via
Kubernetes exec. The runtime targets the exact attempt process group. AgentBox does
not depend on Kubernetes exec reconnect for result delivery.

The Pod is deleted after five idle minutes, when drained, or after an unrecoverable
sandbox failure. A later run creates a fresh Pod and downloads exact artifacts again.

### 6.5 Readiness and watches

Pod phase alone is insufficient. The adapter waits for:

- scheduled Pod and running container;
- readiness condition for workspace runtime or function launcher capability;
- matching Pod UID and allocation labels;
- no terminating timestamp;
- PVC bound/mounted for workspace profiles.

Watch expiration restarts from the latest resource version under the same deadline.
It does not start another Pod. `409 AlreadyExists` triggers exact resource inspect
and allocation-token validation.

### 6.6 Inventory and recovery

Inventory is namespace- and label-scoped. A Pod or PVC is adopted only when its full
allocation token matches a durable create attempt. Exact UID deletion/not-found is
the terminal compute postcondition. A missing list result is not.

## 7. E2B adapter

### 7.1 SDK and templates

Use the official asynchronous E2B SDK and separate immutable templates:

- `workspace-python-v1` derives from or includes the E2B Code Interpreter runtime
  needed for code contexts;
- `function-python-v1` contains only the stateless function launcher/runtime.

Static processes are started and proven ready during template build. E2B snapshots
the running process into the template, so no per-sandbox bootstrap loop is required.
Create-time environment is not visible to template start commands and therefore
must contain no startup dependency. See
[E2B start and ready commands](https://e2b.dev/docs/template/start-ready-command).

Provider create uses the current official async SDK and pins its tested version in
the release lock:

```python
await AsyncSandbox.create(
    template=<exact profile template>,
    metadata=<allocation metadata>,
    secure=True,
    network=<profile policy>,
    lifecycle=<workload-specific lifecycle>,
    timeout=<bounded initial timeout>,
)
```

The documented create request supports secured access, lifecycle, outbound network,
and public-traffic controls; see the
[E2B create-sandbox API](https://e2b.dev/docs/api-reference/sandboxes/create-sandbox).

No AgentBox runtime or workload-specific execution HTTP port is exposed through E2B.

### 7.2 Workspace lifecycle

Use full-memory pause and auto-resume:

```python
lifecycle={
    "on_timeout": "pause",
    "auto_resume": True,
}
```

AgentBox—not the provider timeout—is the five-minute logical idle scheduler. It
updates logical activity on accepted operations, then its distributed idle worker
claims and quiesces the workspace before calling pause. Before an explicit or idle
release, AgentBox:

1. blocks new work;
2. deletes Code Interpreter contexts;
3. kills managed commands and PTYs;
4. stops managed browser/app processes;
5. clears ephemeral credentials and browser state;
6. calls exact sandbox pause.

The E2B timeout is a longer safety bound than the five-minute logical threshold, and
delegated credentials expire before that bound. If E2B auto-pauses first during an
AgentBox outage, reconciliation does not publish that allocation directly as clean:
it fences new work, connects/resumes internally, performs the same quiesce/scrub,
and pauses it again before normal ensure may return it. This keeps provider timeout
from bypassing the portable release contract.

Thus the E2B snapshot contains the static clean template/runtime state plus workspace
files, not live delegated credentials. Although E2B can preserve process memory,
portable callers are promised files only.

With auto-resume enabled, native commands, file operations, and authenticated tunnel
traffic can resume a paused sandbox. An adapter operation may use its cached handle;
otherwise it connects by exact provider ID. It does not call list/status first.
E2B documents that normal pause preserves filesystem and memory, connection resumes
the same sandbox, and auto-resume is valid only for full-memory pause. See
[persistence](https://e2b.dev/docs/sandbox/persistence) and
[auto-resume](https://e2b.dev/docs/sandbox/auto-resume).

Paused E2B sandboxes are retained indefinitely by the provider, so AgentBox's
seven-day logical retention worker must explicitly kill them.

### 7.3 Workspace profile replacement

An E2B sandbox filesystem is native to that physical sandbox, unlike a Docker
volume or Kubernetes PVC. Replacing a workspace profile therefore uses an explicit
two-allocation migration:

1. quiesce the old allocation and keep it as the current fenced allocation;
2. reserve temporary replacement capacity and create the new template allocation
   with a new allocation token;
3. enumerate `/workspace` through native file APIs and build a manifest containing
   path, type, mode, size, and content digest;
4. stream allowed regular files and directories into the new allocation without
   passing whole files through shell/base64 commands;
5. reject path or symlink escapes and verify the destination manifest and digests;
6. run the new profile readiness checks;
7. transactionally bind the storage row and logical sandbox to the new allocation,
   increment the allocation epoch, and only then destroy the old sandbox.

If copy or verification fails, the old allocation remains current and recoverable;
the incomplete replacement is destroyed. New operations cannot observe the new
allocation before the atomic bind. This is the only E2B path that migrates files
between sandbox IDs; ordinary pause/auto-resume reconnects the same exact sandbox.

### 7.4 Workspace execution

Map ports directly:

| AgentBox port | E2B API |
| --- | --- |
| Foreground/background process | `sandbox.commands.run` |
| Reconnect/inspect/list | command PID, `commands.connect`, `commands.list` |
| stdin | native command stdin API |
| PTY create/reconnect/input/resize/kill | `sandbox.pty` |
| Python session | Code Interpreter code context |
| Files | `sandbox.files` |
| Port access | secured `get_host` route plus traffic access token |

E2B's command APIs expose background handles, PID reconnect, list, kill, stdin, and
custom process tags; its PTY API supports reconnect and resize. See
[E2B background commands](https://e2b.dev/docs/commands/background),
[Process Start API](https://e2b.dev/docs/api-reference/process/start), and
[interactive PTY](https://e2b.dev/docs/sandbox/pty).

Use `operation_id` as the provider process tag when supported. Persist the PID only
after acknowledgment. On ambiguous start, search the exact sandbox's process list
for that tag; never submit another start solely because the PID response was lost.

One E2B Code Interpreter context maps to one AgentBox session. Context cwd matches
the session cwd; context restart maps to session restart. See
[E2B code contexts](https://e2b.dev/docs/code-interpreting/contexts).

### 7.5 Function lifecycle and execution

Function E2B sandboxes are stateless and never paused:

```python
lifecycle={
    "on_timeout": "kill",
    "auto_resume": False,
}
```

On allocation readiness, use one native command smoke test of the immutable launcher.
For each attempt:

1. set the sandbox timeout to at least `attempt deadline + termination grace`;
2. start the native E2B process with separate command/arguments, `background=true`,
   `tag=operation_id`, and `stdin=true`;
3. send the single-use ticket through stdin and close stdin;
4. persist PID/tag after acknowledgment;
5. rely on runtime callbacks for normal completion;
6. use exact PID/tag inspection for reconcile/cancel;
7. after the final attempt, set the idle timeout to five minutes;
8. kill exact sandbox after five idle minutes.

No heartbeat extends the timeout. A single timeout update protects a long JOB. No
function result is polled through an E2B public route.

### 7.6 Network and ports

Workspace public app traffic uses E2B secured access and short-lived AgentBox grants.
Raw traffic tokens are encrypted in AgentBox state only when a grant must survive a
manager restart and are never returned to backend callers.

Function sandboxes set public traffic false. Their provider network permits direct
connections only to the Lemma runtime gateway, the controlled egress gateway, and
the required controlled resolver. The egress gateway enforces the attempt's
revision-declared public HTTPS destinations. If the selected E2B template/account
cannot enforce this upstream restriction, the function profile is rejected rather
than weakened.

### 7.7 Create ambiguity, capacity, and events

E2B assigns the sandbox ID and does not document a client idempotency key. AgentBox
therefore commits the allocation token before create and includes it in metadata.
An ambiguous create is resolved through:

1. signed `sandbox.lifecycle.created` webhook metadata;
2. metadata-filtered sandbox listing as background fallback;
3. exact-ID binding once one object is found.

The create call is never repeated for that token. Webhook handlers verify signatures
and deduplicate delivery IDs. See
[E2B lifecycle webhooks](https://e2b.dev/docs/sandbox/lifecycle-events-webhooks)
and [listing sandboxes](https://e2b.dev/docs/sandbox/list).

Provider project concurrency and create rate are AgentBox admission inputs. E2B 429
responses update one distributed provider-scope `blocked_until`; adapter-local retry
loops are forbidden. See [E2B billing and limits](https://e2b.dev/docs/billing).

## 8. Provider conformance declarations

Adapters do not self-assert production capability. A release manifest records the
conformance evidence for each `(provider, profile digest, protocol version)`:

```text
test run ID and commit
provider/account/cluster class
portable capability results
provider-specific lifecycle results
security profile result
warm/resume/cold latency distributions
create rate and concurrency observations
known deviations
expiry/revalidation date
```

AgentBox startup in production rejects a profile/provider combination without a
passing, nonexpired conformance record unless an explicit audited override exists.
