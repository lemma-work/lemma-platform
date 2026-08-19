# connectors contract

What every `connectors` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `connector.account.create` | POST | `/organizations/{organization_id}/connectors/accounts` | Create Account |
| `connector.account.delete` | DELETE | `/organizations/{organization_id}/connectors/accounts/{account_id}` | Delete Account |
| `connector.account.get` | GET | `/organizations/{organization_id}/connectors/accounts/{account_id}` | Get Account |
| `connector.account.list` | GET | `/organizations/{organization_id}/connectors/accounts` | List Accounts |
| `connector.auth_config.create` | POST | `/organizations/{organization_id}/connectors/auth-configs` | Create Auth Config |
| `connector.auth_config.delete` | DELETE | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | Delete Auth Config |
| `connector.auth_config.get` | GET | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | Get Auth Config |
| `connector.auth_config.list` | GET | `/organizations/{organization_id}/connectors/auth-configs` | List Auth Configs |
| `connector.auth_config.refresh_operations` | POST | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}/operations/refresh` | Refresh Auth Config Operations |
| `connector.auth_config.update` | PATCH | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | Update Auth Config |
| `connector.connect_request.create` | POST | `/organizations/{organization_id}/connectors/connect-requests` | Initiate Connect Request |
| `connector.get` | GET | `/connectors/{connector_id}` | Get Connector |
| `connector.list` | GET | `/connectors` | List Connectors |
| `connector.oauth.callback` | GET | `/connectors/connect-requests/oauth/callback` | OAuth Callback |
| `connector.operation.detail` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}` | Get Connector Operation Details |
| `connector.operation.details.batch` | POST | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/details` | Get Connector Operation Details In Batch |
| `connector.operation.discover` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/operations` | Discover Connector Operations |
| `connector.operation.execute` | POST | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}/execute` | Execute Connector Operation |
| `connector.operation.search` | GET | `/organizations/{organization_id}/connector-operations` | Search Connector Operations Across Installs |
| `connector.skill.get` | GET | `/connectors/{connector_id}/skill` | Get Connector Skill |
| `connector.status.get` | GET | `/organizations/{organization_id}/connectors/status` | Get Connector Status |
| `connector.trigger.get` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/triggers/{trigger_name}` | Get Connector Trigger |
| `connector.trigger.list` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/triggers` | List Connector Triggers |

<!-- /generated:operations -->
