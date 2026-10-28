# connectors contract

What every `connectors` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `connector.account.bind_installation` | POST | `/organizations/{organization_id}/connectors/accounts/{account_id}/github/installations` | Bind Account Installation |
| `connector.account.create` | POST | `/organizations/{organization_id}/connectors/accounts` | Create Account |
| `connector.account.delete` | DELETE | `/organizations/{organization_id}/connectors/accounts/{account_id}` | Delete Account |
| `connector.account.get` | GET | `/organizations/{organization_id}/connectors/accounts/{account_id}` | Get Account |
| `connector.account.installations` | GET | `/organizations/{organization_id}/connectors/accounts/{account_id}/github/installations` | Account Installations |
| `connector.account.list` | GET | `/organizations/{organization_id}/connectors/accounts` | List Accounts |
| `connector.account.pods` | GET | `/me/connectors/accounts/{account_id}/pods` | Where One Of My Accounts Is Used |
| `connector.account.update` | PATCH | `/organizations/{organization_id}/connectors/accounts/{account_id}` | Update Account |
| `connector.auth_config.create` | POST | `/organizations/{organization_id}/connectors/auth-configs` | Create Auth Config |
| `connector.auth_config.delete` | DELETE | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | Delete Auth Config |
| `connector.auth_config.get` | GET | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | Get Auth Config |
| `connector.auth_config.list` | GET | `/organizations/{organization_id}/connectors/auth-configs` | List Auth Configs |
| `connector.auth_config.refresh_operations` | POST | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}/operations/refresh` | Refresh Auth Config Operations |
| `connector.auth_config.update` | PATCH | `/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}` | Update Auth Config |
| `connector.connect_request.create` | POST | `/organizations/{organization_id}/connectors/connect-requests` | Initiate Connect Request |
| `connector.connect_request.install` | POST | `/organizations/{organization_id}/connectors/connect-requests/install` | Start Install Step |
| `connector.get` | GET | `/connectors/{connector_id}` | Get Connector |
| `connector.installation.impact` | GET | `/organizations/{organization_id}/installations/{auth_config_id}/impact` | What Removing An Installation Would Stop |
| `connector.list` | GET | `/connectors` | List Connectors |
| `connector.oauth.callback` | GET | `/connectors/connect-requests/oauth/callback` | OAuth Callback |
| `connector.operation.detail` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}` | Get Connector Operation Details |
| `connector.operation.details.batch` | POST | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/details` | Get Connector Operation Details In Batch |
| `connector.operation.discover` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/operations` | Discover Connector Operations |
| `connector.operation.execute` | POST | `/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}/execute` | Execute Connector Operation (deprecated) |
| `connector.operation.search` | GET | `/organizations/{organization_id}/connector-operations` | Search Connector Operations Across Installs |
| `connector.skill.get` | GET | `/connectors/{connector_id}/skill` | Get Connector Skill |
| `connector.status.get` | GET | `/organizations/{organization_id}/connectors/status` | Get Connector Status |
| `connector.trigger.get` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/triggers/{trigger_name}` | Get Connector Trigger |
| `connector.trigger.list` | GET | `/organizations/{organization_id}/connectors/{auth_config_name}/triggers` | List Connector Triggers |
| `pod.connector.ask_members` | POST | `/pods/{pod_id}/connectors/{slug}/ask-members` | Ask Members To Connect |
| `pod.connector.coverage` | GET | `/pods/{pod_id}/connectors/{slug}/coverage` | Who A Per-Member Connector Is Waiting On |
| `pod.connector.create` | POST | `/pods/{pod_id}/connectors` | Declare A Pod Connector |
| `pod.connector.delete` | DELETE | `/pods/{pod_id}/connectors/{slug}` | Remove Pod Connector |
| `pod.connector.get` | GET | `/pods/{pod_id}/connectors/{slug}` | Get Pod Connector |
| `pod.connector.list` | GET | `/pods/{pod_id}/connectors` | List Pod Connectors |
| `pod.connector.operation.execute` | POST | `/pods/{pod_id}/connectors/{slug}/operations/{operation_name}/execute` | Execute Pod Connector Operation |
| `pod.connector.operation.get` | GET | `/pods/{pod_id}/connectors/{slug}/operations/{operation_name}` | Get Pod Connector Operation |
| `pod.connector.operation.list` | GET | `/pods/{pod_id}/connectors/{slug}/operations` | Discover Pod Connector Operations |
| `pod.connector.update` | PATCH | `/pods/{pod_id}/connectors/{slug}` | Update Pod Connector |
| `pod.connector_account.add` | POST | `/pods/{pod_id}/connector-accounts` | Add One Of Your Accounts To The Pod |
| `pod.connector_account.list` | GET | `/pods/{pod_id}/connector-accounts` | List Accounts In Use In The Pod |
| `pod.connector_account.remove` | DELETE | `/pods/{pod_id}/connector-accounts/{pod_account_id}` | Remove An Account From The Pod |
| `pod.connector_account.share_as` | POST | `/pods/{pod_id}/connector-accounts/{pod_account_id}/share-as` | Share Your Account As A Pod Connector |
| `pod.connector_account.update` | PATCH | `/pods/{pod_id}/connector-accounts/{pod_account_id}` | Share Or Withdraw Your Account In The Pod |

<!-- /generated:operations -->
