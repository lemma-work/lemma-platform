# Building a pod

**Journey:** A person turns an empty organization into a pod that holds real
work, and decides who is in it.

A pod is the unit of work in Lemma. Tables, documents, functions, workflows,
schedules, agents, surfaces, and apps all live inside one, and every one of them
inherits the pod's answer to "who can touch this". A person's first pod is the
moment the product becomes useful, so this journey is about getting there
quickly and then controlling it precisely.

This journey covers the pod itself and who belongs to it. What goes *inside* a
pod is [Working with data](working-with-data.md),
[Automating work](automating-work.md), and
[Agents and conversations](agents-and-conversations.md). Fine-grained control
over a single resource is [Sharing and permissions](sharing-and-permissions.md).

---

## Capability: Create a pod

### PS-POD-001 — A member of an organization creates a pod and administers it
**Status:** covered

- When a member of an organization creates a pod, the system shall create it and
  make that person a pod admin.
- When a pod is created, the system shall record `pod.created`.
- When a pod is created, the system shall prepare its storage so the first table
  or document does not have to wait for it.
- If a person who does not belong to the organization attempts to create a pod
  in it, then the system shall refuse.

**Contracts:** `pod.create`, `pod.created`

### PS-POD-002 — A pod's name identifies it within its organization
**Status:** covered

- The system shall keep pod names unique within an organization, so that a name
  resolves to exactly one pod.
- If a person creates a pod whose name is already used in that organization,
  then the system shall refuse and shall say the name is taken.
- The system shall trim and normalise a pod name on the way in, so that names
  differing only by surrounding whitespace collide rather than both being
  accepted.
- When a pod is deleted, the system shall free its name for reuse.

> Names are addresses here, not labels: `lemma --pod <name>`, bundle references,
> and inbound email routing all resolve a pod by name. Two pods answering to one
> name would make each of those ambiguous. This is the opposite of the rule for
> organization *display* names (`PS-ONB-014`), which are labels and are shared
> freely — the difference is whether anything resolves through them.

**Contracts:** `pod.create`, `pod.update`, `pod.list`

### PS-POD-003 — A pod carries the settings its work depends on
**Status:** planned

- When a pod admin updates part of a pod's settings, the system shall leave the
  parts they did not mention unchanged.
- Where a pod pins a default agent runtime, the system shall apply it to agents
  in that pod that do not pin one themselves.
- Where a pod pins nothing, the system shall fall back to the deployment default
  rather than failing.

**Contracts:** `pod.update`, `pod.get`

---

## Capability: Put people in a pod

### PS-POD-010 — A pod admin adds an organization member to the pod
**Status:** covered

- When a pod admin adds an organization member to the pod with a role, the
  system shall grant them that role in the pod.
- When someone joins a pod, the system shall record `pod.member_joined`.
- If a pod admin attempts to add someone who is not a member of the pod's
  organization, then the system shall refuse — organization membership is the
  outer boundary and a pod cannot widen it.
- If a pod admin attempts to add someone who is already a member of the pod,
  then the system shall refuse rather than creating a second membership.

**Contracts:** `pod.member.add`, `pod.member.list`, `pod.member_joined`

### PS-POD-011 — A person's pod role decides what they may do inside it
**Status:** covered

- The system shall offer four built-in pod roles, ordered from least to most
  able: viewer, user, editor, admin.
- While a person holds a pod role, the system shall allow exactly the actions
  that role permits and refuse the rest, on every request and not only in the
  interface that offered it.
- When a pod admin changes a person's roles, the system shall apply the change
  promptly and shall never require a cached snapshot to expire first.

> A role change is observable within a request or two, not immediately within
> the same one — the cached snapshot is dropped just after the change commits,
> so a request already in flight behind it can be served from the old one. The
> measured window is a single request. `DEV-POD-003` records it; the promise
> above is deliberately worded to the guarantee that matters, which is that
> nobody waits five minutes for a demotion.

**Contracts:** `pod.member.update_roles`, `pod.permissions.me`, `pod.permissions.catalog`

### PS-POD-012 — A person can find out what they may do, before trying
**Status:** covered

- When a person asks what they may do in a pod, the system shall answer with
  their effective permissions, so an interface can hide what would be refused.
- The system shall keep that answer consistent with what the API actually
  enforces, so that anything reported as permitted succeeds and anything
  reported as denied is refused.

**Contracts:** `pod.permissions.me`, `pod.permissions.catalog`

### PS-POD-013 — A pod admin defines roles the built-in ones do not cover
**Status:** covered

- When a pod admin creates a custom role with a set of permissions, the system
  shall allow it to be assigned like a built-in one.
- If a person attempts to grant a role carrying permissions they do not
  themselves hold, then the system shall refuse — nobody may confer more than
  they have.
- When a pod admin changes a custom role's permissions, the system shall apply
  the change to everyone already holding it.

**Contracts:** `pod.roles.create`, `pod.roles.update`, `pod.role.permissions.replace`, `pod.role.permissions.get`

---

## Capability: Let people ask to join

### PS-POD-020 — A pod decides who may walk in
**Status:** covered

- Where a pod is invite-only, the system shall refuse every self-join and shall
  point the person at requesting access instead.
- Where a pod is open to the organization, the system shall let any member of
  its organization join directly, as a pod user.
- Where a pod is open to everyone, the system shall let any signed-in person
  join directly, adding them to the organization as an ordinary member on the
  way in.
- The system shall default a new pod to invite-only.

**Contracts:** `pod.join`, `pod.update`, `pod.member_joined`

### PS-POD-021 — A person asks for access and an admin decides
**Status:** covered

- When a person requests to join a pod, the system shall record the request as
  pending and notify the people who can decide it.
- When a pod admin approves a request, the system shall add the person to the
  pod with the role the approver chose, adding them to the organization first if
  they are not yet a member.
- If a person requests to join a pod they already belong to, then the system
  shall refuse rather than creating a request that cannot do anything.
- If someone attempts to decide a request that has already been decided, then
  the system shall refuse and shall say what it was decided as.

**Contracts:** `pod.join_request.create`, `pod.join_request.approve`, `pod.join_request.list`, `pod.join_request.me`

### PS-POD-022 — Approving a request cannot be used to gain authority
**Status:** planned

- If an approver attempts to grant an organization role above their own, then
  the system shall refuse — approving a join request shall not become a side
  channel for minting owners or editors.
- If a pod admin who is only an ordinary organization member approves a request,
  then the system shall cap the organization role they may confer at ordinary
  member.
- If an approver attempts to grant pod roles carrying permissions they do not
  hold, then the system shall refuse.

**Contracts:** `pod.join_request.approve`

---

## Capability: See the pods you have

### PS-POD-030 — A person sees exactly the pods they may open
**Status:** gap

- When a person lists the pods in an organization, the system shall return every
  pod they are entitled to open, and no others.
- The system shall make listing and opening agree: any pod the system will open
  for a person shall appear in their list, and any pod absent from their list
  shall be refused when opened directly.
- When an organization owner lists pods, the system shall return every pod in
  the organization, because ownership carries responsibility for all of it.
- If a person attempts to open a pod in an organization they do not belong to,
  then the system shall refuse.

> **Gap:** listing and opening disagree for organization editors — an editor can
> open any pod in the organization by identity, but sees only their own pods in
> any list. See `DEV-POD-001`; one of the two is wrong and they need deciding
> together.

**Contracts:** `pod.list`, `pod.get`

### PS-POD-031 — A person sees their pods across every organization at once
**Status:** planned

- When a person asks for their navigation, the system shall return the
  organizations they belong to and the pods they may open in each, in one
  answer.
- The system shall keep navigation consistent with the per-organization pod
  list.

**Contracts:** `org.navigation`, `org.home`, `pod.list`

---

## Capability: Change and remove membership

### PS-POD-040 — Removing someone from a pod takes their access away immediately
**Status:** covered

- When a pod admin removes a member, the system shall revoke their access to the
  pod and everything in it on their next request.
- When a member is removed, the system shall also drop the resource grants they
  held through that membership, so that re-adding them does not silently restore
  old access.
- If someone who is neither an organization owner nor a pod admin attempts to
  remove another member, then the system shall refuse.

**Contracts:** `pod.member.remove`, `pod.permissions.me`

### PS-POD-041 — A pod always has at least one admin
**Status:** gap

- The system shall ensure every pod has at least one admin at all times.
- If removing a member or changing their roles would leave the pod with no
  admin, then the system shall refuse and shall say another admin must be
  appointed first.

> **Gap:** neither path is guarded. Unlike the organization equivalent this is
> recoverable — an organization owner bypasses pod roles and can appoint a new
> admin — so it is a smaller problem than it looks. See `DEV-POD-002`.

**Contracts:** `pod.member.remove`, `pod.member.update_roles`

---

## Capability: Delete a pod

### PS-POD-050 — Deleting a pod stops the work it was doing
**Status:** covered

- When an organization owner or a pod admin deletes a pod, the system shall stop
  showing it and shall stop its schedules, surfaces, and other standing work.
- When a pod is deleted, the system shall record `pod.deleted`.
- When a pod is deleted, the system shall free its name for reuse.
- If a pod member who is not an admin attempts to delete the pod, then the
  system shall refuse.
- The system shall keep deleting safe to repeat, so that a retried deletion
  reports success rather than failing on the second attempt.

**Contracts:** `pod.delete`, `pod.deleted`

### PS-POD-051 — Deletion does not take unrelated things with it
**Status:** covered

- When a pod is deleted, the system shall leave the organization, its other
  pods, and their contents untouched.
- The system shall not remove the workspaces and data other pods depend on while
  cleaning up after a deleted one.

**Contracts:** `pod.delete`, `pod.deleted`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Organizations, invitations, org roles | [Getting started](getting-started.md) |
| Granting one person access to one table or agent | [Sharing and permissions](sharing-and-permissions.md) |
| Making a pod from someone else's bundle | [Packaging and reuse](packaging-and-reuse.md) |
| What the pod's default agent may do on your behalf | [Agents and conversations](agents-and-conversations.md) |
