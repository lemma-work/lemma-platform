# Load test report — full product surface @ 100 concurrent users

**Date:** 2026-06-26
**Branch:** `e2e-mock-llm-and-sandbox`
**Topology (prod-like):** API 1 CPU / 2 GB, worker 1 CPU / 2 GB, Postgres `max_connections=100`,
per-process pool `10+10` (main) + `5+5` (datastore), `WORKER_CONCURRENCY=20`.
**Mocks:** LLM = in-process FunctionModel (`E2E_LLM_MODE=mock`, 1.5 s emulated latency × 2 turns);
sandbox = fake AgentBox service (`E2E_SANDBOX_MODE=fake`) — functions create + execute without Docker.

## What the journey exercises now
Each of 100 VUs provisions once (signup → org → pod → TODO agent → conversation → **API function** →
**app + dist bundle**), then loops the full surface every iteration:

- **agent message** (SSE round-trip: api → DB → redis → worker → mock → SSE)
- **file CRUD**: create → update(content) → download → delete
- **function execute** (API/sync, against the fake AgentBox)
- **app asset load** (serve the uploaded dist bundle)

Profile: ramp 0→100 over 2m30s, hold 2m, ramp down 30s.

## Per-API latency (ms)

| API | count | avg | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| signup | 100 | 262 | 254 | 291 | 322 | 415 | 505 |
| create_org | 100 | 30 | 27 | 52 | 64 | 95 | 131 |
| create_pod | 100 | 42 | 33 | 69 | 95 | 134 | 147 |
| create_agent | 100 | 16 | 13 | 30 | 34 | 85 | 87 |
| create_conv | 100 | 9 | 6 | 18 | 21 | 29 | 42 |
| create_function | 100 | 108 | 96 | 169 | 183 | 209 | 211 |
| create_app | 100 | 12 | 7 | 26 | 31 | 46 | 46 |
| upload_bundle | 100 | 20 | 14 | 53 | 61 | 86 | 87 |
| **message_roundtrip** | 1709 | 11543 | 13225 | 16465 | 16587 | 16820 | 16992 |
| create_file | 1709 | 24 | 17 | 50 | 59 | 92 | 231 |
| update_file | 1709 | 153 | 93 | 377 | 438 | 530 | 664 |
| download_file | 1709 | 32 | 23 | 64 | 82 | 153 | 350 |
| delete_file | 1708 | 30 | 22 | 59 | 74 | 114 | 341 |
| execute_function | 1708 | 107 | 92 | 200 | 237 | 314 | 482 |
| load_app_asset | 1708 | 23 | 16 | 47 | 59 | 92 | 276 |

## Success + resource summary

| Metric | Value |
|---|---|
| provision_success | **100%** |
| message_success | **100%** |
| file_success (create+update+download+delete) | **100%** |
| function_success | **100%** |
| app_success | **100%** |
| journey_errors | **0** |
| DB connections (app DBs) — peak total | **38** / 60 budget |
| DB connections — peak `idle in transaction` | **10** |
| QueuePool checkout timeouts | **0** |
| Pool-utilization (80%) warnings | **0** |
| API peak CPU / mem | 95% / 344 MiB (of 2 GiB) |
| Worker peak CPU / mem | 63% / 340 MiB |
| fake-agentbox peak CPU / mem | 2% / 48 MiB |

## Analysis

- **The connection-holding work holds under the full surface.** With 100 concurrent users driving
  agent runs *and* file CRUD *and* function execution *and* app serving simultaneously, the database
  peaks at **38 of the 60-connection budget** with only **10 idle-in-transaction** and **zero
  QueuePool checkout timeouts**. Every flow that previously held a pooled connection across object
  storage / sandbox / SSE I/O — file create/update/download/delete, app upload/delete/asset-serving,
  function create/execute, the agent message + brief — now releases the connection during that I/O.
  This is the headline result: the system is no longer connection-bound.

- **The bottleneck is now CPU/throughput, not connections.** The API saturates one core (95%) doing
  SSE relay + the heavy per-iteration mix; `message_roundtrip` sits at ~11.5 s avg / ~16.5 s p90.
  That latency is worker queueing + API CPU, not the DB: 100 message runs arrive against a 1-CPU
  worker at concurrency 20 (with 1.5 s × 2 emulated model turns), so runs queue. This is the expected,
  healthy next bottleneck — addressed by scaling API/worker replicas horizontally, *not* by pool or
  connection changes. Memory is a non-issue (~340 MiB of 2 GiB).

- **New flows are cheap and stable.** Function execute (107 ms avg), app asset load (23 ms), and file
  update (153 ms avg — the heaviest, being the two-UoW content-update saga) all stay flat at 100 users
  with 100% success. The fake AgentBox barely registers (2% CPU).

- **Benign log noise:** the worker logs ~1 "error" per run — `Missing usage pricing for system model
  '…deepseek-v4-flash'; using fallback pricing so usage is still recorded`. This is the *mock* model
  name lacking a pricing entry; usage is still recorded and the run completes. It is not a failure
  (every flow was 100%) and would not occur with a real, priced model.

## Conclusion

At 100 concurrent users across the full product surface (agent chat, file CRUD, function execution,
app loading), the backend runs **100% green with zero connection-pool exhaustion** on a prod-like
1-CPU/2-GB API + worker. The original failure mode — connections climbing and sticking until QueuePool
timeouts cascaded — is gone. Remaining latency is CPU/throughput-bound and is a horizontal-scaling
concern, not a correctness or connection bug.
