# channels contract

What every `channels` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `channel.get` | GET | `/pods/{pod_id}/channels/{channel_id}` | Get Channel |
| `channel.handler.release` | DELETE | `/pods/{pod_id}/channels/{channel_id}/handler` | Release Channel |
| `channel.handler.take` | PUT | `/pods/{pod_id}/channels/{channel_id}/handler` | Take Channel |
| `channel.list` | GET | `/pods/{pod_id}/channels` | List Channels |
| `channel.mark_read` | POST | `/pods/{pod_id}/channels/{channel_id}/read` | Mark Channel Read |
| `channel.message.list` | GET | `/pods/{pod_id}/channels/{channel_id}/messages` | List Channel Messages |

<!-- /generated:operations -->
