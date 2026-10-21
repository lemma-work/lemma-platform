# channels contract

What every `channels` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `channel.case.list` | GET | `/pods/{pod_id}/channels/{channel_id}/cases` | List Cases |
| `channel.create` | POST | `/pods/{pod_id}/channels` | Create Channel |
| `channel.delete` | DELETE | `/pods/{pod_id}/channels/{channel_id}` | Delete Channel |
| `channel.delivery.list` | GET | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/deliveries` | List Deliveries |
| `channel.delivery.retry` | POST | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/deliveries/{delivery_id}/retry` | Retry Delivery |
| `channel.get` | GET | `/pods/{pod_id}/channels/{channel_id}` | Get Channel |
| `channel.inbox.get` | GET | `/pods/{pod_id}/inbox` | Get Inbox |
| `channel.list` | GET | `/pods/{pod_id}/channels` | List Channels |
| `channel.me.create` | POST | `/pods/{pod_id}/channels/me` | Create My Channel |
| `channel.me.get` | GET | `/pods/{pod_id}/channels/me` | Get My Channel |
| `channel.member.add` | POST | `/pods/{pod_id}/channels/{channel_id}/members` | Add Members |
| `channel.member.join` | POST | `/pods/{pod_id}/channels/{channel_id}/members/me` | Join Channel |
| `channel.member.leave` | DELETE | `/pods/{pod_id}/channels/{channel_id}/members/me` | Leave Channel |
| `channel.member.list` | GET | `/pods/{pod_id}/channels/{channel_id}/members` | List Members |
| `channel.member.me_update` | PATCH | `/pods/{pod_id}/channels/{channel_id}/members/me` | Update My Membership |
| `channel.member.remove` | DELETE | `/pods/{pod_id}/channels/{channel_id}/members/{kind}:{member_id}` | Remove Member |
| `channel.member.update` | PATCH | `/pods/{pod_id}/channels/{channel_id}/members/{kind}:{member_id}` | Update Member |
| `channel.message.answer` | POST | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/answer` | Answer Question |
| `channel.message.delete` | DELETE | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}` | Delete Message |
| `channel.message.file` | GET | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/files/{file_id}` | Line File |
| `channel.message.list` | GET | `/pods/{pod_id}/channels/{channel_id}/messages` | List Messages |
| `channel.message.post` | POST | `/pods/{pod_id}/channels/{channel_id}/messages` | Post Message |
| `channel.message.share` | POST | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/share` | Share Message |
| `channel.message.thread` | GET | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/thread` | List Thread |
| `channel.message.update` | PATCH | `/pods/{pod_id}/channels/{channel_id}/messages/{message_id}` | Edit Message |
| `channel.read` | POST | `/pods/{pod_id}/channels/{channel_id}/read` | Mark Read |
| `channel.search` | GET | `/pods/{pod_id}/search` | Search |
| `channel.update` | PATCH | `/pods/{pod_id}/channels/{channel_id}` | Update Channel |

<!-- /generated:operations -->
