# Function module

**Status:** Canonical the sandbox runtime-backed execution implemented; rollout verification in progress

The function domain owns definitions, immutable revisions, permissions, public
runs, schemas, durable attempts, scheduling, tickets, callbacks, cancellation,
and completion events. It uses the sandbox runtime only through the provider-neutral sandbox
and process API.

The canonical target design is:

- [Function execution](../../../docs/architecture/sandbox/function-execution.md)
- [Function execution benchmark](../../../docs/operators/sandbox-function-benchmark.md)
- [Sandbox design overview](../../../docs/architecture/sandbox/README.md)
- [Sandbox protocol](../../../docs/architecture/sandbox/sandbox-protocol.md)
- [Testing strategy](../../../docs/architecture/sandbox/testing-strategy.md)
- [Verification and rollout](../../../docs/architecture/sandbox/verification-and-rollout.md)

Do not duplicate function runtime, retry, queue, provider, credential, or artifact
rules here. They belong in the canonical design set. There is no separate legacy
execution service or fallback path.

## Main data model

| Table | Meaning |
| --- | --- |
| `functions` | Pod-scoped definition: name, description, input/output/config schemas, code path, and the revision hash currently promoted |
| `function_revisions` | One built, executable revision. The artifact and source bytes were always content-addressed; this row is what makes them findable, and it snapshots the schemas so promoting an old revision restores the contract its code actually implements |
| `function_runs` | One durable attempt: input/output, status, logs, error, deadline, and job handle. The foreign key to `functions` is `SET NULL`, because a run is the record of what the function did and must outlive the definition; the delete path refuses while any run is still in flight |

The table list is inventory, not a runtime contract. Attempt, retry, queue, and
artifact rules live in the canonical design set above.
