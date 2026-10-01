# decisions contract

What every `decisions` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `decider.create` | POST | `/pods/{pod_id}/deciders` | Create a decider |
| `decider.delete` | DELETE | `/pods/{pod_id}/deciders/{decider_name}` | Delete a decider |
| `decider.get` | GET | `/pods/{pod_id}/deciders/{decider_name}` | Get a decider |
| `decider.list` | GET | `/pods/{pod_id}/deciders` | List deciders |
| `decider.test` | POST | `/pods/{pod_id}/deciders/test` | Test a decider |
| `decider.update` | PUT | `/pods/{pod_id}/deciders/{decider_name}` | Save a new version of a decider |
| `decider.version.list` | GET | `/pods/{pod_id}/deciders/{decider_name}/versions` | List a decider's versions |
| `decision.answer` | POST | `/pods/{pod_id}/decisions/{decision_id}/answer` | Answer a decision |
| `decision.create` | POST | `/pods/{pod_id}/decisions` | Ask a decision |
| `decision.get` | GET | `/pods/{pod_id}/decisions/{decision_id}` | Get a decision |
| `decision.list` | GET | `/pods/{pod_id}/decisions` | List decisions |
| `decision.rows` | POST | `/pods/{pod_id}/decisions/rows` | Decide many rows |

<!-- /generated:operations -->
