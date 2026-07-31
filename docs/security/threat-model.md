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
| PostgreSQL | Concurrent state changes, migrations, tenant schemas | Parameterized SQL, RLS, transactional outbox/inbox, uniqueness, ordered locks |
| Redis Streams and job queue | Duplicate, delayed, abandoned, or legacy messages | At-least-once delivery, deterministic IDs, 60-second reclaim, bounded retries, durable terminal outcome |
| Object storage and archives | Traversal, bombs, polyglots, partial uploads | Streaming limits, metadata inspection, type verification, cleanup |
| Provider and model APIs | Errors containing secrets, timeouts, hostile payloads | Server-side credentials, allowlisted error fields, retry classification, time/cost budgets |
| AgentBox/workspaces | User code and delegated tokens | Isolation, least privilege, encrypted session-bound cache, short expiry, cleanup |
| Generated SDK/browser bundles | Breaking or stale contracts | Committed OpenAPI source, reproducible generation, drift checks, semver-major releases |

## Primary threats and mitigations

- **Authorization bypass or tenant crossover:** all resource operations require
  the central authorization context; RLS and cross-session isolation tests are
  defense in depth.
- **Credential disclosure:** credentials stay server-side, secret-bearing
  fields are centrally redacted, workspace cache payloads are encrypted and
  session-bound, and canary-secret tests cover observable boundaries.
- **Lost or duplicate work:** domain state and outbox rows commit together;
  consumers use a durable per-consumer inbox, deterministic downstream job IDs,
  bounded retry, and a replayable dead-letter state.
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

At-least-once transports can repeat external I/O after a process dies between
the provider side effect and the inbox completion write. Provider idempotency
keys and deterministic job IDs are required where available. Already committed
pod-bundle steps are not compensated automatically; cancellation exposes
`PARTIALLY_CANCELLED` and its committed steps.

The architecture baseline is a debt ratchet, not proof that existing cycles are
safe. Existing violations remain tracked in `lemma-backend/docs/modules/issues.md`.
