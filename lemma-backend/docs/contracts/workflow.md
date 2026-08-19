# workflow contract

What every `workflow` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `workflow.create` | POST | `/pods/{pod_id}/workflows` | Create Workflow |
| `workflow.delete` | DELETE | `/pods/{pod_id}/workflows/{workflow_name}` | Delete Workflow |
| `workflow.get` | GET | `/pods/{pod_id}/workflows/{workflow_name}` | Get Workflow |
| `workflow.graph.update` | PUT | `/pods/{pod_id}/workflows/{workflow_name}/graph` | Update Workflow Graph |
| `workflow.list` | GET | `/pods/{pod_id}/workflows` | List Workflows |
| `workflow.run.cancel` | POST | `/pods/{pod_id}/workflow-runs/{run_id}/cancel` | Cancel Workflow Run |
| `workflow.run.create` | POST | `/pods/{pod_id}/workflows/{workflow_name}/runs` | Create Workflow Run |
| `workflow.run.form.submit` | POST | `/pods/{pod_id}/workflow-runs/{run_id}/form` | Submit Workflow Run Form |
| `workflow.run.get` | GET | `/pods/{pod_id}/workflow-runs/{run_id}` | Get Workflow Run |
| `workflow.run.list` | GET | `/pods/{pod_id}/workflows/{workflow_name}/runs` | List Workflow Runs |
| `workflow.run.list_for_pod` | GET | `/pods/{pod_id}/workflow-runs` | List Workflow Runs In Pod |
| `workflow.run.stream` | GET | `/pods/{pod_id}/workflow-runs/{run_id}/stream` | Stream Workflow Run |
| `workflow.run.visualize` | GET | `/pods/{pod_id}/workflow-runs/{run_id}/visualize` | Visualize Workflow Run |
| `workflow.run.waiting_assigned_to_me` | GET | `/pods/{pod_id}/workflow-runs/waiting/assigned-to-me` | List Workflow Runs Waiting For Current User |
| `workflow.update` | PATCH | `/pods/{pod_id}/workflows/{workflow_name}` | Update Workflow Metadata |
| `workflow.visualize` | GET | `/pods/{pod_id}/workflows/{workflow_name}/visualize` | Visualize Workflow |

<!-- /generated:operations -->
