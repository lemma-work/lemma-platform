# Function module

**Status:** Canonical AgentBox-backed execution implemented; rollout verification in progress

The function domain owns definitions, immutable revisions, permissions, public
runs, schemas, durable attempts, scheduling, tickets, callbacks, cancellation,
and completion events. It uses AgentBox only through the provider-neutral sandbox
and process API.

The canonical target design is:

- [Function execution](../../../docs/design/agentbox/function-execution.md)
- [Function execution benchmark](../../../docs/operators/agentbox-function-benchmark.md)
- [AgentBox overview](../../../docs/design/agentbox/README.md)
- [Sandbox protocol](../../../docs/design/agentbox/sandbox-protocol.md)
- [Testing strategy](../../../docs/design/agentbox/testing-strategy.md)
- [Verification and rollout](../../../docs/design/agentbox/verification-and-rollout.md)

Do not duplicate function runtime, retry, queue, provider, credential, or artifact
rules here. They belong in the canonical design set. There is no separate legacy
execution service or fallback path.
