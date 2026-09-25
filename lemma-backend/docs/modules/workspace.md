# Workspace module

**Target status:** the sandbox runtime proposed; not yet implemented

The workspace module will own user authorization, conversation/session naming,
short-lived workspace credentials, and translation of agent tools to the sandbox runtime. It
will not own provider lifecycle or provider SDKs.

The canonical target design is:

- [Sandbox design overview](../../../docs/architecture/sandbox/README.md)
- [Sandbox protocol](../../../docs/architecture/sandbox/sandbox-protocol.md)
- [Provider adapters](../../../docs/architecture/sandbox/provider-adapters.md)
- [Testing strategy](../../../docs/architecture/sandbox/testing-strategy.md)
- [Verification and rollout](../../../docs/architecture/sandbox/verification-and-rollout.md)

Do not add lifecycle, retry, provider, persistence, or execution protocol rules to
this module document. They belong in the canonical design set.

The current backend code still uses the experimental the sandbox runtime API until the coordinated
breaking migration described in the rollout document.

## Main data model

| Table | Meaning |
| --- | --- |
| `sandboxes` | A named sandbox's stable identity, independent of any running container: kind, owner, profile name/digest, desired state, epoch, storage generation, mounts, and the timestamps the idle sweep reads. `owner_id` is a user id for workspaces and a pod id for function runtimes, so it carries no foreign key |
| `sandbox_instances` | One concrete provider object backing a sandbox at a given epoch, unique per `(sandbox_id, epoch)`. Kept as its own row rather than columns on the sandbox so the destroy of an old container can be driven to completion while a new epoch is already serving |

These two rows are the durable half. Live process, session, and credential
state belongs to the sandbox runtime and Redis, and the lifecycle rules that
move a row between states are in the canonical design set above, not here.
