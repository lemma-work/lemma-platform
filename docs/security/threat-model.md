# Lemma backend threat model

## Assets

The backend protects user identity and authorization sessions, connector and
OAuth credentials, delegated workspace tokens, pod data and files, agent and
workflow inputs/outputs, usage limits, executable function/app content, and the
integrity of asynchronous work.

## Trust boundaries

| Boundary | Untrusted input | Required controls |
| --- | --- | --- |
| Public API and webhooks | Headers, bodies, multipart files, provider events | Authentication/authorization, request and module size limits, validation, stable provider IDs, redaction |
| PostgreSQL | Concurrent state changes, migrations, tenant schemas | Parameterized SQL, RLS, work items written with their cause, uniqueness, ordered locks |
| Work runtime (`work_items`) | Duplicate, delayed, abandoned, or poison work | Leases with heartbeat and reclaim, dedup keys, per-pod caps, bounded retries, `dead` with the domain state terminal |
| Object storage and archives | Traversal, bombs, polyglots, partial uploads | Streaming limits, metadata inspection, type verification, cleanup |
| Provider and model APIs | Errors containing secrets, timeouts, hostile payloads | Server-side credentials, allowlisted error fields, retry classification, time/cost budgets |
| Sandboxes and workspaces | User code and delegated tokens | Isolation, least privilege, encrypted session-bound cache, short expiry, cleanup |
| Generated SDK/browser bundles | Breaking or stale contracts | Committed OpenAPI source, reproducible generation, drift checks, semver-major releases |

## Primary threats and mitigations

- **Authorization bypass or tenant crossover:** all resource operations require
  the central authorization context; RLS and cross-session isolation tests are
  defense in depth.
- **Credential disclosure:** credentials stay server-side, secret-bearing
  fields are centrally redacted, workspace cache payloads are encrypted and
  session-bound, and canary-secret tests cover observable boundaries.
- **Lost or duplicate work:** domain state and the work it causes commit
  together; a handler's database effects commit with the item's completion,
  under its lease, so a reclaimed item cannot apply them twice; retries are
  bounded and a dead item is kept for an operator to retry.
- **Resource exhaustion:** request, upload, archive-entry, decompressed-size,
  image-dimension, queue-batch, retry, and execution limits are explicit.
- **Arbitrary code execution:** connector schema snippets use a restricted AST
  parser; user function/app execution belongs in the sandbox boundary.
- **Quota races:** all applicable usage scopes are created, locked in canonical
  order, evaluated, and reserved in one transaction.
- **Supply-chain compromise:** dependency review, CodeQL, secret scanning,
  dependency audit, and container scanning run in CI. Generated code is pinned
  to a committed specification.

## Residual risks

A work item can repeat external I/O when its process dies between the provider
side effect and the transaction that completes the item. Provider idempotency
keys and deterministic dedup keys are required where available. Already committed
pod-bundle steps are not compensated automatically; cancellation exposes
`PARTIALLY_CANCELLED` and its committed steps.

A person's workspace sandbox is one Linux user shared by every session of every
agent of theirs, so a restricted agent can read another session's environment
and files, the pod assistant's delegated token among them. That token counts
for `WORKSPACE_DELEGATED_TOKEN_TTL_SECONDS` (15 minutes) and not at all once its
conversation ends; the runtime and its Python workers start from the image's
interpreter with `-I`, so nothing in the home is imported ahead of their code;
and E2B's saved Python namespace is sealed per session. Still open: same-user
reads of a live token, shims planted in the home (`~/.profile`, `~/.python`,
`~/.local/bin`) that pick up every fresh token, the runtime overlay in
`/opt/lemma-runtime`, which the sandbox user can write and which persists on
Docker, and the signed-in browser, whose CDP, VNC and X11 endpoints take any
local client. A Linux user per agent closes the first three; the browser needs
a service of its own.

The architecture baseline is a debt ratchet, not proof that existing cycles are
safe. The violations it tolerates are enumerated in
`lemma-backend/architecture-baseline.json` and enforced by `make architecture`,
which fails on any growth.
