# schedule contract

What every `schedule` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `schedule.create` | POST | `/pods/{pod_id}/schedules` | Create Schedule |
| `schedule.delete` | DELETE | `/pods/{pod_id}/schedules/{schedule_id}` | Delete Schedule |
| `schedule.event.list` | GET | `/pods/{pod_id}/events` | List Events |
| `schedule.get` | GET | `/pods/{pod_id}/schedules/{schedule_id}` | Get Schedule |
| `schedule.list` | GET | `/pods/{pod_id}/schedules` | List Schedules |
| `schedule.run.create` | POST | `/pods/{pod_id}/schedules/{schedule_id}/runs` | Create Schedule Run |
| `schedule.run.list` | GET | `/pods/{pod_id}/schedules/{schedule_id}/runs` | List Schedule Runs |
| `schedule.run.retry` | POST | `/pods/{pod_id}/schedules/{schedule_id}/runs/{run_id}/retry` | Retry Schedule Run |
| `schedule.skip.list` | GET | `/pods/{pod_id}/schedules/{schedule_id}/skips` | List Schedule Skips |
| `schedule.update` | PATCH | `/pods/{pod_id}/schedules/{schedule_id}` | Update Schedule |

<!-- /generated:operations -->

## `schedule.event.list`

Any pod member. The hook points a schedule here can start on, each in the MCP
Events descriptor shape (`name`, `input_schema`, `payload_schema`) with the
`schedule_type` that serves it: `time` (TIME) and `record.*` (DATASTORE).

Then every event on an MCP server the caller connected in the pod's
organization, named `mcp.<install>.<event>` and served by WEBHOOK, with the
`account_id` a schedule on it listens through, the `server` and the `event`'s
own name. Another member's servers are not listed: the subscription is made on
the author's own account.

## `schedule.create` (an MCP server's event)

A WEBHOOK schedule whose config is `{"source": "mcp", "event", "arguments"}`
and that names an `account_id` subscribes on that account before the row is
returned: the event must be one the install's server listed, the arguments
must carry what its `inputSchema` requires, and the server must prove our
callback with a signed challenge. Our subscription id is stored as
`config.provider_trigger_id`. Without an account, for an event the server does
not offer, or for missing arguments, it is refused (400) and nothing is left
subscribed. Editing the event or its arguments subscribes anew and drops the
old one after commit; deleting the schedule unsubscribes. An agent target only:
a workflow listens to the event its own start names.

## `schedule.run.create`

Starts one run of a TIME schedule now, outside its timing. No body; answers
`202` with the run, `PROCESSING` and `attempts: 0` until the
`schedule.start_target` job -- written in the same transaction as the run --
hands it to its target.

- **Who may:** the holder of `schedule.update` on the schedule, the same gate
  as `schedule.run.retry`, who must also be able to execute its agent or
  workflow. The run is theirs: `user_id` is the caller, never the owner, and
  the start job checks their access to the target again when it starts.
- **Same limits as a firing:** it counts toward and is stopped by the daily
  ceiling (`trigger_fire_day`) and `max_concurrent_runs`, and its start draws
  on the per-trigger `trigger_fire` rate. The target's own start applies
  whatever it applies to any run, the pod's spend included.
- **What it carries:** the config's `payload`, as a timer's occurrence does;
  `metadata` is `{"manual": true, "started_by_user_id": "<caller>"}`;
  `source_event_id` is `manual:<uuid>`, new on every call. A schedule's filter
  is not asked.
- **Leaves the timer alone:** `next_fire_at` and its pending fire are
  unchanged.
- **Refuses:** 404 for a schedule the caller cannot see; 403 without
  `schedule.update` or execute on the target; 422 `SCHEDULE_RUN_NEEDS_EVENT`
  for WEBHOOK and DATASTORE schedules; 409 `SCHEDULE_INACTIVE` while paused,
  `SCHEDULE_TARGET_MISSING` once the target is deleted, `SCHEDULE_AT_CAPACITY`
  when full, `SCHEDULE_FIRE_CAP_REACHED` past the ceiling. The last two commit
  what a firing records there first: a skipped run, or the pause, the
  `fire_cap` skip and the owner's notice.
