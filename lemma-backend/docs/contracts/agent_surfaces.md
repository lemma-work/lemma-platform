# agent_surfaces contract

What every `agent_surfaces` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `agent.notice.send` | POST | `/pods/{pod_id}/members/{member}/notices` | Send Notice |
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
| `agent.surface.slack_app.create` | POST | `/pods/{pod_id}/surface-setup/slack/apps` | Create Slack App |
| `agent.surface.slack_app_creation.forget` | DELETE | `/pods/{pod_id}/surface-setup/slack/app-creation/{team_id}` | Forget Slack Configuration Token |
| `agent.surface.slack_app_creation.list` | GET | `/pods/{pod_id}/surface-setup/slack/app-creation` | List Slack App Workspaces |
| `agent.surface.slack_app_creation.save` | PUT | `/pods/{pod_id}/surface-setup/slack/app-creation` | Save Slack Configuration Token |
| `agent.surface.slack_install.availability` | GET | `/surface-setup/slack/install` | Slack Install Availability |
| `agent.surface.slack_install.start` | POST | `/surface-setup/slack/install` | Start Slack Install Flow |
| `agent.surface.slack_manager.bind` | PUT | `/pods/{pod_id}/surface-setup/slack/manager` | Bind Slack Manager |
| `agent.surface.slack_manager.list` | GET | `/pods/{pod_id}/surface-setup/slack/manager` | List Slack Managed Workspaces |
| `agent.surface.slack_manifest` | GET | `/surface-setup/slack/manifest` | Get Slack App Manifest |
| `agent.surface.slack_person.sign_in` | POST | `/surface-setup/slack/people/sign-in` | Start Slack Sign In |
| `agent.surface.teams_admin_consent_callback` | GET | `/surfaces/teams/admin-consent/callback` | Teams Admin Consent Callback |
| `agent.surface.telegram_managed.get` | GET | `/pods/{pod_id}/telegram-bot-setups/{setup_id}` | Get Telegram Managed Bot Setup |
| `agent.surface.telegram_managed.start` | POST | `/pods/{pod_id}/telegram-bot-setups` | Start Telegram Managed Bot Setup |
| `agent.surface.update` | PATCH | `/pods/{pod_id}/surfaces/{surface_name}` | Update Surface |
| `agent.web_widget.create` | POST | `/pods/{pod_id}/web-widgets` | Create Widget |
| `agent.web_widget.delete` | DELETE | `/pods/{pod_id}/web-widgets/{widget_id}` | Delete Widget |
| `agent.web_widget.list` | GET | `/pods/{pod_id}/web-widgets` | List Widgets |
| `agent.web_widget.reissue` | POST | `/pods/{pod_id}/web-widgets/{widget_id}/secret` | Reissue Widget |
| `agent.web_widget.update` | PATCH | `/pods/{pod_id}/web-widgets/{widget_id}` | Update Widget |
| `channel.source.get` | GET | `/pods/{pod_id}/channels/{channel_id}/sources/{source_id}` | Get Source |
| `channel.source.update` | PATCH | `/pods/{pod_id}/channels/{channel_id}/sources/{source_id}` | Update Source |
| `channel.telegram_link.create` | POST | `/pods/{pod_id}/channels/telegram-links` | Telegram Link |
| `channel.whatsapp_group.start` | POST | `/pods/{pod_id}/channels/whatsapp-groups` | Start Whatsapp Group |
| `contact.outreach.send` | POST | `/pods/{pod_id}/contacts/{contact_id}/outreach` | Send Outreach |
| `public.web.challenge.read` | GET | `/public/web/{public_key}/challenge` | Web Challenge |
| `public.web.code.send` | POST | `/public/web/{public_key}/code` | Web Send Code |
| `public.web.code.verify` | POST | `/public/web/{public_key}/code/verify` | Web Verify Code |
| `public.web.history.read` | GET | `/public/web/{public_key}/history` | Web Read History |
| `public.web.message.send` | POST | `/public/web/{public_key}/messages` | Web Send Message |
| `public.web.row.add` | POST | `/public/web/{public_key}/rows` | Web Add Row |
| `public.web.rows.read` | GET | `/public/web/{public_key}/rows` | Web Read Rows |
| `public.web.session.start` | POST | `/public/web/{public_key}/session` | Web Start Session |
| `public.web.stream.read` | GET | `/public/web/{public_key}/stream` | Web Stream Answers |
| `public.web.table.read` | GET | `/public/web/{public_key}/table` | Web Read Table |
| `surface.webhook.handle_platform` | POST | `/surfaces/webhooks/{platform}` | Handle platform-level surface webhook |
| `surface.webhook.handle_surface` | POST | `/surfaces/{surface_id}/webhook` | Handle surface-level webhook |
| `surface.webhook.handle_telegram_manager` | POST | `/surfaces/webhooks/telegram-manager` | Handle Telegram manager-bot webhook |
| `surface.webhook.handle_whatsapp_number` | POST | `/surfaces/webhooks/whatsapp/numbers/{phone_number_id}` | Handle a webhook delivered to one pooled WhatsApp number |
| `surface.webhook.verify` | GET | `/surfaces/webhooks/{platform}` | Verify surface webhook using the platform callback URL |
| `surface.webhook.verify_surface` | GET | `/surfaces/{surface_id}/webhook` | Verify surface webhook using a surface-level callback URL |
| `surface.webhook.verify_whatsapp_number` | GET | `/surfaces/webhooks/whatsapp/numbers/{phone_number_id}` | Verify a pooled WhatsApp number's own callback URL |
| `telegram_identity_link.confirm` | POST | `/pods/{pod_id}/telegram-identity-links/{code}/confirm` | Confirm Identity Link |
| `telegram_identity_link.create` | POST | `/pods/{pod_id}/telegram-identity-links` | Create Identity Link |
| `telegram_identity_link.decline` | POST | `/pods/{pod_id}/telegram-identity-links/{code}/decline` | Decline Identity Link |
| `telegram_identity_link.get` | GET | `/pods/{pod_id}/telegram-identity-links/{code}` | Get Identity Link |

<!-- /generated:operations -->
