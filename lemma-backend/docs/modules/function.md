# Function module

**Status:** Canonical the sandbox runtime-backed execution implemented; rollout verification in progress

The function domain owns definitions, immutable revisions, permissions, public
runs, schemas, durable attempts, scheduling, tickets, callbacks, cancellation,
and completion events. It uses the sandbox runtime only through the provider-neutral sandbox
and process API.

The canonical target design is:

- [Function execution](../../../docs/design/sandbox/function-execution.md)
- [Function execution benchmark](../../../docs/operators/sandbox-function-benchmark.md)
- [Sandbox design overview](../../../docs/design/sandbox/README.md)
- [Sandbox protocol](../../../docs/design/sandbox/sandbox-protocol.md)
- [Testing strategy](../../../docs/design/sandbox/testing-strategy.md)
- [Verification and rollout](../../../docs/design/sandbox/verification-and-rollout.md)

Do not duplicate function runtime, retry, queue, provider, credential, or artifact
rules here. They belong in the canonical design set. There is no separate legacy
execution service or fallback path.
