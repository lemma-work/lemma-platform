# Why the API "fails" at 100 users — root-cause analysis

> Correcting the earlier "scale out / per-replica ceiling" conclusion. That was
> the symptom (pool checkout timeouts), not the cause. The real story: **several
> independent single-core bottlenecks + a chatty hot path + a load shape and a
> mock that don't reflect production.** The connection-management code is largely
> fine; the pool is **not** undersized.

## How I tested (decomposition)
Isolated each layer with a constant-VU micro-benchmark (`micro.js`) and sampled
container CPU, instead of inferring from the full journey.

| Test | Work per request | Throughput (1 uvicorn worker) | Throughput (2 workers) | Bound by |
|---|---|---:|---:|---|
| `GET /health` | none (auth-excluded) | **3,104 rps**, p99 46ms, CPU 100% | **5,505 rps**, p99 31ms, CPU 200% | CPU, cores |
| `GET /organizations` | `get_session` + 1 DB read | **705 rps**, p99 788ms | **1,204 rps**, p99 325ms | CPU, cores |
| `POST /st/auth/signup` | SuperTokens password hash | **~18 rps** (flat 20→60 VUs); SuperTokens 3+ cores | — | hashing CPU |

Key facts from these:
- Even a **no-op** (`/health`) pegs a full core at 100 concurrent and caps at
  ~3.1k rps → the API can only use **one core** (single `uvicorn` worker, no
  `--workers`) and pays ~0.3ms CPU/req of framework+middleware+access-log overhead.
- Adding a 2nd worker (**same 20-connection budget**, pool halved to 5+5 each)
  gave ~1.8× throughput and used both cores → the bottleneck was **CPU on one
  core, not the pool**.
- Signup caps at **~18 rps** independent of concurrency, and the cost is in
  **SuperTokens** (password hashing — intentionally expensive, one-time per user).

## What's actually wrong (ranked)

### 1. Single-process deployment — the API uses 1 of 2 cores
`uvicorn app.app:app` runs **one** event loop on **one** core. On the 2-CPU box
the second core is idle. Proven by /health (3.1k→5.5k rps with `--workers 2`).
This is a **deployment/config** issue, not a code-design flaw.

### 2. The streaq worker is also a single process on one core
In the journey the worker pegged its core (99.9%) at `WORKER_CONCURRENCY=20`;
messages queued → `message_roundtrip` p90 ~20s. **Largely a mock artifact:** the
mock LLM returns instantly and is CPU-bound, so 20 concurrent runs saturate one
core. In production an agent run is **I/O-bound** (seconds waiting on the LLM API),
so one core absorbs far more concurrency. The 20s here is mostly "instant mock ×
single-core queue", not a real steady-state number.

### 3. Signup is gated by password hashing (~18/s), and the load shape is unrealistic
100 users signing up *simultaneously* hammers SuperTokens' Argon2/bcrypt. That
cost is by design (it's what makes hashes hard to crack) and is a **one-time**
per-user event. Real traffic amortizes signups over time; ~18/s ≈ 65k/hour. This
single step accounts for most of the dismal "provision 10%". It is not an API flaw.

### 4. The message-start path holds a connection across ~10 sequential queries
`add_user_message_and_start_run` does, in one transaction/connection: get-or-create
conversation → expected agent → re-get conversation → access check → authorization
grant → `lock_conversation` (FOR UPDATE) → resolve agent → active-run (FOR UPDATE)
→ usage preflight → create run → append message → commit → publish. That's ~10
round-trips pinning **one** connection. Each `await` yields to the loop; when the
loop is busy (its share of long-lived SSE streams), the gaps between awaits stretch,
so the **connection-hold time balloons far beyond the actual DB work**. With a
small per-worker pool that produces `QueuePool ... connection timed out` (830 at
1 worker, 461 at 2 workers). This is the literal version of "we're holding a DB
connection too long somewhere" — it's hold-time amplification, not a hard block.
(The SSE relay itself, `iter_subscription`, is DB-free — verified.)

### 5. The connection pool is NOT the bottleneck
Postgres connections stayed bounded the whole time (peak ~54/100, `lemma` ~36,
idle-in-tx ~28 = worker concurrency). The QueuePool timeouts are a **downstream
symptom** of #1 + #4 (connections held too long on a congested single loop), not
of too few connections. Enlarging the pool would just move the wall into Postgres.

### 6. (Minor) per-request overhead
~0.3ms CPU per request even for /health: the middleware stack (incl. a
`BaseHTTPMiddleware`-style auth path — ~40% slower than pure ASGI per Starlette
maintainers) plus uvicorn access logging. Caps single-core throughput; worth
trimming but not the headline.

## Best practices (researched) vs. our design

**Workers / cores.** FastAPI/uvicorn: run **workers = vCPUs** (gunicorn+uvicorn
workers, or `uvicorn --workers`); the async loop gives concurrency *within* a
worker, workers give parallelism *across* cores. We run 1 → fix this first.

**Pool sizing with workers (critical).** SQLAlchemy: QueuePool max =
`pool_size + max_overflow`, and **each forked worker gets its own pool** — never
share connections across `fork()`. So cluster connections =
`Σ processes × (pool_size + max_overflow)`. Budget formula:

```
API_workers × (pool+overflow) + worker_procs × (pool+overflow) + datastore pools  ≤  postgres_max_connections
```

Our lazy `get_engine()` is fork-safe (engine created post-fork on first use), so
adding workers is safe **if** you divide the per-worker pool accordingly.

**Session per request.** Our `get_uow` (session-per-request, `expire_on_commit=
False`, `autoflush=False`, asyncpg) matches the recommended pattern. The issue is
not the pattern but **how long the hot path holds the session** (#4).

## Recommendations (in order)
1. **Run uvicorn with `--workers = vCPUs`** (e.g., gunicorn `-k uvicorn.workers.UvicornWorker -w $(nproc)`),
   and **size the per-worker pool from the budget formula** above. This alone roughly
   doubled API throughput here with the *same* connection budget.
2. **Run multiple streaq worker processes** (parallelism across cores), and
   **re-benchmark with a latency-modeling mock** (`await asyncio.sleep` to mimic LLM
   I/O) — the current instant mock makes the worker look CPU-bound in a way prod isn't.
3. **Slim `add_user_message_and_start_run`**: drop the redundant re-`get_conversation`,
   combine the two `FOR UPDATE` reads, and fetch what you can in one round-trip, so the
   connection is held for ~1-2 awaits instead of ~10. Biggest lever for the pool timeouts.
4. **Cut per-request overhead**: convert the auth `BaseHTTPMiddleware` to pure ASGI
   middleware or a router dependency; disable/sample uvicorn access logging.
5. **Don't enlarge the pool** to "fix" timeouts — it re-introduces the Postgres-budget
   risk the connection-exhaustion work removed. The pool is fine.
6. **Make the load test representative**: pre-provision users (amortize the hashing
   burst), model LLM latency in the mock, and measure provisioning vs steady-state
   separately. Then 100 concurrent *chatting* users on this box is very achievable.

## Bottom line
100 users on 2 GB/2 CPU is absolutely reasonable — the API serves ~1.2k authenticated
DB reads/sec on *two* cores already. The journey looked catastrophic because of
(a) using one core, (b) a single CPU-bound mock worker, (c) a synchronized
password-hashing burst, and (d) a chatty message-start transaction — not because
the connection layer is fundamentally broken.

## Update: prod topology (API 1 CPU + worker 1 CPU, separate processes)

Re-ran the full 100-user journey with each process capped at **1 CPU / 2 GB**
(how prod actually runs), single uvicorn worker, pool 10+10.

| metric | value |
|---|---|
| provision success | 8.8% |
| message success | 86.9% |
| signup p50 | 10.3s |
| message_roundtrip p50 / p90 | ~1.1s / ~20s |
| **API peak CPU** | **9%** |
| **worker peak CPU** | **99.8% (1 core)** |
| DB peak total / lemma / idle-in-tx | 57 / 40 / 30 |
| **API QueuePool timeouts** | **805** (worker: 0) |

**The API is barely working (9% CPU) yet still throws 805 pool timeouts.** That
kills the "API is CPU-starved" story for prod sizing — at 1 CPU the API is nearly
idle. The journey is **worker-bound and signup-bound, not API-bound.**

`pg_stat_activity` during the hold was rock-stable:
```
idle in transaction | ClientRead | 20 | last query: SELECT pods.user_id, pods.organization_id ...
idle                | ClientRead | 10 | last query: COMMIT
```
- **20 connections `idle in transaction`, wait = `ClientRead`** = exactly
  `WORKER_CONCURRENCY`. These are the worker's **short per-event UoWs held open**
  because the worker's one core is saturated by 20 concurrent **instant-mock**
  runs — the coroutine that opened the transaction can't get CPU to commit it, so
  the connection sits open (Postgres waiting on the app, not on a lock). Hold time
  ~5s. **Largely a mock artifact**: a real LLM run is I/O-bound, the core wouldn't
  saturate, and these UoWs would close in milliseconds.
- **10 connections `idle` (COMMIT)** = the API's base pool, returned and idle.

### Corrected conclusion for prod sizing
1. **The API is not the bottleneck** — 9% CPU on one core. The connection layer is
   fine; the message-start path's pool pressure during the burst traces back to the
   worker's open transactions + the synchronized provisioning burst, not API load.
2. **The worker is the bottleneck** — single process, single core, CPU-saturated by
   20 concurrent runs. It both queues messages (→20s) and stretches its short UoWs
   into multi-second idle-in-transaction. **Scale the worker horizontally** (more
   worker replicas; each is 1 core) and tune `WORKER_CONCURRENCY` per core.
3. **The instant mock overstates the problem.** Re-benchmark with a latency-modeling
   mock (`await asyncio.sleep`) so the worker is I/O-bound like prod; expect far
   higher per-core run concurrency and the idle-in-transaction to vanish.
4. **Signup is SuperTokens password hashing** (~18/s), unchanged — amortized in real
   traffic; don't load-test 100 simultaneous signups as representative.
5. **Pool stays as-is.** It's not the limiter.

## Sources
- Starlette BaseHTTPMiddleware overhead/streaming: https://github.com/Kludex/starlette/discussions/2160 , https://github.com/Kludex/starlette/discussions/1729 , https://medium.com/@ssazonov/analysing-fastapi-middleware-performance-8abe47a7ab93
- Uvicorn workers vs single process: https://fastapi.tiangolo.com/deployment/server-workers/
- SQLAlchemy pooling / fork safety / QueuePool: https://docs.sqlalchemy.org/en/20/core/pooling.html , https://docs.sqlalchemy.org/en/20/errors.html
- SQLAlchemy async sessions in FastAPI: https://dev.to/akarshan/asynchronous-database-sessions-in-fastapi-with-sqlalchemy-1o7e
