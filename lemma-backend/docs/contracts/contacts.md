# contacts contract

What every `contacts` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `contact.delete` | DELETE | `/pods/{pod_id}/contacts/{contact_id}` | Delete Contact |
| `contact.export` | GET | `/pods/{pod_id}/contacts/{contact_id}/export` | Export Contact |
| `contact.follow_up` | POST | `/pods/{pod_id}/contacts/{contact_id}/messages` | Follow Up Contact |
| `contact.get` | GET | `/pods/{pod_id}/contacts/{contact_id}` | Get Contact |
| `contact.list` | GET | `/pods/{pod_id}/contacts` | List Contacts |
| `contact.update` | PATCH | `/pods/{pod_id}/contacts/{contact_id}` | Update Contact |

<!-- /generated:operations -->
