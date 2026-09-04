# apps contract

What every `apps` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `app.asset.get` | GET | `/pods/{pod_id}/apps/{app_name}/assets/{asset_path}` | Get App Asset |
| `app.asset.root.get` | GET | `/pods/{pod_id}/apps/{app_name}/assets` | Get App Root Asset |
| `app.bundle.upload` | POST | `/pods/{pod_id}/apps/{app_name}/bundle` | Upload App Bundle |
| `app.create` | POST | `/pods/{pod_id}/apps` | Create App |
| `app.create_from_widget` | POST | `/pods/{pod_id}/apps/from-widget` | Save Widget As App |
| `app.delete` | DELETE | `/pods/{pod_id}/apps/{app_name}` | Delete App |
| `app.dist.archive.get` | GET | `/pods/{pod_id}/apps/{app_name}/dist/archive` | Download App Dist Archive |
| `app.get` | GET | `/pods/{pod_id}/apps/{app_name}` | Get App |
| `app.list` | GET | `/pods/{pod_id}/apps` | List Apps |
| `app.release.list` | GET | `/pods/{pod_id}/apps/{app_name}/releases` | List App Releases |
| `app.release.promote` | POST | `/pods/{pod_id}/apps/{app_name}/releases/{release_ref}/promote` | Promote App Release |
| `app.source.archive.get` | GET | `/pods/{pod_id}/apps/{app_name}/source/archive` | Download App Source Archive |
| `app.update` | PATCH | `/pods/{pod_id}/apps/{app_name}` | Update App |

<!-- /generated:operations -->
