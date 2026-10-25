# identity contract

What every `identity` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `auth.verify_token` | GET | `/auth/verify-token` | Verify access token |
| `org.apps.list` | GET | `/organizations/{organization_id}/apps` | List Apps Across The Organization |
| `org.create` | POST | `/organizations` | Create Organization |
| `org.get` | GET | `/organizations/{organization_id}` | Get Organization |
| `org.home` | GET | `/organizations/{organization_id}/home` | Get Organization Home |
| `org.home.seen.get` | GET | `/organizations/{organization_id}/home/seen` | Get When You Last Looked At Home |
| `org.home.seen.set` | PUT | `/organizations/{organization_id}/home/seen` | Mark Home Seen |
| `org.invitation.accept` | POST | `/organizations/invitations/{invitation_id}/accept` | Accept Invitation |
| `org.invitation.get` | GET | `/organizations/invitations/{invitation_id}` | Get Organization Invitation |
| `org.invitation.invite` | POST | `/organizations/{organization_id}/invitations` | Invite Member |
| `org.invitation.list` | GET | `/organizations/{organization_id}/invitations` | List Organization Invitations |
| `org.invitation.list_mine` | GET | `/organizations/invitations` | List My Invitations |
| `org.invitation.revoke` | DELETE | `/organizations/invitations/{invitation_id}` | Revoke Invitation |
| `org.join_auto_join` | POST | `/organizations/{organization_id}/join` | Join Auto-Join Organization |
| `org.list` | GET | `/organizations` | List My Organizations |
| `org.member.list` | GET | `/organizations/{organization_id}/members` | List Organization Members |
| `org.member.remove` | DELETE | `/organizations/{organization_id}/members/{member_id}` | Remove Member |
| `org.member.update_role` | PATCH | `/organizations/{organization_id}/members/{member_id}/role` | Update Member Role |
| `org.navigation` | GET | `/organizations/navigation` | List Organizations And Their Pods |
| `org.needs_you.list` | GET | `/organizations/{organization_id}/needs-you` | List What Waits On You Across The Organization |
| `org.permissions.me` | GET | `/organizations/{organization_id}/permissions/me` | My Permissions In An Organization |
| `org.recents.get` | GET | `/organizations/{organization_id}/recents` | Get What Moved Lately In Each Pod |
| `org.slug_availability` | GET | `/organizations/slug-availability` | Check Organization Slug Availability |
| `org.suggested` | GET | `/organizations/suggested` | Get Suggested Organizations |
| `org.update` | PATCH | `/organizations/{organization_id}` | Update Organization |
| `user.current.get` | GET | `/users/me` | Get Current User |
| `user.email_delivery.get` | GET | `/users/me/email-delivery` | Get Email Delivery Status |
| `user.email_delivery.test` | POST | `/users/me/email-delivery/test` | Send A Test Email |
| `user.profile.get` | GET | `/users/me/profile` | Get User Profile |
| `user.profile.upsert` | POST | `/users/me/profile` | Create or Update Profile |
| `users.ensure_first_workspace` | POST | `/users/me/first-workspace` | Ensure The Current User Has A Workspace |

<!-- /generated:operations -->

## `org.needs_you.list`

Everything waiting on the caller across every pod they may open in the
organization, as one list, oldest first.

- **Authorization:** membership of the organization; 403 otherwise, whether or
  not it exists. Inside it, each source keeps the rule its per-pod read has:
  pauses are the caller's own conversations (`agent.conversation.pending_interaction.list`),
  workflow waits are theirs by being assigned to one of their pod memberships
  (`workflow.run.waiting_assigned_to_me`), and contacts are listed only in pods
  where the caller holds `channel.read` and `contact.read` (the pod's contact
  list).
- **Effect:** none; read-only and safe to poll.
- **Shape:** `items[]` of `key`, `kind` (`QUESTION`, `APPROVAL`, `PERMISSION`,
  `FORM`, `DECISION`, `CONTACT`), `pod_id`, `pod_name`, `title`, `detail`,
  `since`, and exactly one of `conversation_id` (with `tool_call_id`, the
  approval id), `workflow_run_id` or `channel_id`. One item per conversation,
  aged by its longest-waiting pause. `limit` (1-200, default 100) caps the list;
  `truncated` says more was waiting.
- **Errors:** 403 not a member; 422 `limit` out of range.

## `org.recents.get`

What moved lately in each pod the caller may open, quiet pods included.

- **Authorization:** membership of the organization; 403 otherwise. Chats and
  day counts are the caller's own conversations; automations are every run of
  a workflow, or fire of a non-internal schedule, the caller may read in that
  pod, whoever started it.
- **Effect:** none.
- **Shape:** `pods[]` of `pod_id`, `pod_name`, `chats[]` (newest first, at most
  `chats`; conversations of no channel whose origin is `chat` or `channel`, or
  any waiting on the caller), `automations[]` (one per workflow or schedule
  that ran in the window, most recent first: `kind`, `id`, `name`,
  `run_count`, `latest_run_id`, `latest_status`, `latest_at`, and
  `workflow_run_id` or `conversation_id` to open) and `activity[]` (one per day
  of the window, oldest first, zeros included: `day`, `by_agent`, `by_people`).
  A conversation counts once, on the day it was last active; archived ones and
  a channel's count too.
- **Errors:** 403 not a member; 422 `days` outside 1-31, `chats` outside 1-25,
  or a `timezone` that is not an IANA name this host knows.

## `org.apps.list`

Every app the caller may open across the organization's pods, most recently
updated first.

- **Authorization:** membership of the organization; 403 otherwise. Each app is
  filtered by its own visibility and owner against the caller's permissions in
  its pod -- another member's personal app is never listed.
- **Effect:** none.
- **Shape:** `items[]` of `id`, `pod_id`, `pod_name`, `name`, `description`,
  `url` (null where the deployment serves no app host), `status`, `visibility`,
  `updated_at`. No page: each pod contributes at most 200, a ceiling rather
  than a page size.
- **Errors:** 403 not a member.

## `org.home.seen.get`

When the caller last marked the organization's Home seen.

- **Authorization:** membership of the organization; 403 otherwise. The mark is
  the caller's own; nobody else's is readable.
- **Effect:** none.
- **Shape:** `seen_at` (null until first marked) and `previous_seen_at`, equal
  to it on a read.

## `org.home.seen.set`

Marks the organization's Home seen by the caller.

- **Authorization:** membership of the organization; 403 otherwise.
- **Effect:** sets the caller's mark to `seen_at`, or to now when it is
  omitted; a future moment is read as now. The mark only moves forward -- a
  write older than the stored mark leaves it -- so repeating a write is safe
  and an older tab cannot undo a newer one. The membership's `updated_at` is
  not touched.
- **Shape:** `seen_at` after the write, and `previous_seen_at` before it.
- **Errors:** 403 not a member; 422 a `seen_at` without a UTC offset.

