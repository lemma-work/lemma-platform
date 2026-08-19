# pod contract

What every `pod` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `pod.create` | POST | `/pods` | Create Pod |
| `pod.delete` | DELETE | `/pods/{pod_id}` | Delete Pod |
| `pod.get` | GET | `/pods/{pod_id}` | Get Pod |
| `pod.join` | POST | `/pods/{pod_id}/join` | Join Pod |
| `pod.join_request.approve` | POST | `/pods/{pod_id}/join-requests/{join_request_id}/approve` | Approve Pod Join Request |
| `pod.join_request.create` | POST | `/pods/{pod_id}/join-requests` | Create Pod Join Request |
| `pod.join_request.list` | GET | `/pods/{pod_id}/join-requests` | List Pod Join Requests |
| `pod.join_request.me` | GET | `/pods/{pod_id}/join-requests/me` | Get My Pod Join Request |
| `pod.list` | GET | `/pods/organization/{organization_id}` | List PodS by Organization |
| `pod.member.add` | POST | `/pods/{pod_id}/members` | Add Pod Member |
| `pod.member.get` | GET | `/pods/{pod_id}/members/{pod_member_id}` | Get Pod Member |
| `pod.member.list` | GET | `/pods/{pod_id}/members` | List Pod Members |
| `pod.member.lookup_by_email` | GET | `/pods/{pod_id}/members/lookup/by-email` | Lookup Pod Member By Email |
| `pod.member.lookup_by_user_id` | GET | `/pods/{pod_id}/members/lookup/by-user-id/{user_id}` | Lookup Pod Member By User ID |
| `pod.member.remove` | DELETE | `/pods/{pod_id}/members/{pod_member_id}` | Remove Pod Member |
| `pod.member.update_roles` | PATCH | `/pods/{pod_id}/members/{pod_member_id}/roles` | Update Member Roles |
| `pod.permissions.catalog` | GET | `/pods/{pod_id}/permissions/catalog` | Get Pod Permission Catalog |
| `pod.permissions.me` | GET | `/pods/{pod_id}/permissions/me` | Get My Pod Permissions |
| `pod.resource.preview` | GET | `/pods/{pod_id}/resources/{resource_type}/preview` | Preview a Shared Resource |
| `pod.resource_access.get` | GET | `/pods/{pod_id}/resources/{resource_type}/{resource_name}/access` | Get Resource Access |
| `pod.resource_access.grant.delete` | DELETE | `/pods/{pod_id}/resources/{resource_type}/{resource_name}/access/grantees/{grantee_type}/{grantee_id}` | Delete Resource Access Grant |
| `pod.resource_access.grant.replace` | PUT | `/pods/{pod_id}/resources/{resource_type}/{resource_name}/access/grantees/{grantee_type}/{grantee_id}` | Replace Resource Access Grant |
| `pod.role.permissions.get` | GET | `/pods/{pod_id}/roles/{role_name}/permissions` | Get Pod Role Permissions |
| `pod.role.permissions.replace` | PUT | `/pods/{pod_id}/roles/{role_name}/permissions` | Replace Pod Role Permissions |
| `pod.roles.create` | POST | `/pods/{pod_id}/roles` | Create Pod Role |
| `pod.roles.delete` | DELETE | `/pods/{pod_id}/roles/{role_name}` | Delete Pod Role |
| `pod.roles.list` | GET | `/pods/{pod_id}/roles` | List Pod Roles |
| `pod.roles.update` | PATCH | `/pods/{pod_id}/roles/{role_name}` | Update Pod Role |
| `pod.update` | PUT | `/pods/{pod_id}` | Update Pod |

<!-- /generated:operations -->
