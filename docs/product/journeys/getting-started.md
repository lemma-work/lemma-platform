# Getting started

**Journey:** A person arrives with nothing, and ends up somewhere they can work
with the people they work with.

Everything in Lemma happens inside an organization, and almost everything
happens inside a pod inside an organization. This journey covers getting to that
first organization — by making one, by being invited to one, or by finding one
that already exists. It stops at the point where a pod is worth making, which is
[Building a pod](building-a-pod.md).

Nothing here is automatic. Signing up creates a person and nothing else: no
organization, no pod, no content. That is deliberate — a person's first
organization is a decision about who they work with, and guessing it wrong is
worse than asking.

---

## Capability: Sign up and sign in

### PS-ONB-001 — A new person signs up and becomes a known user
**Status:** covered

- When a person signs up with an email address and a password, the system shall
  create a user for that email and sign them in.
- When a person signs up, the system shall record `auth.signed_up` with the
  method they used.
- The system shall treat email addresses case-insensitively, so that a person
  who signs up as `Ada@example.com` signs in as `ada@example.com`.
- If a person signs up with an email that already has a user, then the system
  shall refuse and shall not create a second user for that email.
- If a person signs up with an email that already has a user through a different
  sign-in method, then the system shall say which method that email already
  uses rather than failing generically.

**Contracts:** `auth.signed_up`

### PS-ONB-002 — A person who has joined nothing sees an empty start, not an error
**Status:** covered

- When a person who belongs to no organization asks for their organizations,
  the system shall return an empty list.
- When a person who belongs to no organization asks for their navigation, the
  system shall return an empty navigation rather than an error.
- The system shall let a person read their own profile before they belong to any
  organization.

**Contracts:** `org.list`, `org.navigation`, `user.current.get`, `user.profile.get`

### PS-ONB-003 — A signed-in person is identified consistently everywhere
**Status:** covered

- While a person holds a valid session, the system shall resolve that session to
  the same user across every API, the CLI, and both SDKs.
- If a token is expired, malformed, or signed by an unknown key, then the system
  shall refuse the request and shall not fall back to an anonymous identity.

**Contracts:** `auth.verify_token`, `user.current.get`

### PS-ONB-004 — A person sets a display name and preferences that follow them
**Status:** covered

- When a person updates their profile, the system shall apply it to every
  organization they belong to, because a person has one profile and not one per
  organization.
- Where a person has set no display name, the system shall fall back to
  something stable and human rather than showing an identifier.

**Contracts:** `user.profile.upsert`, `user.profile.get`

---

## Capability: Create an organization

### PS-ONB-010 — The person who creates an organization owns it
**Status:** covered

- When a person creates an organization, the system shall make them a member of
  it with the owner role.
- When an organization is created, the system shall record
  `organization.created`.
- The system shall let a person belong to more than one organization.

**Contracts:** `org.create`, `organization.created`

### PS-ONB-011 — An organization has a handle that survives being renamed
**Status:** covered

- When an organization is created without a handle, the system shall derive one
  from its name.
- When an organization is renamed, the system shall keep its existing handle, so
  that links and references to it continue to resolve.
- If a person asks for a handle that is already taken, then the system shall
  refuse and shall say the handle is taken.

**Contracts:** `org.create`, `org.update`, `org.slug_availability`

### PS-ONB-014 — Two organizations may share a display name
**Status:** covered

- The system shall allow two unrelated organizations to carry the same display
  name, because names are how people recognise their own organization and not
  how the system tells organizations apart.
- If a person asks whether a name is in use, then the system shall not reveal
  whether some other organization on the deployment is using it.

**Contracts:** `org.create`, `org.update`, `org.slug_availability`

### PS-ONB-013 — Only an owner changes what the organization is
**Status:** covered

- When an owner renames the organization or changes how people may join it, the
  system shall apply the change.
- If a member who is not an owner attempts either, then the system shall refuse.

**Contracts:** `org.update`, `org.get`

---

## Capability: Bring a team in

### PS-ONB-020 — An invited person joins with the role they were offered
**Status:** covered

- When an owner or editor invites an email address with a role, the system shall
  create a pending invitation for that email and notify it.
- When the invited person accepts, the system shall make them a member with
  exactly the role the invitation offered.
- When someone joins an organization, the system shall record
  `organization.member_joined`.
- If a person attempts to accept an invitation addressed to a different email,
  then the system shall refuse.

**Contracts:** `org.invitation.invite`, `org.invitation.accept`, `organization.member_joined`

### PS-ONB-021 — An invitation can carry a pod, and accepting it grants both
**Status:** covered

- Where an invitation names a pod, accepting it shall make the person both a
  member of the organization and a member of that pod, with the pod role the
  invitation offered.
- If the pod named by an invitation cannot be granted — because it was deleted
  after the invitation was sent, for example — then the system shall refuse the
  acceptance and shall say which pod it could not grant, leaving the invitation
  usable once the problem is fixed.

**Contracts:** `org.invitation.invite`, `org.invitation.accept`, `pod.member.add`

### PS-ONB-022 — An invitation stops working when it should
**Status:** covered

- While an invitation is pending and unexpired, the system shall allow the
  addressed person to accept it.
- When an invitation passes its expiry, the system shall treat it as expired and
  shall refuse to accept it.
- When an owner or editor revokes a pending invitation, the system shall refuse
  any later attempt to accept it.
- If a person attempts to accept an invitation they have already accepted, then
  the system shall refuse rather than granting membership twice.

**Contracts:** `org.invitation.accept`, `org.invitation.revoke`, `org.invitation.get`

### PS-ONB-023 — Inviting someone already inside is refused clearly
**Status:** covered

- If an owner or editor invites an email that already belongs to a member of the
  organization, then the system shall refuse and shall say they are already a
  member.
- If an owner or editor invites an email that already has a pending invitation
  to the same organization, then the system shall refuse rather than creating a
  second one.

**Contracts:** `org.invitation.invite`, `org.invitation.list`

### PS-ONB-024 — A person can see the invitations waiting for them
**Status:** covered

- When a person asks for their invitations, the system shall list every pending
  invitation addressed to their email across all organizations.
- The system shall show enough on each invitation — the organization, the role,
  and the pod when it names one — for a person to decide without accepting it
  first.

**Contracts:** `org.invitation.list_mine`, `org.invitation.get`

---

## Capability: Join an organization that already exists

### PS-ONB-030 — A person is offered the organizations they could join
**Status:** covered

- When a person with a work email asks for suggestions, the system shall list
  organizations that allow self-joining and match their email domain.
- The system shall not suggest organizations the person already belongs to.
- Where a person's email is from a consumer email provider, the system shall
  suggest nothing rather than matching on the provider's domain.

**Contracts:** `org.suggested`

### PS-ONB-031 — A person joins an organization that is open to them
**Status:** covered

- When a person joins an organization that is open to everyone, the system shall
  make them a member with the least-privileged role.
- When a person whose email domain matches joins a domain-restricted
  organization, the system shall make them a member with the least-privileged
  role.
- If a person attempts to join an invite-only organization, then the system
  shall refuse.
- If a person attempts to join a domain-restricted organization from a
  non-matching email domain, then the system shall refuse.
- When a person who is already a member attempts to join again, the system shall
  leave their existing membership and role untouched.

**Contracts:** `org.join_auto_join`, `organization.member_joined`

---

## Capability: Change and remove membership

### PS-ONB-040 — An owner changes what a member may do
**Status:** covered

- When an owner changes a member's role, the system shall apply it immediately
  to every later request that member makes.
- If a member who is not an owner attempts to change any role, then the system
  shall refuse.
- If a person who is not an owner attempts to grant the owner or editor role
  through any path, then the system shall refuse — including paths that grant
  roles as a side effect, such as approving a request to join.

**Contracts:** `org.member.update_role`, `org.member.list`

### PS-ONB-041 — An organization always has at least one owner
**Status:** covered

- The system shall ensure every organization has at least one owner at all
  times.
- If removing a member would leave the organization with no owner, then the
  system shall refuse and shall say another owner must be appointed first.
- If changing a member's role would leave the organization with no owner, then
  the system shall refuse on the same grounds.
- The system shall apply both rules to a person acting on their own membership,
  because leaving is the most common way to reach the state.

**Contracts:** `org.member.remove`, `org.member.update_role`

### PS-ONB-042 — Removal respects the role hierarchy
**Status:** covered

- When an owner removes any member, the system shall remove them.
- When an editor removes a member who is not an owner, the system shall remove
  them.
- If an editor attempts to remove an owner, then the system shall refuse.
- If a member who is neither owner nor editor attempts to remove anyone other
  than themselves, then the system shall refuse.

**Contracts:** `org.member.remove`, `org.member.list`

### PS-ONB-043 — A person can leave on their own
**Status:** covered

- When a person removes their own membership, the system shall remove it without
  requiring a role, subject to PS-ONB-041.
- When a person leaves an organization, the system shall stop showing them its
  pods and content on their next request.

**Contracts:** `org.member.remove`, `org.navigation`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Creating the first pod, pod membership, pod roles | [Building a pod](building-a-pod.md) |
| What a member may do to a specific resource | [Sharing and permissions](sharing-and-permissions.md) |
| Email deliverability, verification, abuse protection | [Authentication hardening](../../authentication-hardening.md) |
| Usage limits that apply to an organization | [Operating a deployment](operating-a-deployment.md) |
