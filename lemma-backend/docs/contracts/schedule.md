# schedule contract

What every `schedule` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `schedule.create` | POST | `/pods/{pod_id}/schedules` | Create Schedule |
| `schedule.delete` | DELETE | `/pods/{pod_id}/schedules/{schedule_id}` | Delete Schedule |
| `schedule.get` | GET | `/pods/{pod_id}/schedules/{schedule_id}` | Get Schedule |
| `schedule.list` | GET | `/pods/{pod_id}/schedules` | List Schedules |
| `schedule.run.list` | GET | `/pods/{pod_id}/schedules/{schedule_id}/runs` | List Schedule Runs |
| `schedule.run.retry` | POST | `/pods/{pod_id}/schedules/{schedule_id}/runs/{run_id}/retry` | Retry Schedule Run |
| `schedule.update` | PATCH | `/pods/{pod_id}/schedules/{schedule_id}` | Update Schedule |

<!-- /generated:operations -->
