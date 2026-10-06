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
| `schedule.run.list` | GET | `/pods/{pod_id}/schedules/{schedule_id}/runs` | List Schedule Runs |
| `schedule.run.retry` | POST | `/pods/{pod_id}/schedules/{schedule_id}/runs/{run_id}/retry` | Retry Schedule Run |
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
