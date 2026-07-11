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

## Rollout order

1. Apply the consolidated `0003_backend_reliability` migration.
2. Deploy workers with inbox/outbox support.
3. Deploy API instances.
4. Drain old pod-bundle workers.
5. Enable durable bundle cancellation and durable-job behavior.

Legacy events are assigned deterministic IDs from stable message metadata or a
canonical payload hash during overlap. Events without a stable provider/source
ID are quarantined where exactly-once schedule effects are required.
