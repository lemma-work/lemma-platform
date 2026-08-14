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
  leaves the process" follows a name only when every definition sharing it is
  slow — `execute` is 21 definitions and mostly queries, so it stays unusable,
  while `refresh_credentials` is 4 definitions that are all thread offloads. An
  earlier revision propagated through any name and produced 555 findings, most
  of them nonsense (it decided `conn.execute` was an HTTP call).
- It does not model conditionals, so a slow call on a branch that never
  executes still counts.

So a clean run means *no new violations of the shapes it can see*, not a proof.
It is a ratchet, not a verifier — which is why there is also a runtime detector.

## The runtime detector

`app/core/observability/connection_scope.py` measures the same property from
the other end, where there is nothing to infer. A connection is checked out for
some wall-clock span; some of that span executes statements; the longest
contiguous stretch with no statement running is time the connection was held
while the database sat idle. That stretch *is* the bug, directly observed,
whatever code shape produced it.

It is the sibling of `stall_sampler.py`, which answers the same question for
the event loop, and is built the same way — one small class, one structured
event (`runtime.connection_scope.degraded`), a cooldown, and a `reports`
counter so a test can assert rather than sleep and hope.

Three design points worth knowing before changing it:

- **The trigger is one contiguous gap, not summed idle time.** A session
  issuing a hundred quick queries with ordinary Python between them accumulates
  a second of summed idle and is doing nothing wrong. One contiguous gap is
  exactly one `await`, which is the sentence the detector exists to say.
- **Time spent querying is never counted.** A slow query is
  `db_statement_timeout_seconds`' problem, not this one's.
- **`handle_error` is load-bearing.** `after_cursor_execute` does not fire when
  a statement raises (verified), so without it the failed statement's interval
  never closes and every later gap hides behind it.
- **The stack field must not be called `stack`.** It is reserved — structlog's
  renderers pop it and handle it themselves, so the value is dropped with no
  error. The event catalog does not catch this: it checks that emitted fields
  are *expected*, so listing `stack` in the EventSpec made catalog and emitter
  agree about a field that never arrived. Both detectors shipped that way and
  reported a dozen 1000ms+ stalls with no culprit in them. It is `stack_frames`.

### Reading a hold report

A hold is a connection whose statements are far apart. That is the symptom of
two different diseases, and the report alone does not separate them:

1. The code holding it did slow non-DB work between statements — the bug this
   document is about.
2. **The event loop was stalled by something else**, so every in-flight
   connection's next statement was late. The holder is a victim.

Check the loop-stall count for the same run before attributing a hold. A
function like `record_repository.list_records` — `SET LOCAL`, a count, a select,
no `await` on anything else — cannot produce a 681 ms gap between its own
statements by itself; something else stopped the loop. Fixing the holder in
that case changes nothing.

It works under `NullPool` — the testing default — because checkout and check-in
fire there exactly as on a real pool. That is what makes
`strict_connection_scope` (in `app/modules/test_support/connection_scope.py`)
possible without any engine juggling. In production it warns; in a test that
names the fixture, it fails with the stack of the code that took the
connection.

Naming the culprit took three attempts, all corrected by running it rather than
reasoning about it, and the wrong answers are worth knowing because they are the
obvious ones:

1. `traceback.extract_stack()` — SQLAlchemy's async layer runs pool listeners
   inside a greenlet it spawns, so this returns the greenlet's own stack: a few
   frames of SQLAlchemy internals and no caller at all.
2. Walking the task's `cr_await` chain — only meaningful while a task is
   *suspended*. Check-in runs synchronously inside the greenlet, so the task is
   running, so the chain is empty. Measured on a four-deep call chain: one frame.
3. Walking the **parent greenlet's** frames — correct. Same four-deep chain:
   eleven frames, three of them the ones a developer needs.

### How much a clean sweep actually proves

`LEMMA_CONNECTION_SCOPE_REPORT=1` runs the monitor over a whole pytest session
and writes what it saw, grouped by site. Early results:

| Suite | Tests | Holds |
| --- | --- | --- |
| pod e2e | 97 | 1 |
| datastore + apps e2e | 98 | 4 (3 sites) |
| datastore records e2e, run alone | 17 | 0 |
| real sandbox + real LLM, before the fixes | 80 | 95 (7 sites) |
| real sandbox + real LLM, after | 80 | 86 (9 sites) |

The two real-execution rows are the ones that mattered, because they are the
only runs where the slow calls are real. They drove two fixes and measured
them:

| Site | Before | After |
| --- | --- | --- |
| `build_user_context` | 59 holds, worst 2784 ms | 6 holds, worst 309 ms |
| `record_controller.bulk_create_records` | (hidden behind the above) | fixed: 2291 ms held for 64 ms of querying |

A real-LLM agent run holds **no** connection across the model call — 6 passed
with zero holds attributed to the agent path. `AgentRunnerService` was already
correct, and now there is evidence rather than a reading of the code.

That last row is the important one. The same file contributed a 546 ms hold in
the combined run and none on its own, so those were **cold-start artifacts** —
first-touch model loading and schema setup — not per-request holds.

Which sets the real limit on this evidence: **the hermetic e2e suite mocks
exactly the calls that cause the worst holds.** `E2E_LLM_MODE=mock` replaces the
model with a `FunctionModel`, and platform sends are stubbed. A clean sweep
therefore proves the database-only paths are clean and that the *mocks* are
fast. It says nothing about whether the code would still hold a connection if
the Slack call took four seconds.

So a `connection_scope` test should not rely on a collaborator happening to be
slow. It should **inject a delay into the collaborator** and assert no
connection is held across it — that tests the structure (was the session closed
before the call?), which is the property that actually has to hold, and it
tests it in the hermetic suite where the real call never happens.

## The audit

Every module was audited (2026-08-14) against both defect classes — a
connection held across non-database work, and work that blocks the event loop.
**~103 findings: 35 CRITICAL, 31 HIGH, 12 MEDIUM, 16 LOW.** Both gates were
green at the time, in every slice.

They were green because the static gate had six structural blind spots, all now
closed. Worth recording, because each was invisible until something looked:

| Blind spot | What it cost |
| --- | --- |
| `SessionUnitOfWorkFactory(...)()` not recognised as a session open | 20+ sites in `app/composition`, several holding an open write transaction across an HTTP download |
| `ctx.uow()` not recognised | the entire streaq worker surface |
| Session-yielding context managers (`pod_services`, `uow_scope`, …) not recognised | 71 session-holding blocks treated as ordinary code |
| Propagation required exactly one definition of a callee name | everything reached through an interface |
| Third-party SDK calls invisible — no definition in `app/` for a name lookup to find | every SuperTokens / aiohttp / Redis call |
| `dependencies=[...]` in a route decorator never read | the pod_bundle SSE routes |

Session-yielding context managers are now **discovered rather than listed**, so
the next such helper is covered the day it is written.

### Structural findings

- **190 of 271 route handlers (70%) hold a connection for the whole request**
  via `Depends(get_uow)`, directly or through a service dependency. Most are
  harmless — a handler that only queries holds it about as long as it needs.
  The problem is that holding is the **default**, so a slow call added later
  costs a connection with nothing at the call site to say so. That is exactly
  how the surface-route findings happened. The fix is targeted rather than a
  global flip; the gate is what stops new ones appearing.
- **53% of thread offloads bypass the named limiters** (28 `asyncio.to_thread`
  + 9 bare `anyio` against 33 `run_blocking`), so `OFFLOAD_*_LIMIT` governs
  under half the traffic it names. `asyncio.to_thread` uses asyncio's *default
  executor* — a different pool from anyio's, shared with every `getaddrinfo`
  the process makes, and untouched by the headroom `configure_thread_pool()`
  raises at startup. Enforced now by `make lint-io-hygiene`.
- **11 `aiohttp.ClientSession()` built with no timeout.** aiohttp's default is
  **five minutes** (httpx's is five seconds). Where the caller holds a DB
  session, it parks a pooled connection for that long. Also enforced by
  `make lint-io-hygiene`.

### The worst individual finding

`app/core/security.py` — `verify_auth` is a global dependency on every request,
and SuperTokens' `get_session` reaches a **synchronous `requests.get`** on the
event loop, under a threading mutex, with no negative cache for an unknown
`kid`. `kid` is read from the token *before* signature verification, so an
unauthenticated client can force one blocking round trip per request with a
forged JWT.

### Fix pattern

The same shape in nearly every case, and
`app/modules/connectors/application/connector_operation_use_cases.py` is the
reference implementation: resolve DB state in a short scope, close it, do the
outbound work with no connection held, reopen a short scope to persist. Other
in-tree references: `managed_bot_configurator.py`,
`whatsapp_mobile_verification.py`, and `schedule/handlers/schedule_consumer.py`
(whose docstring states the rule, and whose datastore sibling violated it).

## The loop half

`app/modules/test_support/loop_scope.py` is the sibling gate. `stall_sampler`
(from #349) already does the hard part — it watches from an OS thread, so it
runs *during* a stall and captures the stack of the call that is blocking
rather than the scaffolding around it. The fixture adds a verdict and a tick to
watch, and `make test-connection-scope` runs both.

It exists because `make lint-async` cannot see this class of bug at all: the
ruff ASYNC rules know the synchronous I/O primitives and nothing about CPU. A
per-character loop over a document, thirteen regex passes over 8 MiB, or a chunk
walk whose iteration count the uploader chooses are all invisible to it.

Two findings fixed and pinned this way:

- **Function terminal logs** redacted the whole of stdout+stderr — up to 8 MiB,
  thirteen regex passes — and then kept the first 4 MiB. Half the work was spent
  on text nobody would see. It now trims first, with a margin past the limit so
  a credential straddling the final cut is still inside the window the patterns
  ran over, and the whole thing is offloaded.
- **Icon PNG validation** walked the container's chunk list, and a chunk
  declaring length 0 advances only its 12-byte header — so a file of nothing but
  empty chunks costs `len(data)/12` passes. Measured at **73 ms** of
  uninterruptible loop for 5 MiB, from an upload, and the dimension limits could
  not help because they are checked after the walk returns. Now bounded at 4096
  chunks, which no real icon approaches.

Both tests assert the property rather than the implementation: the redaction one
asserts no part of the credential survives (not that the `[REDACTED]` marker
lands in a particular place, which is arithmetic), and the PNG one asserts wall
clock with a wide margin, because the cost *is* the wall clock. Each was checked
against the unfixed code — the PNG walk reproduces the audit's 73 ms exactly.

## What the stalls actually were

The first sweep with stall stacks intact (they had been silently dropped — see
the reserved-name note above) reported four stalls over a second. Exactly one
was a production bug. The taxonomy is worth keeping, because three of the four
look alarming in a dashboard and are not worth anyone's afternoon:

| Stall | Cause | Verdict |
| --- | --- | --- |
| 1027 ms | `insert(...).values(rows)` in `stage_domain_events` recompiling per batch size | **real, fixed** |
| 1025 ms | SQLAlchemy logging every row at DEBUG during `fetchall` | DEBUG-only |
| 1045 ms | `subprocess.run` removing containers in pytest teardown | test harness |
| 1065 ms | `inspect.get_annotations` at import | cold start |

The real one: `.values(list)` renders every row inline, so the compiled SQL is a
function of the row count and the statement cache misses on every batch size it
has not seen. Recompiling landed on the loop *inside* the write transaction,
holding the locks of the write that produced those events. Executemany
parameters compile once for every size.

The DEBUG one cannot happen in production: `_dependency_floor_applies` holds
`sqlalchemy.engine` at WARNING whenever the configured level is INFO. At DEBUG
it logs a line per row, and `LOG_QUIET_DEPENDENCIES=1` is the existing opt-out.

The lesson for the next person reading a stall report: check whether the
deepest frames are ours before doing anything. Three of these four were the
harness or the interpreter warming up.

## A regression this caught, which is the point of having it

`_bulk_write_records` converts each chunk's returned rows into entities and
domain events. Batching all of that until after the last execute is tidier and
measurably worse -- the worst gap went from 784 ms to 2163 ms. The events must
be staged in the same transaction for the outbox to be atomic, so the work
cannot leave it; collecting it turns N short gaps into one long one.

The detector triggers on the longest *contiguous* gap, because that is the
stretch actually holding row locks. Optimising for fewer gaps instead of shorter
ones makes the number of incidents go down and the damage go up.

## What authorization costs

Authorization runs on essentially every API request and every agent tool call,
so its cost is multiplied by the busiest number in the system. Measured against
real Postgres and Redis by
`app/modules/pod/tests/e2e/test_authorization_cost_e2e.py`, on a pod created
through the API so the principal has genuine membership:

| Regime | median | p95 | DB queries |
| --- | --- | --- | --- |
| cold — snapshot miss, derives from DB | 14.96 ms | 21.07 ms | several |
| warm — Redis snapshot hit, new `Context` | 1.51 ms | 2.82 ms | **0** |
| ↳ of which `build_user_context` | 1.38 ms | | |
| repeat — decision cache, same request | 0.0003 ms | 0.0005 ms | 0 |

Warm is the regime almost every request is in, and nearly all of its 1.5 ms is
`build_user_context`: one Redis GET plus deserializing the snapshot into
frozensets. The decision itself is ~0.12 ms of in-memory set logic.

The zero-query result is the point, and it is asserted rather than observed.
The pod-scoped snapshot key omits the organization (see `_snapshot_suffix`)
precisely so the lookup needs no row read to build the key; keyed the other way,
every pod request paid a `Pod` read *at a 100% cache hit rate*.

That file also carries the counterweight test: a pod the principal has no
membership in must still be denied. Every other assertion there rewards making
authorization skip work, and a cache key loose enough to serve one pod's
snapshot for another would improve all of them while breaking the system.

Two adjacent hazards worth knowing:

- **An unknown permission id denies silently.** `"pod:read"` (colon) is not
  `pod.read` (dot); the first simply returns `False` with no error. Fails
  closed, which is safe, but invisible. Use the `Permissions` constants.
- **Cold is ~10× warm**, which is what justifies the snapshot cache existing.
  It is paid once per principal per `authorization_role_cache_ttl_seconds`.

## What does not work: committing the request UoW from a handler body

The obvious fix for a request-scoped hold is to commit the unit of work once the
handler has finished reading, so the connection goes back before model
validation, serialization and the write to the client. Tried on
``GET /pods/{}/functions/{}/runs/{}`` -- the top hold site at seventeen holds --
and it fails:

```
RuntimeError: Attempted to exit a cancel scope that isn't the current
              task's current cancel scope
```

A/B against the same commit: without the release the test passes in 79s, with it
the run dies. The unit of work is a FastAPI yield-dependency whose ``async with``
was entered by the dependency solver's exit stack; committing it from inside the
handler body unwinds that scope in a different task context than the one that
entered it.

Note the distinction from ``_release_after_authorization``, which does the same
``await uow.commit()`` and is fine: it runs *inside a dependency*, during
dependency resolution, not from the handler body afterwards. That is why the
authorization release works and this one does not.

So the remaining request-scope holds need a different mechanism -- a
release-aware dependency, or moving the read into a scope the handler owns
outright -- not a commit bolted onto the end of a handler. Anything that looks
like the snippet above should be assumed broken until an e2e run says otherwise.

## Known debt

**APScheduler drives a synchronous psycopg job store on the event loop.**
`scheduler_service.py` builds `SQLAlchemyJobStore(url=build_sync_jobstore_url(...))`
— a separate sync engine, not the app's asyncpg pool — under an
`AsyncIOScheduler`. Every `add_job` / `remove_job` / `get_jobs` blocks the loop
for a synchronous round trip, as does APScheduler's own periodic wakeup.

Not fixed here, deliberately. Our six call sites are sync methods
(`def remove_job`, `def get_job`, `def get_jobs`), so offloading them means an
async signature change rippling through every caller — and it would still leave
the scheduler's internal wakeup on the loop, since that is APScheduler's code,
not ours. The complete fix is a `BackgroundScheduler` on its own thread with
job callbacks marshalled back via `run_coroutine_threadsafe`, which is a real
architectural change to scheduling and wants its own PR and its own e2e
coverage. The exposure is bounded: a local Postgres round trip, not the
74 ms-class CPU stalls this document's audit was chasing.

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
