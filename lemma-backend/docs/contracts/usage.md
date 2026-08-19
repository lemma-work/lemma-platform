# usage contract

What every `usage` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `usage.organization.events.list` | GET | `/usage/organizations/{organization_id}/events` | List Usage Events |
| `usage.organization.limits.get` | GET | `/usage/organizations/{organization_id}/limits` | Get Usage Limits |
| `usage.organization.me.summary.get` | GET | `/usage/organizations/{organization_id}/me` | Get My Usage |
| `usage.organization.stats.get` | GET | `/usage/organizations/{organization_id}/stats` | Get Usage Stats |
| `usage.organization.summary.get` | GET | `/usage/organizations/{organization_id}/summary` | Get Organization Usage Summary |

<!-- /generated:operations -->
