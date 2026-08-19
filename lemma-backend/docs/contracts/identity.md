# identity contract

What every `identity` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `auth.verify_token` | GET | `/auth/verify-token` | Verify access token |
| `org.create` | POST | `/organizations` | Create Organization |
| `org.get` | GET | `/organizations/{org_id}` | Get Organization |
| `org.home` | GET | `/organizations/{org_id}/home` | Get Organization Home |
| `org.invitation.accept` | POST | `/organizations/invitations/{invitation_id}/accept` | Accept Invitation |
| `org.invitation.get` | GET | `/organizations/invitations/{invitation_id}` | Get Organization Invitation |
| `org.invitation.invite` | POST | `/organizations/{org_id}/invitations` | Invite Member |
| `org.invitation.list` | GET | `/organizations/{org_id}/invitations` | List Organization Invitations |
| `org.invitation.list_mine` | GET | `/organizations/invitations` | List My Invitations |
| `org.invitation.revoke` | DELETE | `/organizations/invitations/{invitation_id}` | Revoke Invitation |
| `org.join_auto_join` | POST | `/organizations/{org_id}/join` | Join Auto-Join Organization |
| `org.list` | GET | `/organizations` | List My Organizations |
| `org.member.list` | GET | `/organizations/{org_id}/members` | List Organization Members |
| `org.member.remove` | DELETE | `/organizations/{org_id}/members/{member_id}` | Remove Member |
| `org.member.update_role` | PATCH | `/organizations/{org_id}/members/{member_id}/role` | Update Member Role |
| `org.navigation` | GET | `/organizations/navigation` | List Organizations And Their Pods |
| `org.slug_availability` | GET | `/organizations/slug-availability` | Check Organization Slug Availability |
| `org.suggested` | GET | `/organizations/suggested` | Get Suggested Organizations |
| `org.update` | PATCH | `/organizations/{org_id}` | Update Organization |
| `user.current.get` | GET | `/users/me` | Get Current User |
| `user.profile.get` | GET | `/users/me/profile` | Get User Profile |
| `user.profile.upsert` | POST | `/users/me/profile` | Create or Update Profile |

<!-- /generated:operations -->
