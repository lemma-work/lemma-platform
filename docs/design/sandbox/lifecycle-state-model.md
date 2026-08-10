# The sandbox runtime Lifecycle State Model

**Status:** Implemented

**Parent:** [the sandbox runtime](README.md)

## Decision

The sandbox runtime durably stores control-plane lifecycle facts only. Process handles,
interactive Python sessions, execution output, and stream cursors are data-plane
facts owned by one live manager and one physical allocation incarnation.

This is intentionally not an exactly-once execution system. If the sandbox runtime loses the
outcome of non-idempotent work, it reports that ambiguity and does not replay the
work. The caller or agent decides whether a new operation is appropriate.

## Why the previous model failed

The previous schema copied transient provider state into PostgreSQL:

- `processes`
- `sessions`
- `python_executions`

Those rows could not restore the provider's output buffer, interpreter memory,
stdin channel, watchdog, or manager callback after a restart. They therefore
looked durable without making the underlying operation durable. Stale rows also
blocked release and cleanup, and execution history grew without being a business
record or a reliable source of truth.

Durability is useful only when it can support a real recovery invariant. The sandbox runtime
keeps the create-attempt journal because an ambiguous provider create can leak an
entire billable sandbox and can be reconciled by allocation token. It does not keep
an execution journal because provider process/Python state cannot be safely
reconstructed or replayed from such a row.

## The five durable tables

| Table | Why it exists | Retention |
| --- | --- | --- |
| `sandboxes` | Desired logical resource, selected profile, current allocation, generation, epoch, activity protection, and maintenance claim | Lifetime of the logical resource/tombstone |
| `allocations` | Exact provider object, immutable profile, generation, epoch, state, and admission ownership | Bounded operational history |
| `allocation_create_attempts` | Write-ahead record for reconciling an ambiguous provider create without issuing it twice | Same as its allocation |
| `workspace_storage` | Ownership and deletion of logical workspace storage; for E2B this explicitly records allocation-native storage | Lifetime of the workspace/tombstone |
| `provider_admission` | Provider-scope capacity and rate-limit accounting | Lifetime of the provider scope |

No environment value, stdin, source code, Python result, process output, PID, or
stream cursor is stored in PostgreSQL.

The first rollout is expand-only: the old runtime tables remain physically present
but are no longer mapped, read, or written. This keeps the migration compatible
with the N-1 manager image that is still serving while migrations run. After the
new manager has baked in production, a separate contract migration drops those
ignored legacy tables and any obsolete historical rows they contain. The durable
model is five tables throughout; the temporary physical shells are rollout
compatibility, not current state.

## Three different identities

The model does not treat these values as interchangeable:

1. `resource_generation` changes whenever desired lifecycle/profile intent changes.
   A delayed provider completion from an older generation cannot publish or
   resurrect a sandbox.
2. `allocation_epoch` changes when a physical allocation becomes current. A handle
   from an older allocation cannot be redirected to the replacement.
3. manager incarnation is implicit in the in-memory routing caches. Restarting the
   manager loses process and Python handles by design.

Every lifecycle completion performs a generation compare-and-set before
publication. Every runtime operation validates allocation ID and epoch before
calling the provider.

## Runtime failure contract

| Situation | Result | Automatic replay |
| --- | --- | --- |
| Provider definitively rejected work before it began | `SAFE_SAME_OPERATION` | Allowed by an explicit caller retry |
| Sandbox is provisioning or temporarily capacity-limited | `WAIT` | Allowed after the supplied delay |
| Process/session handle is absent after manager restart | `PROCESS_NOT_RUNNING` or `ALLOCATION_CHANGED` | No |
| Allocation ID or epoch changed | `ALLOCATION_CHANGED` | No |
| Process start or Python execution may have begun but its response was lost | `UNKNOWN_DISPATCH` | No |
| `stdin` delivery fails or its target PID no longer matches the operation | `PROCESS_NOT_RUNNING`/non-retryable failure | No |

`stdin` and stateful Python execution are non-idempotent. Replaying either after an
ambiguous network failure can duplicate input or mutations, so the sandbox runtime never marks
them safe for an automatic retry.

After an ambiguous Python execution, the sandbox runtime best-effort removes the exact
interpreter context. If cleanup succeeds, a later explicit agent retry may create a
fresh context; that retry can repeat an earlier unconfirmed side effect, but it
cannot race a second live interpreter. If cleanup cannot be confirmed, an
allocation-aware tombstone fences new interpreter creation on that allocation
until it changes or the tombstone expires. After a manager restart, the E2B adapter
removes interpreter contexts not owned by the new manager before admitting a fresh
Python session.

Function business runs remain durable in the backend's function-run model. The sandbox runtime
does not duplicate that record. A lost runtime invocation outcome is surfaced to
the backend; the backend does not transparently invoke the operation again.

## Bounded in-memory state

The manager keeps:

- at most 64 deadline-retained process routing/results records, each with a 2 MiB
  output ceiling;
- at most 32 Python execution results for same-incarnation request dedup, with the
  backend requesting at most 1 MiB per result;
- at most 512 live/tombstoned Python session handles with one-hour idle expiry;
- provider-specific output buffers and watchdogs.

These structures improve a healthy manager's local UX. They are not recovery
promises. Different Python sessions may run concurrently, while calls within one
stateful session serialize.

The data-plane manager is deliberately single-replica and single-process. Requests
are not safe to load-balance across independent manager memories. Deployments must
enforce one active manager replica and one application worker; lifecycle
reconciliation may be separated or scaled only after it no longer hosts runtime
routes. A future horizontally scaled data plane requires explicit owner routing,
not restoring the deleted execution tables.

Live operation records are retained until their absolute request deadline. When
the bounded cache is full of unexpired records, new work receives
`CAPACITY_EXHAUSTED`; the sandbox runtime never evicts a live idempotency record and then
replays the same operation ID.

A stateful Python interpreter has a fixed working directory. Conversations have one
stable resolved cwd today, shared by shell and Python. Backend session identity
also includes that cwd, so if the product later allows a conversation cwd change,
both runtimes move together and Python cannot silently reuse a kernel rooted in the
old directory.

## Lifecycle and storage rules

- Provider calls never run inside a database transaction.
- `protected_until` is the only lifecycle protection needed for active filesystem,
  process, Python, or port work. It has an absolute bound and cannot poison cleanup
  forever.
- Release, destroy, reset, and profile replacement increment
  `resource_generation` before provider I/O.
- A stale completion may clean up only its exact provider object. It cannot publish
  itself as current.
- A manager reservation that never crossed the provider dispatch boundary is
  reclaimed after 30 seconds. Once provider create may have run, an empty inventory
  response never proves absence and the allocation token remains fenced.
- Superseded `DRAINING` allocations are provider-finalized by exact ID before their
  admission ownership is released.
- E2B activity-driven automatic resume is disabled, which E2B requires anyway for
  the filesystem-only snapshots the sandbox runtime uses. This does not make resume
  exclusively the sandbox runtime's to schedule: `connect()` resumes a paused sandbox
  implicitly, so the control plane observes a resume and assigns the new
  allocation epoch rather than gating the transition.
- The E2B workspace timeout is refreshed on every runtime operation. E2B's
  `timeout` is a continuous-runtime ceiling rather than an idle timer, so without
  a refresh the provider stops a busy workspace mid-session. Refreshing makes the
  provider's pause an inactivity backstop consistent with the sandbox runtime idle release.
- E2B workspace storage is sandbox-native. It is co-located with that allocation;
  a replacement must not pretend the files were independently preserved. This is
  why a workspace profile change tolerates drift rather than replacing the
  allocation — see [the sandbox runtime](README.md) §6.
- A workspace pause is filesystem-only. Files persist; running processes and
  interpreter state do not, and callers must not treat them as recoverable.
- Permanent deletion and recoverable compute release are separate operations.

## Provider evidence behind the model

E2B documents sandbox persistence, explicit pause/resume, and filesystem-only
snapshot behavior:

- [Sandbox persistence](https://e2b.dev/docs/sandbox/persistence)
- [Automatic resume](https://e2b.dev/docs/sandbox/auto-resume)
- [Filesystem-only snapshots](https://e2b.dev/docs/sandbox/filesystem-only-snapshots)
- [Lifecycle events](https://e2b.dev/docs/sandbox/lifecycle-events-api)

The generation/claim approach follows the same reconciliation principles as
Kubernetes controllers, finalizers, and leases:

- [Controllers](https://kubernetes.io/docs/concepts/architecture/controller/)
- [Finalizers](https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/)
- [Leases](https://kubernetes.io/docs/concepts/architecture/leases/)

These sources do not imply provider-specific behavior in the sandbox runtime's public API.
They support the core separation: durable desired state and fenced reconciliation
belong in the control plane; one runtime incarnation's mutable execution state does
not.
