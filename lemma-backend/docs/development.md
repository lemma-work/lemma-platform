# Backend development guidelines

Engineering conventions for `lemma-backend`. Each rule below is enforced by
existing code you can copy from — the pointers name the canonical example.
Setup, stack, and run instructions live in the [README](../README.md).

## DB sessions and connections

The pool is small (defaults: 10 + 10 overflow per process; see
`app/core/infrastructure/db/session.py`). One rule covers almost everything:

> **Never hold a DB session across external I/O or a streaming body.**
> External I/O = sandbox calls, LLM calls, connector/HTTP requests, storage
> walks, sleeps/retries. Do the DB work in a short unit of work, commit,
> release, THEN do the slow thing.

How to comply, by situation:

- **Streaming/SSE endpoints**: build the auth context and do the DB resolve
  inside `pod_context_scope()` / `current_context_scope()`
  (`app/core/authorization/scope.py`) — they commit and release the connection
  *before* you yield the streaming response. Canonical:
  `conversation_controller.py` `send_message` / `stream_conversation`.
- **Resolve → external call → persist**: use the two-phase saga — one short
  scope to resolve + authorize, no scope (or a fresh bare one) for the external
  call, another short scope to persist the outcome. Canonical:
  `connector_operation_use_cases.py`.
- **Multi-phase executors**: take a `uow_factory` and open a fresh short UoW
  per DB step (status update, terminal write). Canonical:
  `function_dispatcher.py` and `function_runtime_gateway.py` — neither holds a
  connection across the sandbox runtime, object-storage, identity, or wait operations.
- **Agent tools**: one short UoW per tool call, committed on clean exit.
  Canonical: `pod_data_access.py::pod_services`.
- **Message-bus handlers**: `uow_factory` per message, never a held session.

Backstops (already configured, don't remove): pool-utilization warning at 80%,
startup connection-budget check, and a 60s `idle_in_transaction_session_timeout`
enforced by Postgres (`db/session.py`).

An authorization `Context` carries an `Authorizer` bound to the session that
built it. That is fine *because* contexts are built inside short scopes — do
not stash a `Context` in anything that outlives its scope (singleton, module
global, background task).

## Caching

- **All data caching goes through Redis** (`RedisJsonCache`,
  `app/core/infrastructure/cache/`) so replicas and workers see invalidations.
  In-process caching is acceptable only for immutable object singletons
  (`lru_cache`) and crypto key material.
- TTLs are config-driven (`app/core/config.py`), one setting per cache.
- **Degrade gracefully, loudly**: Redis being down must never fail the request
  — treat it as a cache miss (or, for approval-style stores, as "not
  approved", the safe direction) and `logger.warning` so the outage is visible
  before it becomes DB pressure. Canonical: `app/core/authorization/cache.py`,
  `app/core/authorization/session_approvals.py`.
- **Invalidate conservatively**: over-clearing only costs a re-derivation;
  stale authorization is never acceptable. The role-snapshot cache clears its
  whole prefix on role mutations by design.

## Errors and logging

- **Never hide an error.** An error nobody can see is how unexplained behaviour
  in production stays unexplained. Every caught exception is logged with what
  actually went wrong — the message and the traceback (`exc_info=True`), not a
  type name — before it is turned into a fallback, a retry, or a returned error
  result.
- **Log it where it can be seen.** Production runs at `LOG_LEVEL=INFO`, so
  `logger.debug` for a caught failure is the same as not logging it at all. Use
  `logger.warning` when the run survives in a degraded state and
  `logger.error` when it does not. Reserve `logger.debug` for genuine
  diagnostics: an expected cancellation, a policy decision, an exception that is
  re-raised for the caller to log.
- **The one exception is volume.** A failure that fires per-token or per-row
  drowns the signal it was added for. Log those once per run, per batch, or on a
  transition, with a count — never per occurrence, and never by dropping the
  level to hide them.
- **Returning an error to the model is not logging it.** A tool that answers
  `{"success": false, "error": ...}` has told the agent; it has told nobody
  operating the system. Do both.
- **A bare `except: pass` or `suppress(Exception)` around real work is a bug.**
  `scripts/check_swallowed_errors.py` ratchets these — `silent-broad-catch`,
  `debug-only-broad-catch`, and `cancellation-blind-catch` — so the count can
  only go down.
- **Name the event for what happened**, and keep the suffix honest, since it is
  how the catalog reads: `.degraded` for something lost or fallen back,
  `.propagated` for re-raised, `.observed` for an outcome worth recording,
  `.diagnostic` for genuine debug detail. New events must be regenerated into
  `app/core/log/event_catalog.py` (`make quality` checks it), and a field named
  `stack` is silently dropped by structlog — pick another name.
- **Never log a secret or a user's message text.** See Secrets below; error
  strings reach both the log and, for agent tools, the user-visible transcript,
  so route free text through `app/core/redaction.py`.

## Authorization model (summary)

Two ledgers decide everything:

1. **Human roles** — org/pod role bundles resolve to permission ids for USER
   actors.
2. **Workload grants** — named agents/functions/workflows start with ZERO
   access and act on exactly the resources granted to them
   (`resource_permission_grants`, name-keyed in bundles). **Grant-first**: a
   workload's explicit grant is standalone authority; the invoking user's role
   is consulted only for PERSONAL ownership, org-scoped resources, and
   data-layer scoping (RLS, `/me`). The default pod agent is the opposite — it
   mirrors the invoking user's pod permissions and holds no grants.

**Destructive actions** (`DESTRUCTIVE_ACTIONS`,
`app/core/authorization/delegation.py`) are the carve-out: no workload —
default pod agent included — performs them by default. Unlocks: an explicit
grant of the destructive permission (standing authority, works headless) or a
user session approval (`APPROVE_FOR_SESSION` → Redis store in
`session_approvals.py`, keyed `(conversation, workload, permission)`, TTL
`session_approval_ttl_seconds`).

Frequent deny codes: `MISSING_WORKLOAD_RESOURCE_GRANT` (grant the workload),
`DESTRUCTIVE_ACTION_REQUIRES_APPROVAL` (approve or grant),
`INSUFFICIENT_PERMISSION` (human role problem), `DELEGATION_SCOPE_VIOLATION`
(minimal-scope token used outside its operation), `PERSONAL_RESOURCE_DENIED`
(privacy trumps grants). The full model, with payload examples, is documented
for pod builders in `lemma-skills/lemma-builder/references/authorization-model.md`.

Implication map: `execute ⊃ read` for agents/functions/workflows and
`write/delete ⊃ read` families live in `IMPLIED_PERMISSIONS`
(`permissions.py`) — grant checks, SQL projections, and listings all honor it
via `equivalent_permission_ids`.

## Secrets

API keys and tokens are pydantic `SecretStr` end to end. Reveal plaintext only
at the point of use (`reveal_secret` / `reveal_credentials`), never in logs,
never in serialized payloads.
