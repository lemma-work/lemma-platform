# Lemma backend route inventory

Generated from the committed OpenAPI specification. Do not edit by hand;
run `uv run python scripts/generate_route_inventory.py`.

## agent

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/agents/{agent_name}` | `agent.delete` | Delete Agent |
| GET | `/agent-runtime/harnesses` | `agent.runtime.harnesses.list` | List Available Agent Harnesses |
| GET | `/organizations/{org_id}/agent-runtime/profiles` | `agent.runtime.profiles.list` | List Available Agent Runtime Profiles |
| GET | `/pods/{pod_id}/agents` | `agent.list` | List Agents |
| GET | `/pods/{pod_id}/agents/{agent_name}` | `agent.get` | Get Agent |
| GET | `/pods/{pod_id}/agents/{agent_name}/permissions` | `agent.permissions.get` | Get Agent Resource Permissions |
| GET | `/pods/{pod_id}/conversations` | `agent.conversation.list` | List Pod Agent Conversations |
| GET | `/pods/{pod_id}/conversations/{conversation_id}` | `agent.conversation.get` | Get Pod Conversation |
| GET | `/pods/{pod_id}/conversations/{conversation_id}/approvals` | `agent.conversation.approval.list` | List Agent Run Approvals |
| GET | `/pods/{pod_id}/conversations/{conversation_id}/messages` | `agent.conversation.message.list` | List Pod Conversation Messages |
| GET | `/pods/{pod_id}/conversations/{conversation_id}/stream` | `agent.conversation.stream` | Stream Pod Conversation |
| PATCH | `/pods/{pod_id}/agents/{agent_name}` | `agent.update` | Update Agent |
| PATCH | `/pods/{pod_id}/conversations/{conversation_id}` | `agent.conversation.update` | Update Pod Conversation |
| POST | `/organizations/{org_id}/agent-runtime/profiles` | `agent.runtime.profiles.create` | Create Agent Runtime Profile |
| POST | `/pods/{pod_id}/agents` | `agent.create` | Create Agent |
| POST | `/pods/{pod_id}/conversations` | `agent.conversation.create` | Create Pod Agent Conversation |
| POST | `/pods/{pod_id}/conversations/{conversation_id}/approvals/{approval_id}/decision` | `agent.conversation.approval.resolve` | Resolve User Approval |
| POST | `/pods/{pod_id}/conversations/{conversation_id}/messages` | `agent.conversation.message.send` | Send Pod Conversation Message |
| POST | `/pods/{pod_id}/conversations/{conversation_id}/stop` | `agent.conversation.stop` | Stop Pod Conversation |
| POST | `/pods/{pod_id}/widgets/{conversation_id}/{tool_call_id}/embed-token` | `widget.embed_token` | Mint Widget Embed URL |
| POST | `/tools/report-feedback` | `agent.tool.report_feedback` | Agent Report Feedback |
| POST | `/tools/web-search` | `agent.tool.web_search` | Agent Web Search |
| PUT | `/pods/{pod_id}/agents/{agent_name}/permissions` | `agent.permissions.replace` | Replace Agent Resource Permissions |

## agent_surfaces

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/surfaces/{surface_name}` | `agent.surface.delete` | Delete Surface |
| GET | `/pods/{pod_id}/available-surfaces` | `agent.surface.available` | List Available Surfaces |
| GET | `/pods/{pod_id}/surface-setup/{platform}` | `agent.surface.setup_guide` | Get Surface Setup Guide |
| GET | `/pods/{pod_id}/surfaces` | `agent.surface.list` | List Surfaces |
| GET | `/pods/{pod_id}/surfaces/{surface_name}` | `agent.surface.get` | Get Surface |
| GET | `/pods/{pod_id}/surfaces/{surface_name}/channels` | `agent.surface.channels` | List Surface Channels |
| GET | `/pods/{pod_id}/surfaces/{surface_name}/setup` | `agent.surface.setup` | Get Surface Setup |
| GET | `/surfaces/me` | `agent.surface.list_mine` | List My Surfaces |
| GET | `/surfaces/teams/admin-consent/callback` | `agent.surface.teams_admin_consent_callback` | Teams Admin Consent Callback |
| GET | `/surfaces/webhooks/{platform}` | `surface.webhook.verify` | Verify surface webhook using the platform callback URL |
| GET | `/surfaces/{surface_id}/webhook` | `surface.webhook.verify_surface` | Verify surface webhook using a surface-level callback URL |
| PATCH | `/pods/{pod_id}/surfaces/{surface_name}` | `agent.surface.update` | Update Surface |
| POST | `/pods/{pod_id}/surfaces` | `agent.surface.create` | Create Surface |
| POST | `/pods/{pod_id}/surfaces/{surface_name}/send` | `agent.surface.send` | Send Surface Message |
| POST | `/surfaces/webhooks/{platform}` | `surface.webhook.handle_platform` | Handle platform-level surface webhook |
| POST | `/surfaces/{surface_id}/webhook` | `surface.webhook.handle_surface` | Handle surface-level webhook |
| PUT | `/surfaces/me/default` | `agent.surface.set_my_default` | Set My Default Surface |

## apps

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/apps/{app_name}` | `app.delete` | Delete App |
| GET | `/pods/{pod_id}/apps` | `app.list` | List Apps |
| GET | `/pods/{pod_id}/apps/{app_name}` | `app.get` | Get App |
| GET | `/pods/{pod_id}/apps/{app_name}/assets` | `app.asset.root.get` | Get App Root Asset |
| GET | `/pods/{pod_id}/apps/{app_name}/assets/{asset_path}` | `app.asset.get` | Get App Asset |
| GET | `/pods/{pod_id}/apps/{app_name}/dist/archive` | `app.dist.archive.get` | Download App Dist Archive |
| GET | `/pods/{pod_id}/apps/{app_name}/source/archive` | `app.source.archive.get` | Download App Source Archive |
| PATCH | `/pods/{pod_id}/apps/{app_name}` | `app.update` | Update App |
| POST | `/pods/{pod_id}/apps` | `app.create` | Create App |
| POST | `/pods/{pod_id}/apps/from-widget` | `app.create_from_widget` | Save Widget As App |
| POST | `/pods/{pod_id}/apps/{app_name}/bundle` | `app.bundle.upload` | Upload App Bundle |

## connectors

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/organizations/{organization_id}/connectors/accounts/{account_id}` | `connector.account.delete` | Delete Account |
| DELETE | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | `connector.auth_config.delete` | Delete Auth Config |
| GET | `/connectors` | `connector.list` | List Connectors |
| GET | `/connectors/connect-requests/oauth/callback` | `connector.oauth.callback` | OAuth Callback |
| GET | `/connectors/{connector_id}` | `connector.get` | Get Connector |
| GET | `/connectors/{connector_id}/skill` | `connector.skill.get` | Get Connector Skill |
| GET | `/organizations/{organization_id}/connectors/accounts` | `connector.account.list` | List Accounts |
| GET | `/organizations/{organization_id}/connectors/accounts/{account_id}` | `connector.account.get` | Get Account |
| GET | `/organizations/{organization_id}/connectors/auth-configs` | `connector.auth_config.list` | List Auth Configs |
| GET | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | `connector.auth_config.get` | Get Auth Config |
| GET | `/organizations/{organization_id}/connectors/status` | `connector.status.get` | Get Connector Status |
| GET | `/organizations/{organization_id}/connectors/{auth_config_name}/operations` | `connector.operation.discover` | Discover Connector Operations |
| GET | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}` | `connector.operation.detail` | Get Connector Operation Details |
| GET | `/organizations/{organization_id}/connectors/{auth_config_name}/triggers` | `connector.trigger.list` | List Connector Triggers |
| GET | `/organizations/{organization_id}/connectors/{auth_config_name}/triggers/{trigger_name}` | `connector.trigger.get` | Get Connector Trigger |
| POST | `/organizations/{organization_id}/connectors/accounts` | `connector.account.create` | Create Account |
| POST | `/organizations/{organization_id}/connectors/auth-configs` | `connector.auth_config.create` | Create Auth Config |
| POST | `/organizations/{organization_id}/connectors/connect-requests` | `connector.connect_request.create` | Initiate Connect Request |
| POST | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/details` | `connector.operation.details.batch` | Get Connector Operation Details In Batch |
| POST | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}/execute` | `connector.operation.execute` | Execute Connector Operation |

## core

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| GET | `/health` | `health_check_health_get` | Health Check |

## datastore

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/datastore/files/by-path` | `file.delete` | Delete File Or Folder |
| DELETE | `/pods/{pod_id}/datastore/files/by-path/markdown` | `file.markdown.detach` | Detach Document Markdown |
| DELETE | `/pods/{pod_id}/datastore/tables/{table_name}` | `table.delete` | Delete Table |
| DELETE | `/pods/{pod_id}/datastore/tables/{table_name}/columns/{column_name}` | `table.column.remove` | Remove Column |
| DELETE | `/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}` | `record.delete` | Delete Record |
| GET | `/pods/{pod_id}/datastore/files` | `file.list` | List Files |
| GET | `/pods/{pod_id}/datastore/files/by-path` | `file.get` | Get File |
| GET | `/pods/{pod_id}/datastore/files/children` | `file.children.list` | List a document's derived child files |
| GET | `/pods/{pod_id}/datastore/files/children/content` | `file.child.get` | Fetch a document's child artifact by path |
| GET | `/pods/{pod_id}/datastore/files/download` | `file.download` | Download File |
| GET | `/pods/{pod_id}/datastore/files/tree` | `file.tree` | Get Directory Tree |
| GET | `/pods/{pod_id}/datastore/files/url` | `file.url` | Get a short-lived URL for a file |
| GET | `/pods/{pod_id}/datastore/tables` | `table.list` | List Tables |
| GET | `/pods/{pod_id}/datastore/tables/{table_name}` | `table.get` | Get Table |
| GET | `/pods/{pod_id}/datastore/tables/{table_name}/records` | `record.list` | List Records |
| GET | `/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}` | `record.get` | Get Record |
| PATCH | `/pods/{pod_id}/datastore/files/by-path` | `file.update` | Update File |
| PATCH | `/pods/{pod_id}/datastore/tables/{table_name}` | `table.update` | Update Table |
| PATCH | `/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}` | `record.update` | Update Record |
| POST | `/pods/{pod_id}/datastore/files` | `file.upload` | Upload File |
| POST | `/pods/{pod_id}/datastore/files/folders` | `file.folder.create` | Create Folder |
| POST | `/pods/{pod_id}/datastore/files/search` | `file.search` | Search Files |
| POST | `/pods/{pod_id}/datastore/files/signed-url` | `file.signed_url` | Create a public, hit-capped signed URL for a file |
| POST | `/pods/{pod_id}/datastore/query` | `query.execute` | Execute Query |
| POST | `/pods/{pod_id}/datastore/tables` | `table.create` | Create Table |
| POST | `/pods/{pod_id}/datastore/tables/{table_name}/columns` | `table.column.add` | Add Column |
| POST | `/pods/{pod_id}/datastore/tables/{table_name}/records` | `record.create` | Create Record |
| POST | `/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/create` | `record.bulk_create` | Bulk Create |
| POST | `/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/delete` | `record.bulk_delete` | Bulk Delete |
| POST | `/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/update` | `record.bulk_update` | Bulk Update |
| PUT | `/pods/{pod_id}/datastore/files/by-path/markdown` | `file.markdown.attach` | Attach Document Markdown |

## function

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/functions/{function_name}` | `function.delete` | Delete Function |
| GET | `/pods/{pod_id}/functions` | `function.list` | List Functions |
| GET | `/pods/{pod_id}/functions/{function_name}` | `function.get` | Get Function |
| GET | `/pods/{pod_id}/functions/{function_name}/permissions` | `function.permissions.get` | Get Function Resource Permissions |
| GET | `/pods/{pod_id}/functions/{function_name}/runs` | `function.run.list` | List Runs |
| GET | `/pods/{pod_id}/functions/{function_name}/runs/{run_id}` | `function.run.get` | Get Run |
| PATCH | `/pods/{pod_id}/functions/{function_name}` | `function.update` | Update Function |
| POST | `/pods/{pod_id}/functions` | `function.create` | Create Function |
| POST | `/pods/{pod_id}/functions/{function_name}/runs` | `function.run` | Execute Function |
| PUT | `/pods/{pod_id}/functions/{function_name}/permissions` | `function.permissions.replace` | Replace Function Resource Permissions |

## icon

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| GET | `/public/icons/{icon_path}` | `icon.public.get` | Get Public Icon |
| POST | `/icons/upload` | `icon.upload` | Upload Icon |

## identity

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/organizations/invitations/{invitation_id}` | `org.invitation.revoke` | Revoke Invitation |
| DELETE | `/organizations/{org_id}/members/{member_id}` | `org.member.remove` | Remove Member |
| GET | `/auth/verify-token` | `auth.verify_token` | Verify access token |
| GET | `/organizations` | `org.list` | List My Organizations |
| GET | `/organizations/invitations` | `org.invitation.list_mine` | List My Invitations |
| GET | `/organizations/invitations/{invitation_id}` | `org.invitation.get` | Get Organization Invitation |
| GET | `/organizations/slug-availability` | `org.slug_availability` | Check Organization Slug Availability |
| GET | `/organizations/suggested` | `org.suggested` | Get Suggested Organizations |
| GET | `/organizations/{org_id}` | `org.get` | Get Organization |
| GET | `/organizations/{org_id}/invitations` | `org.invitation.list` | List Organization Invitations |
| GET | `/organizations/{org_id}/members` | `org.member.list` | List Organization Members |
| GET | `/users/me` | `user.current.get` | Get Current User |
| GET | `/users/me/profile` | `user.profile.get` | Get User Profile |
| PATCH | `/organizations/{org_id}` | `org.update` | Update Organization |
| PATCH | `/organizations/{org_id}/members/{member_id}/role` | `org.member.update_role` | Update Member Role |
| POST | `/organizations` | `org.create` | Create Organization |
| POST | `/organizations/invitations/{invitation_id}/accept` | `org.invitation.accept` | Accept Invitation |
| POST | `/organizations/{org_id}/invitations` | `org.invitation.invite` | Invite Member |
| POST | `/organizations/{org_id}/join` | `org.join_auto_join` | Join Auto-Join Organization |
| POST | `/users/me/profile` | `user.profile.upsert` | Create or Update Profile |

## pod

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}` | `pod.delete` | Delete Pod |
| DELETE | `/pods/{pod_id}/members/{pod_member_id}` | `pod.member.remove` | Remove Pod Member |
| DELETE | `/pods/{pod_id}/resources/{resource_type}/{resource_name}/access/grantees/{grantee_type}/{grantee_id}` | `pod.resource_access.grant.delete` | Delete Resource Access Grant |
| DELETE | `/pods/{pod_id}/roles/{role_name}` | `pod.roles.delete` | Delete Pod Role |
| GET | `/pods/organization/{organization_id}` | `pod.list` | List PodS by Organization |
| GET | `/pods/{pod_id}` | `pod.get` | Get Pod |
| GET | `/pods/{pod_id}/join-requests` | `pod.join_request.list` | List Pod Join Requests |
| GET | `/pods/{pod_id}/join-requests/me` | `pod.join_request.me` | Get My Pod Join Request |
| GET | `/pods/{pod_id}/members` | `pod.member.list` | List Pod Members |
| GET | `/pods/{pod_id}/members/lookup/by-email` | `pod.member.lookup_by_email` | Lookup Pod Member By Email |
| GET | `/pods/{pod_id}/members/lookup/by-user-id/{user_id}` | `pod.member.lookup_by_user_id` | Lookup Pod Member By User ID |
| GET | `/pods/{pod_id}/members/{pod_member_id}` | `pod.member.get` | Get Pod Member |
| GET | `/pods/{pod_id}/permissions/catalog` | `pod.permissions.catalog` | Get Pod Permission Catalog |
| GET | `/pods/{pod_id}/permissions/me` | `pod.permissions.me` | Get My Pod Permissions |
| GET | `/pods/{pod_id}/resources/{resource_type}/{resource_name}/access` | `pod.resource_access.get` | Get Resource Access |
| GET | `/pods/{pod_id}/roles` | `pod.roles.list` | List Pod Roles |
| GET | `/pods/{pod_id}/roles/{role_name}/permissions` | `pod.role.permissions.get` | Get Pod Role Permissions |
| PATCH | `/pods/{pod_id}/members/{pod_member_id}/roles` | `pod.member.update_roles` | Update Member Roles |
| PATCH | `/pods/{pod_id}/roles/{role_name}` | `pod.roles.update` | Update Pod Role |
| POST | `/pods` | `pod.create` | Create Pod |
| POST | `/pods/{pod_id}/join` | `pod.join` | Join Pod |
| POST | `/pods/{pod_id}/join-requests` | `pod.join_request.create` | Create Pod Join Request |
| POST | `/pods/{pod_id}/join-requests/{join_request_id}/approve` | `pod.join_request.approve` | Approve Pod Join Request |
| POST | `/pods/{pod_id}/members` | `pod.member.add` | Add Pod Member |
| POST | `/pods/{pod_id}/roles` | `pod.roles.create` | Create Pod Role |
| PUT | `/pods/{pod_id}` | `pod.update` | Update Pod |
| PUT | `/pods/{pod_id}/resources/{resource_type}/{resource_name}/access/grantees/{grantee_type}/{grantee_id}` | `pod.resource_access.grant.replace` | Replace Resource Access Grant |
| PUT | `/pods/{pod_id}/roles/{role_name}/permissions` | `pod.role.permissions.replace` | Replace Pod Role Permissions |

## pod_bundle

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/bundle/imports/{import_id}` | `pod.bundle.import.cancel` | Cancel Pod Import |
| GET | `/pods/bundle/download` | `pod.bundle.download` | Download A Bundle Archive |
| GET | `/pods/{pod_id}/bundle/exports/{export_id}` | `pod.bundle.export.get` | Get Pod Export Status |
| GET | `/pods/{pod_id}/bundle/imports/{import_id}` | `pod.bundle.import.get` | Get Pod Import Status |
| GET | `/pods/{pod_id}/bundle/imports/{import_id}/events` | `pod.bundle.import.events` | Stream Pod Import Progress |
| GET | `/pods/{pod_id}/bundle/publishes/{publish_id}` | `pod.bundle.publish.get` | Get Pod Publish Status |
| GET | `/pods/{pod_id}/bundle/publishes/{publish_id}/events` | `pod.bundle.publish.events` | Stream Pod Publish Progress |
| POST | `/pods/{pod_id}/bundle/exports` | `pod.bundle.export.start` | Start Pod Export |
| POST | `/pods/{pod_id}/bundle/imports` | `pod.bundle.import.start` | Start Pod Import |
| POST | `/pods/{pod_id}/bundle/imports/{import_id}/apply` | `pod.bundle.import.apply` | Apply Pod Import |
| POST | `/pods/{pod_id}/bundle/imports/{import_id}/replan` | `pod.bundle.import.replan` | Re-plan Pod Import |
| POST | `/pods/{pod_id}/bundle/publishes` | `pod.bundle.publish.start` | Publish Pod To GitHub |
| POST | `/pods/{pod_id}/bundle/uploads` | `pod.bundle.upload` | Stage A Local Bundle Upload |

## schedule

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/schedules/{schedule_id}` | `schedule.delete` | Delete Schedule |
| GET | `/pods/{pod_id}/schedules` | `schedule.list` | List Schedules |
| GET | `/pods/{pod_id}/schedules/{schedule_id}` | `schedule.get` | Get Schedule |
| GET | `/pods/{pod_id}/schedules/{schedule_id}/runs` | `schedule.run.list` | List Schedule Runs |
| PATCH | `/pods/{pod_id}/schedules/{schedule_id}` | `schedule.update` | Update Schedule |
| POST | `/pods/{pod_id}/schedules` | `schedule.create` | Create Schedule |
| POST | `/pods/{pod_id}/schedules/{schedule_id}/runs/{run_id}/retry` | `schedule.run.retry` | Retry Schedule Run |

## usage

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| GET | `/usage/organizations/{organization_id}/events` | `usage.organization.events.list` | List Usage Events |
| GET | `/usage/organizations/{organization_id}/limits` | `usage.organization.limits.get` | Get Usage Limits |
| GET | `/usage/organizations/{organization_id}/me` | `usage.organization.me.summary.get` | Get My Usage |
| GET | `/usage/organizations/{organization_id}/stats` | `usage.organization.stats.get` | Get Usage Stats |
| GET | `/usage/organizations/{organization_id}/summary` | `usage.organization.summary.get` | Get Organization Usage Summary |

## workflow

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| DELETE | `/pods/{pod_id}/workflows/{workflow_name}` | `workflow.delete` | Delete Workflow |
| GET | `/pods/{pod_id}/workflow-runs/waiting/assigned-to-me` | `workflow.run.waiting_assigned_to_me` | List Workflow Runs Waiting For Current User |
| GET | `/pods/{pod_id}/workflow-runs/{run_id}` | `workflow.run.get` | Get Workflow Run |
| GET | `/pods/{pod_id}/workflow-runs/{run_id}/visualize` | `workflow.run.visualize` | Visualize Workflow Run |
| GET | `/pods/{pod_id}/workflows` | `workflow.list` | List Workflows |
| GET | `/pods/{pod_id}/workflows/{workflow_name}` | `workflow.get` | Get Workflow |
| GET | `/pods/{pod_id}/workflows/{workflow_name}/runs` | `workflow.run.list` | List Workflow Runs |
| GET | `/pods/{pod_id}/workflows/{workflow_name}/visualize` | `workflow.visualize` | Visualize Workflow |
| PATCH | `/pods/{pod_id}/workflows/{workflow_name}` | `workflow.update` | Update Workflow Metadata |
| POST | `/pods/{pod_id}/workflow-runs/{run_id}/cancel` | `workflow.run.cancel` | Cancel Workflow Run |
| POST | `/pods/{pod_id}/workflow-runs/{run_id}/form` | `workflow.run.form.submit` | Submit Workflow Run Form |
| POST | `/pods/{pod_id}/workflows` | `workflow.create` | Create Workflow |
| POST | `/pods/{pod_id}/workflows/{workflow_name}/runs` | `workflow.run.create` | Create Workflow Run |
| PUT | `/pods/{pod_id}/workflows/{workflow_name}/graph` | `workflow.graph.update` | Update Workflow Graph |

## workspace

| Method | Path | Operation ID | Summary |
| --- | --- | --- | --- |
| GET | `/workspace/me` | `workspace.me` | Get current workspace state |
| POST | `/workspace/apps/browser/access` | `workspace.browser.access` | Create workspace browser access URL |
