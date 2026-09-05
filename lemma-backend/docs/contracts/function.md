# function contract

What every `function` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `function.create` | POST | `/pods/{pod_id}/functions` | Create Function |
| `function.delete` | DELETE | `/pods/{pod_id}/functions/{function_name}` | Delete Function |
| `function.get` | GET | `/pods/{pod_id}/functions/{function_name}` | Get Function |
| `function.list` | GET | `/pods/{pod_id}/functions` | List Functions |
| `function.permissions.get` | GET | `/pods/{pod_id}/functions/{function_name}/permissions` | Get Function Resource Permissions |
| `function.permissions.replace` | PUT | `/pods/{pod_id}/functions/{function_name}/permissions` | Replace Function Resource Permissions |
| `function.revision.get` | GET | `/pods/{pod_id}/functions/{function_name}/revisions/{revision_ref}` | Get Function Revision |
| `function.revision.list` | GET | `/pods/{pod_id}/functions/{function_name}/revisions` | List Function Revisions |
| `function.revision.promote` | POST | `/pods/{pod_id}/functions/{function_name}/revisions/{revision_ref}/promote` | Promote Function Revision |
| `function.run` | POST | `/pods/{pod_id}/functions/{function_name}/runs` | Execute Function |
| `function.run.get` | GET | `/pods/{pod_id}/functions/{function_name}/runs/{run_id}` | Get Run |
| `function.run.list` | GET | `/pods/{pod_id}/functions/{function_name}/runs` | List Runs |
| `function.update` | PATCH | `/pods/{pod_id}/functions/{function_name}` | Update Function |

<!-- /generated:operations -->
