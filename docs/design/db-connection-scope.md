# Database connection scope

## The model

A pooled Postgres connection is held for exactly as long as an `AsyncSession`
is open. Everything else follows from that.

The old model treated the pool as a budget to be divided: every process got a
slice, `worker_concurrency` had to fit inside its slice, and the cluster-wide
total was `replicas × (pool_size + max_overflow)` checked against
`POSTGRES_MAX_CONNECTIONS` at startup. That arithmetic assumed a task holds a
connection for its whole lifetime. It doesn't — sessions are taken per unit of
work — so the arithmetic was constraining the wrong thing, and it stopped being
true at all the moment replica count became elastic.

The model now:

- **`DB_POOL_SIZE` is a hard per-process ceiling.** `max_overflow` is pinned to
  0, so a process opens at most `DB_POOL_SIZE` connections per engine. A
  deterministic ceiling is what makes capacity predictable under an autoscaler;
  overflow connections are also discarded on return, so they are the expensive
  kind.
- **Pool size is sized from concurrent in-flight queries**, by Little's Law:
  `queries_per_second × seconds_per_query`. An agent run is 95%+ non-database
  latency, so a worker at `WORKER_CONCURRENCY=50` needs single-digit connections
  in steady state. The pool absorbs the burst at task start and finish.
- **`WORKER_CONCURRENCY` is a RAM and CPU budget**, unrelated to the pool.
- **The cluster-wide ceiling is enforced at the server**, with
  `ALTER ROLE <app> CONNECTION LIMIT n`, not by arithmetic in application
  config. Postgres refusing connection *n+1* is a guarantee; a startup log line
  is not.
- **Pressure is reported from measurement.** The `database_pool_capacity`
  incident in `app/core/infrastructure/db/session.py` fires on sustained
  checkout saturation.

Two server-side timeouts bound the damage when something goes wrong anyway:
`idle_in_transaction_session_timeout` catches a session held open while *not*
querying, and `statement_timeout` catches one held open *by* a query.

## What has to stay true

The trade above is only safe while a session is never held across non-database
work. `make lint-session-scope` (`scripts/check_session_scope.py`) enforces it,
ratcheted against `session-scope-baseline.json` — the baseline may shrink
freely, anything new fails the build. It reports four things:

| Rule | Meaning |
| --- | --- |
| `non-db-await` | An `await` on HTTP, object storage, Redis, an event publish, a job enqueue, a model call, a sandbox operation, a thread offload or a sleep, while a session is open |
| `session-across-yield` | A session held across a `yield` — how a streaming endpoint pins a connection for the length of the response |
| `nested-session` | A second session opened while one is already held; costs two connections for one unit of work and self-deadlocks a saturated pool |
| `async-for-non-db` | The same as `non-db-await`, spread over an iterator |

Rules carrying a `/request-scoped` suffix are held by FastAPI's
`Depends(get_uow)` for the whole request rather than by an `async with` in the
handler — same cost, different fix.

### What the gate cannot see

It is a precision-first tool, and the limits are deliberate:

- Calls are resolved **by name**, with no type inference. Propagation of "this
  leaves the process" is therefore restricted to function names with exactly
  one definition in the tree. A service method named `send`, `execute` or
  `create_surface` — defined on several classes — is not followed. An earlier
  revision without this restriction produced 555 findings, most of them
  nonsense (it decided `conn.execute` was an HTTP call).
- It does not model conditionals, so a slow call on a branch that never
  executes still counts.

So a clean run means *no new violations of the shapes it can see*, not a proof.
It is a ratchet, not a verifier.

## Known debt

An audit of the agent and agent-surfaces HTTP paths (2026-08-14) found these,
all of which predate this document and none of which are fixed here. They are
recorded in the baseline. Every one is a connection held across outbound
platform I/O under FastAPI's request-scoped UoW:

| Path | Held across | Worst case |
| --- | --- | --- |
| `GET /pods/{id}/surfaces` (and single-surface routes) | Slack/Telegram/WhatsApp/MS Graph identity lookup, once per surface, sequential | 6s × surfaces |
| `POST`/`PATCH /pods/{id}/surfaces` | Telegram webhook registration with retry backoff, scheduler and Composio calls — inside an **open transaction**, after the row is written | tens of seconds |
| `POST /pods/{id}/surfaces/{name}/send` | Platform message send | up to 60s |
| `GET /pods/{id}/surfaces/{name}/channels` | Paginated Slack `conversations.list` | up to 60s |
| `POST /pods/{id}/notifications` | Delivery HTTP, per candidate channel until one succeeds | tens of seconds |
| Slack modal fast lane in `POST /surfaces/webhooks/{platform}` | 1–3 Slack Web API calls, holding **two** connections (nested session) | up to 60s each |
| `POST /organizations/{id}/agent-runtime-profiles` | DNS resolution + `GET {base_url}/models` against a caller-supplied host | 10–15s |
| `POST /pods/{id}/conversations/{id}/stop` | Scheduler HTTP call with **no client timeout** | unbounded |
| `persist_managed_bot` | Telegram `setWebhook` with retries, inside an open transaction holding row locks | tens of seconds |

The fix is the same shape in every case, and
`managed_bot_configurator.py` already demonstrates it: load what you need in a
short session, close it, do the outbound work, reopen a short session to persist
the result.

Two structural items behind them:

- `get_uow` (`app/core/api/dependencies.py`) is a FastAPI yield-dependency, so
  every handler using `UoWDep` — directly or through a service dependency —
  holds a connection for the entire request including the response body. Fine
  for a handler whose duration *is* its query time; the trap is that nothing
  stops a slow call being added later. The handlers that stream already avoid it
  by taking `get_uow_factory` instead.
- `SchedulerAPIClient` (`app/modules/schedule/scheduler/api_client.py`) creates
  an `aiohttp.ClientSession()` with no timeout.

## Pooler compatibility

Nothing here requires a middle-tier pooler, but two invariants keep one usable
without a rewrite, and both are worth having regardless:

- **No session-level state.** Always `SET LOCAL`, never bare `SET`. A bare
  `SET search_path` leaks this pod's schema onto the connection for whoever
  borrows it next — that was a real bug in `file_chunk_repository.py`.
- **Only transaction-scoped advisory locks** (`pg_advisory_xact_lock`), which
  release at commit. A session-scoped `pg_advisory_lock` would survive into the
  next borrower's work.

If a transaction-mode pooler is ever put in front, note that `asyncpg` needs
PgBouncer ≥ 1.21 with `max_prepared_statements` set to a non-zero value;
otherwise prepared-statement names collide across multiplexed backends.
