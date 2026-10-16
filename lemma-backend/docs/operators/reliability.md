# Reliability operations

## Background work

Everything that happens after a request runs as a row in `work_items`, written
in the transaction that caused it. That covers agent turns, file processing,
trigger fires, workflow steps, deliveries, bundle jobs, surface ingest and
receivers, and retention sweeps. The work runtime
(`app/core/infrastructure/work/`) claims rows fairly across pods, holds each
one under a lease it heartbeats, and deletes it in the transaction that
completes it. A process that crashes or is deployed leaves its rows running
under leases that expire, and another process reclaims them. A poison item
stops at `dead` with its domain state written terminal, and stays for
inspection.

Every claim bumps the item's `lease_epoch`, and every fenced write (a
completion, a failure, a release, an agent turn's step) matches the owner and
that epoch under the item's row lock. A process whose lease lapsed while it was
still running the item -- its heartbeat could not reach the database -- writes
nothing after another claim, even one by the same process.

The database being unavailable (a refused connection, a full server, an empty
pool, a connection cut mid-statement) is not an item failing. The runtime
retries it without charging an attempt, for as long as the kind's unavailable
budget, and handlers that record failures re-raise it rather than record it
(`raise_if_database_unavailable`). An item that outlasts the budget dies as
`unavailable_exhausted`, and its domain state says the database was down.

Work runs in classes (`interactive`, `automation`, `delivery`, `ingestion`,
`admin`, `receivers`, `events`), each with its own slots, per-pod cap, lease
and shutdown grace. Recurring maintenance is periodic work: one item per sweep,
re-armed in its own completion transaction. A tick that keeps failing goes
`dead` like any other item, and the dead transaction re-arms the next tick.

There is no message broker. Committed domain events go only to an in-process
after-commit sink (`app/core/analytics/domain_events.py`). Its projections are
product analytics and the agent memory-brief cache invalidation, both best
effort. Nothing durable reads them.

## The worker process

`python -m app.worker` runs the work runtime through `worker_services`
(`app/core/infrastructure/work/process.py`). It starts:

- the classes named in `WORK_CLASSES` (empty means all of them). An
  ingestion-only or receivers-only deployment is the same command with a
  different value;
- every module's worker lifespans;
- the loop-lag watchdog, which refreshes the heartbeat file at
  `WORKER_HEARTBEAT_PATH`. Give the worker a liveness probe on that file's
  freshness;
- the `lemma:worker:alive` Redis key, which the API's `/health/ready` reads;
- a memory sampler, and a `worker.heartbeat` log line every five minutes.

It is ready when it logs `service.started` (logger
`app.core.infrastructure.work.process`).

On SIGTERM or SIGINT the runtime stops claiming at once. Each running job sees
`ctx.stopping` at its next safe point and is handed back uncharged. Each class
waits at most its own shutdown grace:

- `interactive` has 90 seconds built in, so an agent turn can reach a step
  boundary;
- every other class has `WORK_SHUTDOWN_GRACE_SECONDS` (default 20);
- `WORK_SHUTDOWN_GRACE_JSON` overrides any class, for example
  `{"interactive": 120}`.

Whatever is still running when its grace ends is released to the queue. Then
the process exits 0. **The platform's termination grace must exceed the longest
class grace plus teardown** (120 seconds for the defaults). Otherwise the
platform kills what is still draining, and those items wait out their leases.

Several workers can run side by side, and a rolling deploy is safe. Claims use
`FOR UPDATE SKIP LOCKED`, periodics are armed under an advisory lock, and
concurrency keys keep, for example, one turn per conversation.

The single-process app (`app/standalone.py`, used by desktop, the local stack
and compose) enters the same `worker_services(embedded=True)` after the API
lifespan. It runs every class and stops before the API's own teardown.

## Operator CLI and signals

```bash
uv run python -m app.core.infrastructure.work.admin ls [--ns NS] [--class C] [--kind K]
uv run python -m app.core.infrastructure.work.admin pause NS [--class C]
uv run python -m app.core.infrastructure.work.admin resume NS [--class C]
uv run python -m app.core.infrastructure.work.admin retry [--ns NS] [--kind K] [--id ID]
```

`retry` puts dead items back with every budget reset.

| Signal | Meaning |
| --- | --- |
| `lemma.work.ready_oldest_age` (by class) | How long the oldest claimable item has waited. Suggested alerts: events 30 s, interactive 60 s, automation 5 min, ingestion 30 min |
| `lemma.work.claim_latency` | Time from due to claimed, per class |
| `lemma.work.dead` (by class, kind, error class) | Items that gave up. Any new one deserves a look |
| `lemma.work.lease_reaped` | Leases that expired, because a process died or wedged |
| `lemma.work.items`, `lemma.work.namespaces` | Queue depth by state |
| `lemma.work.handler.duration` | Handler time, per kind |

## Cutover from the old transport (design §13)

Earlier releases moved background work through Redis Streams (FastStream
consumer groups), streaq queues and a transactional outbox
(`domain_event_outbox`, `domain_event_inbox`). The release that carries
migrations `0054` to `0056` stops using them. **The cutover replays nothing**:
what was durable state is armed, what was in flight ends, and whatever the old
transport still held -- unread stream entries, unpublished outbox rows, queued
streaq jobs -- is dropped.

**Deploy it with the previous release's workers stopped.** Scale the old
workers to zero, run the migrations, then start the new release. An old worker
left running would keep taking work from Redis that the new release never sees,
and race the migrations on the rows they end.

What the migrations do:

* **Armed** -- every TIME schedule gets its next fire (one already past due is
  skipped to its next occurrence, not fired late); flow timers, decisions,
  expiring waits and parked runs get their items; active agent waits, AGENT
  workflow waits and open notifications' expiry get theirs.
* **Ended, saying so** -- a file waiting to be indexed is FAILED ("Retry
  processing to index this file"); a function run and the workflow on it,
  a running agent turn, a conversation paused on an approval that was decided
  but never executed, an unfinished trigger run and an unfinished pod bundle
  job are FAILED with an "interrupted by the upgrade" message. A person
  continues by sending a message or starting the job again.
* **Dropped** -- a trigger event the old consumer had not processed yet, a
  surface reply the old transport owed, and everything else still in Redis or
  the outbox.

The tables, the datastore database's outbox and the legacy consumer groups and
streams are dropped by migration `0057_drop_old_transport` in the next release.

Runbooks that counted `domain_event_outbox` rows (backlog, restore checks)
should read `work_items` instead: queued, running and dead items per class
(`python -m app.core.infrastructure.work.admin ls`).
