# Reliability operations

## Event lifecycle

Accepted domain events are written to `domain_event_outbox` in the state-change
transaction. The dispatcher claims bounded batches with `FOR UPDATE SKIP
LOCKED`, publishes at least once, and retries with backoff. Each consumer claims
`domain_event_inbox` by `(consumer, event_id)` and records completion, terminal
validation failure, retry, or dead-letter state.

Use the backend event admin command to list or inspect backlog and replay a
specific outbox/inbox dead-letter record. Replay is audited; confirm the target
handler and external provider support idempotency before replaying.

## Signals and initial alert guidance

| Signal | Warning | Critical |
| --- | --- | --- |
| Oldest available outbox row | 2 minutes | 10 minutes |
| Outbox pending rows | Sustained growth for 5 minutes | Growth for 15 minutes |
| Consumer lag | Above normal traffic window for 5 minutes | No progress for 10 minutes |
| Outbox/inbox DLQ | Any new row | More than 5 related rows or a security path |
| Schedule fire processing | 2 minutes | 10 minutes or repeated same schedule |
| Pod provisioning | 5 minutes | 15 minutes |
| Bundle step/job | 10 minutes without checkpoint | 30 minutes |
| File processing | 10 minutes | 30 minutes |
| Workflow wait | Past configured deadline plus 5 minutes | Reconciler cannot advance |

Tune counts to deployment traffic, but retain the no-progress and oldest-age
alerts. Dashboards and traces should carry event, correlation, causation,
request, pod, schedule, workflow-run, agent-run, and bundle-job identifiers.

## Redis memory and consumer lanes

Event streams share one Redis with sessions, locks and the job queues, under
`noeviction`. So a stream that outgrows memory takes down every write in the
platform, not just its own. Two mechanisms prevent that.

**The worker restarts itself when a lane stops consuming.** It exits with status
70 in three cases:

- a stream reader task has ended;
- a streaq lane task has ended;
- a consumer group it reads has been behind for `REDIS_STREAM_STALL_SECONDS`
  (default 900) with its reader not reading.

First it logs `worker.lane.dead` or `redis.stream.group_stalled`. Then it stops
refreshing the heartbeat file (`WORKER_HEARTBEAT_PATH`) and the
`lemma:worker:alive` key, so readiness reports it stalled. Run the worker under
something that restarts it, and give it a liveness probe on the heartbeat
file's freshness.

**Streams are held to one memory budget.** Every
`REDIS_STREAM_GUARD_INTERVAL_SECONDS` (default 30s) the worker:

1. works out a budget: streams together may use `REDIS_STREAMS_MEMORY_FRACTION`
   of `maxmemory`, and never so much that total use passes
   `REDIS_MEMORY_WARN_RATIO`. Without a `maxmemory`, the budget is
   `REDIS_STREAMS_BUDGET_BYTES`.
2. trims the largest streams first. Entries every group has read go first.
3. if that is still over budget, trims entries a group had not read. This logs
   `redis.stream.unread_trimmed` and records a gap.
4. once that group reads again, re-publishes the gap from the outbox. Events are
   kept there for `EVENT_COMPLETED_RETENTION_DAYS`, and consumers dedupe
   through the inbox.

Above `REDIS_MEMORY_CRITICAL_RATIO` the outbox dispatcher stops publishing until
use falls below `REDIS_MEMORY_RESUME_RATIO`. Events wait in PostgreSQL in the
meantime.

Give Redis a `maxmemory` below its container limit. Without one, Redis is killed
by the kernel instead of refusing writes, and the budget falls back to a fixed
size.

| Signal | Meaning |
| --- | --- |
| `lemma.redis.memory.bytes` (`kind`: `used`, `max`, `streams_budget`) | Headroom |
| `lemma.redis.stream.group.lag`, `.reader_inactive` | A group falling behind, or its reader silent |
| `redis.memory.critical` | Publishing paused |
| `redis.stream.unread_trimmed` | A group lost entries to the budget; replay pending |
| `redis.stream.gap_unrecoverable` | Part of a gap is older than the outbox keeps |

To replay a window by hand, for example when the gap record itself could not be
written, pass the `after_ms` and `until_ms` from the `redis.stream.unread_trimmed`
line:

```bash
uv run python -m app.core.infrastructure.events.admin replay-window <stream> <after_ms> <until_ms>
```

## Rollout order

1. Apply the consolidated `0003_backend_reliability` migration.
2. Deploy workers with inbox/outbox support.
3. Deploy API instances.
4. Drain old pod-bundle workers.
5. Enable durable bundle cancellation and durable-job behavior.

Legacy events are assigned deterministic IDs from stable message metadata or a
canonical payload hash during overlap. Events without a stable provider/source
ID are quarantined where exactly-once schedule effects are required.
