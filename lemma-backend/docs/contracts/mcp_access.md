# mcp_access contract

What every `mcp_access` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `mcp_access.endpoint.get` | GET | `/oauth/mcp-endpoint/{pod_id}` | The MCP URL for a pod |
| `mcp_access.grants.list` | GET | `/oauth/grants` | MCP clients you have connected |
| `mcp_access.grants.revoke` | DELETE | `/oauth/grants/{grant_id}` | Disconnect an MCP client |
| `mcp_access.grants.subscription.delete` | DELETE | `/oauth/grants/{grant_id}/subscriptions/{subscription_id}` | Stop telling a connected app about an event |

<!-- /generated:operations -->

## `mcp_access.endpoint.get`

Any signed-in person. Returns the URL an MCP client adds for the pod, built from
the API's own public address (`API_URL`), not the caller's. It reads nothing and
does not check access to the pod: the URL is not a secret, and the client that
uses it is refused at consent if the person cannot read the pod.

## `mcp_access.grants.list`

The caller's own live grants, newest first, optionally narrowed to one pod,
bounded at 200. With `everyone=true` and a `pod_id`, every member's live
grants to that pod — only for a caller who may manage the pod's members
(`pod.member.manage`); anyone else gets `403 MCP_ACCESS_NOT_POD_ADMIN`. Each
row carries the `user_id` of the person who connected it. Each row names the client as it
names itself, the scopes the person allowed, when they allowed it and when the
client last used it (accurate to a few minutes). Revoked grants are not listed.

## `mcp_access.grants.revoke`

Ends one of the caller's own live grants — or, for a caller who may manage a
pod's members, any member's grant to that pod — and deletes every token it
issued, in one transaction; the client's next MCP request is a 401 and it must
ask the person again. `404` for a grant that does not exist, is already
revoked, or is not the caller's to end — one answer for all three. Logs
`mcp_access.grant.revoked`; emits no event.

## `mcp_access.grants.subscription.delete`

Whoever may end the connection: the person who made it, or an admin of its
pod. Stops one event subscription; the connection and its other subscriptions
stay, and the client may subscribe again. `404` for no such subscription,
already stopped, or not yours to stop. `mcp_access.grants.list` lists each
connection's subscriptions under `listens_to`.
