# Load test report — 100 concurrent users (mock LLM)

## Setup
- **Topology (prod-shaped):** separate `api` + `worker` containers, each capped at
  **2 CPU / 2 GB**; `db` (pgvector), `redis`, `supertokens`. Pools = the fixed
  config: main `DB_POOL_SIZE=10 + MAX_OVERFLOW=10` (20/proc), datastore `5+5`,
  `WORKER_CONCURRENCY=20`. Postgres `max_connections=100`.
- **LLM:** deterministic in-process mock (`E2E_LLM_MODE=mock`). Each agent run
  streams text + a real `write_todos` tool call (DB-backed) then a final line —
  exercising **api → DB → redis → worker → SSE** with no real model.
- **Load:** k6 `journey.js`, **ramping-vus 0→100 over 2m30s, hold 2m, ramp-down**.
  Each VU provisions once (signup→org→pod→TODO agent→conversation) then loops the
  same message (think-time 500ms). Single api + single worker replica.

## Headline
1. ✅ **The DB connection-exhaustion fix holds.** Postgres connections stayed
   **bounded and flat** under sustained 100-user load — no pile-up, no exhaustion.
2. ⚠️ **The new ceiling is the per-process pool, not Postgres.** A single api
   process (pool 20) can't serve 100 concurrent DB-bound requests → **830
   `QueuePool` checkout timeouts**, dragging provisioning and tail latency.
3. ⚠️ **Both api and worker are CPU-bound** (≈1 core each, 98–100%) at 100 users
   on 2-CPU limits.

## Data

### Per-API latency (ms)
| API | count | avg | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| signup | 582 | 9548 | 10287 | 10657 | 10740 | 10890 | 10978 |
| create_org | 160 | 6814 | 9867 | 10053 | 10093 | 10292 | 10422 |
| create_pod | 124 | 6193 | 9943 | 10062 | 10214 | 10453 | 10488 |
| create_agent | 96 | 5054 | 5251 | 10029 | 10043 | 10079 | 10101 |
| create_conv | 73 | 4021 | 162 | 10049 | 10091 | 10158 | 10186 |
| **message_roundtrip** | 1959 | 5954 | **1124** | **19846** | 19977 | 20169 | 20529 |

Success: **provision 10.4%**, **message 84.1%**. Messages: 1959 attempts, 1647
`write_todos` executed (84%), 6588 tokens streamed. journey_errors=827.

### DB connections (peak / steady-state during hold)
| metric | baseline | steady (hold) | peak | budget / max |
|---|---:|---:|---:|---:|
| total | 22 | ~47 | 58 | 100 (Postgres max) |
| `lemma` (main) | 5 | ~30 | 41 | 40 (2 proc × 20) |
| idle-in-transaction | 0 | **~20 (flat)** | 29 | — |
| `lemma_datastore` | 2 | 2 | 2 | 20 |
| max open-txn age | — | <7s | ~9s | 60s timeout (never tripped) |

idle-in-transaction was **flat at ~20 = `WORKER_CONCURRENCY`** through the whole
hold (one open transaction per in-flight agent run) — bounded, not growing.

### Container resources (peak)
| container | peak CPU | mem | limit |
|---|---:|---:|---|
| lemma-load-api | **97.9%** | 367 MiB | 2 CPU / 2 GB |
| lemma-load-worker | **100.6%** | 352 MiB | 2 CPU / 2 GB |
| db | 25.2% | 245 MiB | — |
| redis | 3.1% | 229 MiB | — |

### Errors
- **api: 830 `sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 10
  reached, connection timed out, timeout 10.00`**
- worker: 0 pool timeouts. No `idle in transaction` pile-up. No Postgres "too many
  connections".

## Analysis

**1. The connection-exhaustion fix achieves its goal.** The original bug pinned
85–104 / 100 Postgres connections and stalled. Under sustained 100-user load the
total stayed **flat at ~47 (peak 58)**, `lemma` sat at the **40-connection pool
budget**, idle-in-transaction stayed **bounded at ~20** (= worker concurrency,
i.e. one txn per running agent run, not a leak), and the longest open transaction
was <9s — well under the 60s `idle_in_transaction_session_timeout`, which never
even had to fire. Postgres CPU/mem were trivial (25% / 245 MiB). **No exhaustion,
no pile-up.**

**2. The bottleneck moved from Postgres to the per-process connection pool.** The
fix deliberately shrank each process's pool to 20 so that *N* replicas stay under
Postgres' 100-connection ceiling. The flip side, visible here, is that **one api
process can only hold ~20 DB connections at once**. With 100 concurrent users all
doing DB-bound work (every signup/org/pod/agent/conv call and the
`add_user_message_and_start_run` at the start of each message needs a `lemma`
connection), the pool saturated and the 21st+ checkout waited out the 10s timeout
— **830 times**. That is exactly why:
   - **Provisioning collapsed to 10.4%** — the first iteration fires 5 sequential
     DB-bound calls per VU, and 100 VUs ramping at once overwhelm the 20-slot
     pool; signup/org/pod sit right at the 10s timeout (p50 ≈ 10s).
   - **Message latency is bimodal** — p50 **1.1s** (healthy when a connection is
     free) but p90 **~20s** (10s pool-wait + worker queue) and ~16% fail.

**3. Both api and worker are CPU-saturated (~1 core).** The worker pegs a core at
`WORKER_CONCURRENCY=20` (CPU-bound even with the mock), so agent runs queue and
each open transaction lives a few seconds — which is what keeps idle-in-tx at 20.
The api also pegs a core handling 100 concurrent SSE streams + JSON, slowing every
request and lengthening pool holds, compounding the checkout timeouts.

**Net:** the fix correctly trades *unbounded Postgres exhaustion* for *bounded,
predictable per-process capacity*. A single 2-CPU api/worker pair tops out around
~20 concurrent DB operations; 100 simultaneous users exceed that.

## Recommendations
1. **Scale out, don't fatten the pool.** The fix leaves headroom — 2 procs use
   40/100 connections, so ~4–5 api replicas (4×20=80 < 100) fit under Postgres.
   Horizontal replicas behind the LB are the intended way to serve 100+ concurrent
   users; this single-replica test is the per-replica ceiling, not the system's.
2. **The SSE/message steady-state is healthy** (p50 1.1s); the pain is the
   *provisioning burst* (5 DB calls × 100 VUs at once), which is artificial — real
   users provision once, spread over time. Worth re-measuring with provisioning
   amortized (e.g., pre-provisioned users + pure message load) to isolate
   steady-state capacity.
3. **Shorten the agent-run transaction.** idle-in-tx = worker_concurrency because
   each run holds a `lemma` connection for its whole duration (incl. non-DB
   streaming). Scoping the run's UoW to just its DB ops (release during
   streaming/redis publish) would free pool slots and cut idle-in-tx — the same
   short-UoW pattern already applied to SSE/datastore/surface paths.
4. **CPU is the other limit** — at 100 users both api and worker are at ~1 core;
   give them more vCPU or replicas. Consider a modest `db_pool_timeout` bump only
   if checkout bursts remain after scaling (do **not** enlarge pools — that
   re-introduces the Postgres-budget risk the fix removed).

## Repro
```
make load-test-build && make load-test-up && make load-test-migrate
# samplers (separate shells): load_tests/docker_stats.py + a pg_stat_activity poll
make load-test-journey MAX_USERS=100 THINK_MS=500
make load-test-down
```
Mock LLM = `E2E_LLM_MODE=mock` (compose); journey scripts `write_todos` via the
conversation `mock_llm_script` metadata.
