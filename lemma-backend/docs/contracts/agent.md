# agent contract

What every `agent` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `agent.conversation.approval.list` | GET | `/pods/{pod_id}/conversations/{conversation_id}/approvals` | List Agent Run Approvals |
| `agent.conversation.approval.resolve` | POST | `/pods/{pod_id}/conversations/{conversation_id}/approvals/{approval_id}/decision` | Resolve User Approval |
| `agent.conversation.create` | POST | `/pods/{pod_id}/conversations` | Create Pod Agent Conversation |
| `agent.conversation.get` | GET | `/pods/{pod_id}/conversations/{conversation_id}` | Get Pod Conversation |
| `agent.conversation.list` | GET | `/pods/{pod_id}/conversations` | List Pod Agent Conversations |
| `agent.conversation.message.list` | GET | `/pods/{pod_id}/conversations/{conversation_id}/messages` | List Pod Conversation Messages |
| `agent.conversation.message.send` | POST | `/pods/{pod_id}/conversations/{conversation_id}/messages` | Send Pod Conversation Message |
| `agent.conversation.retry` | POST | `/pods/{pod_id}/conversations/{conversation_id}/retry` | Retry Failed Pod Conversation Run |
| `agent.conversation.stop` | POST | `/pods/{pod_id}/conversations/{conversation_id}/stop` | Stop Pod Conversation |
| `agent.conversation.stream` | GET | `/pods/{pod_id}/conversations/{conversation_id}/stream` | Stream Pod Conversation |
| `agent.conversation.update` | PATCH | `/pods/{pod_id}/conversations/{conversation_id}` | Update Pod Conversation |
| `agent.create` | POST | `/pods/{pod_id}/agents` | Create Agent |
| `agent.delete` | DELETE | `/pods/{pod_id}/agents/{agent_name}` | Delete Agent |
| `agent.get` | GET | `/pods/{pod_id}/agents/{agent_name}` | Get Agent |
| `agent.host.events.append` | POST | `/agent-host/events:append` | Append Agent Host Events |
| `agent.host.harnesses.list` | GET | `/me/runtime/agent-hosts/{host_id}/harnesses` | List Agent Host Harnesses |
| `agent.host.harnesses.publish` | PUT | `/agent-host/harnesses` | Publish Agent Host Harnesses |
| `agent.host.list` | GET | `/me/runtime/agent-hosts` | List Agent Hosts |
| `agent.host.pairing.complete` | POST | `/agent-host/pairings:complete` | Complete Agent Host Pairing |
| `agent.host.pairing.create` | POST | `/me/runtime/agent-host-pairings` | Create Agent Host Pairing |
| `agent.host.poll` | POST | `/agent-host/poll` | Poll Agent Host Commands |
| `agent.host.revoke` | DELETE | `/me/runtime/agent-hosts/{host_id}` | Revoke Agent Host |
| `agent.host.self_revoke` | POST | `/agent-host/revoke` | Self Revoke Agent Host |
| `agent.list` | GET | `/pods/{pod_id}/agents` | List Agents |
| `agent.permissions.get` | GET | `/pods/{pod_id}/agents/{agent_name}/permissions` | Get Agent Resource Permissions |
| `agent.permissions.replace` | PUT | `/pods/{pod_id}/agents/{agent_name}/permissions` | Replace Agent Resource Permissions |
| `agent.runtime.profiles.archive` | DELETE | `/organizations/{org_id}/agent-runtime/profiles/{profile_id}` | Archive Agent Runtime Profile |
| `agent.runtime.profiles.create` | POST | `/organizations/{org_id}/agent-runtime/profiles` | Create Agent Runtime Profile |
| `agent.runtime.profiles.get` | GET | `/organizations/{org_id}/agent-runtime/profiles/{profile_id}` | Get Agent Runtime Profile |
| `agent.runtime.profiles.list` | GET | `/organizations/{org_id}/agent-runtime/profiles` | List Available Agent Runtime Profiles |
| `agent.runtime.profiles.restore` | POST | `/organizations/{org_id}/agent-runtime/profiles/{profile_id}:restore` | Restore Agent Runtime Profile |
| `agent.runtime.profiles.update` | PATCH | `/organizations/{org_id}/agent-runtime/profiles/{profile_id}` | Update Agent Runtime Profile |
| `agent.tool.report_feedback` | POST | `/tools/report-feedback` | Agent Report Feedback |
| `agent.tool.web_search` | POST | `/tools/web-search` | Agent Web Search |
| `agent.update` | PATCH | `/pods/{pod_id}/agents/{agent_name}` | Update Agent |
| `widget.embed_token` | POST | `/pods/{pod_id}/widgets/{conversation_id}/{tool_call_id}/embed-token` | Mint Widget Embed URL |

<!-- /generated:operations -->
