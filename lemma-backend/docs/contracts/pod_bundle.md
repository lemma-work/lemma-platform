# pod_bundle contract

What every `pod_bundle` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `pod.bundle.download` | GET | `/pods/bundle/download` | Download A Bundle Archive |
| `pod.bundle.export.get` | GET | `/pods/{pod_id}/bundle/exports/{export_id}` | Get Pod Export Status |
| `pod.bundle.export.start` | POST | `/pods/{pod_id}/bundle/exports` | Start Pod Export |
| `pod.bundle.import.apply` | POST | `/pods/{pod_id}/bundle/imports/{import_id}/apply` | Apply Pod Import |
| `pod.bundle.import.cancel` | DELETE | `/pods/{pod_id}/bundle/imports/{import_id}` | Cancel Pod Import |
| `pod.bundle.import.events` | GET | `/pods/{pod_id}/bundle/imports/{import_id}/events` | Stream Pod Import Progress |
| `pod.bundle.import.get` | GET | `/pods/{pod_id}/bundle/imports/{import_id}` | Get Pod Import Status |
| `pod.bundle.import.replan` | POST | `/pods/{pod_id}/bundle/imports/{import_id}/replan` | Re-plan Pod Import |
| `pod.bundle.import.start` | POST | `/pods/{pod_id}/bundle/imports` | Start Pod Import |
| `pod.bundle.publish.events` | GET | `/pods/{pod_id}/bundle/publishes/{publish_id}/events` | Stream Pod Publish Progress |
| `pod.bundle.publish.get` | GET | `/pods/{pod_id}/bundle/publishes/{publish_id}` | Get Pod Publish Status |
| `pod.bundle.publish.start` | POST | `/pods/{pod_id}/bundle/publishes` | Publish Pod To GitHub |
| `pod.bundle.upload` | POST | `/pods/{pod_id}/bundle/uploads` | Stage A Local Bundle Upload |

<!-- /generated:operations -->
