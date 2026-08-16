# agent_surfaces contract

What every `agent_surfaces` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `agent.surface.available` | GET | `/pods/{pod_id}/available-surfaces` | List Available Surfaces |
| `agent.surface.channels` | GET | `/pods/{pod_id}/surfaces/{surface_name}/channels` | List Surface Channels |
| `agent.surface.create` | POST | `/pods/{pod_id}/surfaces` | Create Surface |
| `agent.surface.delete` | DELETE | `/pods/{pod_id}/surfaces/{surface_name}` | Delete Surface |
| `agent.surface.get` | GET | `/pods/{pod_id}/surfaces/{surface_name}` | Get Surface |
| `agent.surface.list` | GET | `/pods/{pod_id}/surfaces` | List Surfaces |
| `agent.surface.list_mine` | GET | `/surfaces/me` | List My Surfaces |
| `agent.surface.send` | POST | `/pods/{pod_id}/surfaces/{surface_name}/send` | Send Surface Message |
| `agent.surface.set_my_default` | PUT | `/surfaces/me/default` | Set My Default Surface |
| `agent.surface.setup` | GET | `/pods/{pod_id}/surfaces/{surface_name}/setup` | Get Surface Setup |
| `agent.surface.setup_guide` | GET | `/pods/{pod_id}/surface-setup/{platform}` | Get Surface Setup Guide |
| `agent.surface.slack_manifest` | GET | `/surface-setup/slack/manifest` | Get Slack App Manifest |
| `agent.surface.teams_admin_consent_callback` | GET | `/surfaces/teams/admin-consent/callback` | Teams Admin Consent Callback |
| `agent.surface.telegram_managed.get` | GET | `/pods/{pod_id}/telegram-bot-setups/{setup_id}` | Get Telegram Managed Bot Setup |
| `agent.surface.telegram_managed.start` | POST | `/pods/{pod_id}/telegram-bot-setups` | Start Telegram Managed Bot Setup |
| `agent.surface.update` | PATCH | `/pods/{pod_id}/surfaces/{surface_name}` | Update Surface |
| `notification.acknowledge` | POST | `/pods/{pod_id}/notifications/{notification_id}/acknowledge` | Acknowledge A Notification |
| `notification.list` | GET | `/pods/{pod_id}/notifications` | List My Notifications |
| `notification.mark_all_read` | POST | `/pods/{pod_id}/notifications/read-all` | Mark All My Notifications Read |
| `notification.mark_read` | POST | `/pods/{pod_id}/notifications/{notification_id}/read` | Mark Notification Read |
| `notification.respond` | POST | `/pods/{pod_id}/notifications/{notification_id}/respond` | Respond To A Notification |
| `notification.send` | POST | `/pods/{pod_id}/notifications` | Notify A Pod Member |
| `notification.unread_count` | GET | `/pods/{pod_id}/notifications/unread-count` | Count My Unread Notifications |
| `surface.webhook.handle_platform` | POST | `/surfaces/webhooks/{platform}` | Handle platform-level surface webhook |
| `surface.webhook.handle_surface` | POST | `/surfaces/{surface_id}/webhook` | Handle surface-level webhook |
| `surface.webhook.handle_telegram_manager` | POST | `/surfaces/webhooks/telegram-manager` | Handle Telegram manager-bot webhook |
| `surface.webhook.verify` | GET | `/surfaces/webhooks/{platform}` | Verify surface webhook using the platform callback URL |
| `surface.webhook.verify_surface` | GET | `/surfaces/{surface_id}/webhook` | Verify surface webhook using a surface-level callback URL |

<!-- /generated:operations -->
