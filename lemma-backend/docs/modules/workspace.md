# Workspace module

The workspace module owns **machines**: user authorization over them, the
`/machines` API, conversation and session naming, short-lived machine
credentials, previews, app consents, compute metering, login protection, and
the translation of agent tools into calls on the sandbox runtime. It does not
own provider lifecycle or provider SDKs; those belong to the sandbox fabric.

What a machine is, how it is addressed and who may use it:
[docs/architecture/machines.md](../../../docs/architecture/machines.md).

The fabric underneath:

- [Sandbox design overview](../../../docs/architecture/sandbox/README.md)
- [Sandbox protocol](../../../docs/architecture/sandbox/sandbox-protocol.md)
- [Provider adapters](../../../docs/architecture/sandbox/provider-adapters.md)
- [Testing strategy](../../../docs/architecture/sandbox/testing-strategy.md)
- [Verification and rollout](../../../docs/architecture/sandbox/verification-and-rollout.md)

Do not add lifecycle, retry, provider, persistence, or execution protocol rules to
this module document. They belong in the canonical design set.

## Where things are

| Concern | Code |
| --- | --- |
| A machine, its ref, size, template and capabilities | `domain/machine.py` |
| Turning a ref into a machine, with authorization | `services/machine_resolver.py` (the only entry point), deciding by `domain/machine_access.py` |
| What a plan allows: sizes, templates, how many | `services/machine_policy.py` |
| Routes | `api/controllers/machines_controller.py` and the `machine_*_controller.py` beside it |
| Pod machines and their `machine.use` grants | `services/pod_machines.py` |
| Processes, run tags and services | `services/machine_processes.py` |
| Run slots and the disk janitor on pod machines | `services/run_slots.py`, `services/disk_janitor.py` |
| Previews and the subdomain proxy | `services/machine_previews.py`, `api/controllers/machine_preview_proxy.py` |
| Release, restart, reset scopes | `services/machine_lifecycle.py` |
| Compute metering and allowances | `services/compute_meter.py`, `services/compute_notices.py` |
| Login backup and restore | `services/login_backup.py` |
| Paths inside a machine | `lemma-backend/sandbox_runtime/paths.py` |

The routes from before machines are removed, not aliased: the caller's own
machine is `/machines/me`, and a port grant opens only under
`/public/machine-ports`. `scripts/check_stale_machine_terms.py` keeps the old
names out of code, docs and skills.

## Main data model

| Table | Meaning |
| --- | --- |
| `sandboxes` | A named sandbox's stable identity, independent of any running container: kind, owner, profile name/digest, desired state, epoch, storage generation, mounts, and the timestamps the idle sweep reads. A workspace row is a machine and also carries its `template`, `size`, `fabric`, `capabilities`, `visibility`, `billing_scope`, `run_slots` and `created_by`. A default machine's id is its user's id; `owner_id` is a user id for a default machine, a pod id for a pod machine or a function runtime, so it carries no foreign key |
| `sandbox_instances` | One concrete provider object backing a sandbox at a given epoch, unique per `(sandbox_id, epoch)`. Kept as its own row rather than columns on the sandbox so the destroy of an old container can be driven to completion while a new epoch is already serving |
| `machine_connections` | A person's own computer joined with the CLI: its owner, mode, the hash of its secret, the capabilities and home it reports, and when it was last seen or revoked. A machine row names it by `connection_id`, without a foreign key, so the row outlives a revoked connection |
| `machine_previews` | A port on a machine reachable at `p-<label>.<MACHINE_PREVIEW_DOMAIN>`, `private` or `link`, revocable before it expires |
| `app_machine_consents` | A person's yes to one app running code on their default machine; revoking it sets `revoked_at` |
| `machine_sessions` | A stretch of a machine running, metered into compute-unit slices in the usage ledger |
| `machine_login_backups` | Encrypted copies of the login-bearing parts of a machine's browser profile, kept off the machine |

`agents.machine` is the ref an agent's commands run on; unset means the default
machine of whoever the run acts for.

These rows are the durable half. Live process, session, and credential state
belongs to the sandbox runtime and Redis, and the lifecycle rules that move a
row between states are in the canonical design set above, not here.
