# pod contract

What every `pod` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `outcome.clear` | DELETE | `/pods/{pod_id}/outcome` | Clear what the pod owns |
| `outcome.set` | PUT | `/pods/{pod_id}/outcome` | Set what the pod owns |
| `pod.create` | POST | `/pods` | Create Pod |
| `pod.delete` | DELETE | `/pods/{pod_id}` | Delete Pod |
| `pod.get` | GET | `/pods/{pod_id}` | Get Pod |
| `pod.join` | POST | `/pods/{pod_id}/join` | Join Pod |
| `pod.join_request.approve` | POST | `/pods/{pod_id}/join-requests/{join_request_id}/approve` | Approve Pod Join Request |
| `pod.join_request.create` | POST | `/pods/{pod_id}/join-requests` | Create Pod Join Request |
| `pod.join_request.list` | GET | `/pods/{pod_id}/join-requests` | List Pod Join Requests |
| `pod.join_request.me` | GET | `/pods/{pod_id}/join-requests/me` | Get My Pod Join Request |
| `pod.list` | GET | `/organizations/{organization_id}/pods` | List Pods by Organization |
| `pod.list_joinable` | GET | `/organizations/{organization_id}/pods/joinable` | List Pods the Caller Can Join |
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

## `pod.list_joinable`

The organization's pods the caller is not in and may join without asking: the
list to browse before `pod.join`.

- **Authorization:** membership of the organization; 403 `POD_ACCESS_DENIED`
  otherwise. An organization owner gets an empty list: they open every pod
  already, and `pod.join` refuses them.
- **Effect:** none; read-only.
- **Shape:** `items[]` of `id`, `name`, `description`, `icon_url`,
  `join_policy` (`ORG_MEMBERS` or `PUBLIC`) and `member_count`, in pod id
  order. `limit` (1-1000, default 100) and `page_token` page through them;
  `next_page_token` is set while more remain. Invite-only pods, deleted pods
  and pods the caller is already in are never listed.
- **Errors:** 403 not a member; 400 an unreadable `page_token`; 422 `limit` out
  of range.

## `pod.create` and `pod.update`: `config.auto_join_new_members`

Makes everyone who joins the organization from then on a member of the pod, as
a pod user -- the membership `pod.join` makes. People already in the
organization are not added.

- **Authorization:** on update, changing it needs `pod.member.manage`, as
  changing `join_policy` does; `pod.update` alone is refused with 403.
- **Errors:** 422 `POD_AUTO_JOIN_NEEDS_OPEN_POLICY` when the pod would be
  invite-only with it on. Judged on the merged config, so closing a pod that
  takes in new members means turning this off in the same request.
- **Emits:** nothing when it is set. When someone joins the organization,
  identity's `OrganizationMemberAdded` fact queues
  `pod.auto_join.org_member_added` under the organization (FAST, one per
  organization and person), which adds them to every such pod still open to
  members. Organization owners are not added.
