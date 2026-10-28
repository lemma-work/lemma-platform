# calls contract

What every `calls` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `call.config` | GET | `/pods/{pod_id}/calls/config` | Call configuration |
| `call.open` | POST | `/pods/{pod_id}/calls` | Open a call |

<!-- /generated:operations -->

## `call.config`

Says which voice engines this server can open a call with.

- **Who:** a member of the pod.
- **Changes:** nothing.
- **Answers:** `engines`, the configured default first; empty when no engine
  has a key, which is how a client knows not to offer a call.

## `call.open`

Opens a call with a pod agent. The call is a new conversation owned by the
caller; its agent coordinates the call.

- **Who:** a member of the pod who may run the agent (`agent_name`, or the
  pod's default agent when it is omitted). A person who may not run it is
  refused as opening any conversation with it would be.
- **Changes:** one conversation, marked as a call in its metadata, with the
  coordinator's brief as its instructions and, when `CALL_COORDINATOR_MODEL`
  is set, its runtime pinned to that model. Nothing runs until the caller
  speaks on the socket.
- **Answers:** `call_id` (the conversation's id) and `engines`.
- **Refusals:** 503 `CALLS_NOT_CONFIGURED` when no voice engine has a key;
  404 and 403 as for opening a conversation with the agent.

The socket, `/pods/{pod_id}/calls/{call_id}/socket`, is not an OpenAPI
operation; its frames and close codes are in
[the module doc](../modules/calls.md#the-socket). Only the person who opened
the call may hold it, and they may hold at most `CALLS_PER_USER` calls at once.
