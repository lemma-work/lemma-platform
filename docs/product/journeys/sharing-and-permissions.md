# Sharing and permissions

**Journey:** A person decides exactly who — and what — can touch each thing in a
pod, and can find out afterwards what they decided.

Access in Lemma is decided in three layers, and a request has to pass all three.
**Membership** puts a person in an organization and a pod. **Roles** say what
they can generally do there. **Grants** say what they can do to one specific
table, agent, or function. On top of that sits a fourth idea that only applies
to software: an agent or a function acting for a person gets that person's
access *narrowed* by its own grants — never widened.

The promise: nothing gets access by accident. Not by being in a channel, not by
being handed a link, not by being called by an agent that happens to have more
rights than the person who asked.

---

## Capability: Decide how widely a resource is shared

### PS-ACCESS-001 — Every resource has a stated reach
**Status:** covered

- The system shall give every table, file, agent, function, workflow, schedule,
  and app one of four reaches: **personal** to its creator, **pod**-wide,
  **restricted** to named grantees, or **public** to every signed-in Lemma
  account.
- The system shall treat public as "every signed-in account", never as "anyone
  on the internet" — an unauthenticated request shall be refused whatever the
  resource's reach.
- When a person creates a resource without saying, the system shall default it
  to pod-wide.

**Contracts:** `table.create`, `agent.create`, `function.create`, `workflow.create`, `pod.resource.preview`

### PS-ACCESS-002 — Narrowing a resource's reach takes access away immediately
**Status:** covered

- When a person narrows a resource from pod-wide to restricted, the system shall
  refuse the next request from anyone not named on it.
- When a person widens a resource's reach, the system shall allow the newly
  entitled on their next request.
- The system shall apply a reach change without waiting for anything to expire.

**Contracts:** `pod.resource_access.get`, `pod.resource_access.grant.replace`

### PS-ACCESS-003 — Changing reach does not silently disarm the pod's software
**Status:** covered

- When a person changes a resource's reach, the system shall keep the grants
  held by agents and functions on that resource.
- The system shall treat a grant to a person and a grant to a workload as
  different things: sharing is about people, and a workload's grant is a
  capability the pod depends on to work.
- If narrowing a resource would stop an agent or workflow that depends on it,
  then the system shall say so before applying the change.

**Contracts:** `pod.resource_access.grant.replace`, `agent.permissions.get`, `function.permissions.get`

---

## Capability: Grant access to one specific thing

### PS-ACCESS-010 — A person grants one other person access to one resource
**Status:** covered

- When a person grants another person permissions on a resource, the system
  shall allow exactly those and refuse the rest.
- When a person removes a grant, the system shall refuse that grantee's next
  request.
- When a person reads a resource's access, the system shall list every grantee
  and exactly what each holds.
- If a person attempts to grant permissions they do not hold themselves, then
  the system shall refuse — nobody may confer more than they have.

**Contracts:** `pod.resource_access.grant.replace`, `pod.resource_access.get`, `pod.resource_access.grant.delete`

### PS-ACCESS-011 — A person grants access to a role rather than a name
**Status:** covered

- When a person grants a role permissions on a resource, the system shall apply
  it to everyone holding that role, including people who receive it later.
- When a person loses a role, the system shall stop giving them what that role
  granted, on their next request.

**Contracts:** `pod.resource_access.grant.replace`, `pod.role.permissions.replace`, `pod.member.update_roles`

### PS-ACCESS-012 — A person can see what they may do before trying
**Status:** covered

- When a person asks what they may do in a pod, the system shall answer with
  their effective permissions.
- The system shall keep that answer honest: everything reported as permitted
  shall succeed, and everything reported as denied shall be refused.
- When a person asks what permissions exist, the system shall list the full
  catalogue with what each one allows.

**Contracts:** `pod.permissions.me`, `pod.permissions.catalog`

---

## Capability: Give software exactly what it needs

### PS-ACCESS-020 — An agent or function never exceeds the person it acts for
**Status:** covered

- While an agent or function acts on a person's behalf, the system shall grant
  it the intersection of that person's access and its own grants, and never the
  union.
- If a workload holds a grant on a resource the invoking person cannot reach,
  then the system shall refuse the access.
- The system shall apply this on every request the workload makes, not only on
  the first.
- Granting a workload access to a resource shall not, by itself, withdraw that
  resource from the people in the pod. A grant to an agent says what the agent
  may do; it says nothing about who may see the thing. Otherwise configuring a
  shared account for an agent would be the act that stopped the pod's members
  running it.

**Contracts:** `agent.permissions.get`, `function.permissions.get`, `record.list`, `query.execute`

### PS-ACCESS-021 — No software does anything destructive by default
**Status:** covered

- The system shall refuse every destructive action attempted by an agent or a
  function unless that exact action was granted in advance or approved by a
  person at the time.
- The system shall apply that rule even when the workload acts for the person
  who created the resource.
- If the record of session approvals is unavailable, then the system shall treat
  every action as unapproved and shall ask again, rather than allowing it.

**Contracts:** `agent.conversation.approval.resolve`, `table.delete`, `record.bulk_delete`

### PS-ACCESS-022 — Approving for a session means that session only
**Status:** covered

- When a person approves an action for the rest of a session, the system shall
  stop asking for that same action within that conversation.
- The system shall not carry a session approval into another conversation,
  another agent, or another person's session.
- The system shall expire a session approval, so a long-lived conversation does
  not become a standing grant by accident.

**Contracts:** `agent.conversation.approval.resolve`, `agent.conversation.approval.list`

### PS-ACCESS-023 — Revoking a person's access revokes their software's too
**Status:** covered

- When a person is removed from a pod, the system shall stop honouring
  delegations made in their name, on the next request.
- The system shall not let work already dispatched on a removed person's behalf
  continue to act with their access.

**Contracts:** `pod.member.remove`, `agent.conversation.get`, `function.run`

---

## Capability: Use a pod from another AI tool

### PS-ACCESS-040 — A person connects an outside AI tool to one pod, and it acts as them
**Status:** manual

- When a person opens a pod's AI tools settings, the system shall give them one
  link for that pod that an MCP client such as Claude or ChatGPT can add
  without installing anything.
- When a tool first uses the link, the system shall ask the person to sign in
  before anything is granted.
- When the person is asked to allow a tool, the system shall show which tool is
  asking.
- When the person is asked to allow a tool, the system shall show which pod the
  tool wants.
- When the person is asked to allow a tool, the system shall show where they
  will be sent back to.
- While a tool is connected, the system shall let it read and change only what
  the person themselves may read and change in that pod.
- If a connected tool asks for a pod other than the one it was connected to,
  then the system shall refuse it.
- When a tool asks to change things, the system shall let the person allow it
  to read only instead.
- Where the person allowed reading only, the system shall not let the tool
  change anything.
- Where the person allowed reading only, the system shall not let the tool
  make a link to a file that anyone can open.
- If a tool asks to send the person anywhere that would run code in their
  browser, then the system shall refuse the request.
- If a tool asks to send the person anywhere that would run code in their
  browser, then the system shall not send them there.
- If a tool presents access that was issued for a different pod, then the
  system shall refuse it.
- If a tool presents access that was issued for a different pod, then the
  system shall reveal nothing about that pod.
- If the person declines, then the system shall grant nothing.
- If the person declines, then the system shall tell the tool it was refused.

> **Manual:** the promise is about someone else's product reaching Lemma from
> the internet, so proving it needs Claude and ChatGPT themselves. Verified by
> hand against both through a public tunnel; the whole exchange — discovery,
> registration, consent, token, tool calls, the pod boundary and read-only
> access — is covered by the module e2e suite (`mcp_access/tests/e2e`).

**Contracts:** `mcp_access.endpoint.get`

### PS-ACCESS-041 — A person sees what they have connected, and disconnecting takes effect at once
**Status:** manual

- When a person opens a pod's AI tools settings, the system shall list the
  tools they have connected to that pod.
- When the system lists a connected tool, it shall show what the tool may do.
- When the system lists a connected tool, it shall show when the tool was last
  used.
- When a pod admin opens the pod's AI tools settings, the system shall list
  every member's connections to that pod.
- When a pod admin disconnects a member's tool, the system shall end that
  connection.
- When a person connects the same tool from a second device, the system shall
  record a second connection.
- When a person disconnects one of two connections, the system shall leave the
  other working.
- When a person disconnects a tool, the system shall refuse that tool's very
  next request.
- When a disconnected tool asks to connect again, the system shall ask the
  person again.
- If a tool's saved access is used by two parties, then the system shall end
  the connection for both.
- If the person's account is deactivated, then the system shall refuse every
  tool they connected.
- When a pod is deleted, the system shall refuse every tool connected to it.
- When a tool disconnects itself, the system shall end that connection.

> **Manual:** as PS-ACCESS-040. The module e2e suite covers disconnecting,
> refresh-token reuse and the list.

**Contracts:** `mcp_access.grants.list`, `mcp_access.grants.revoke`

### PS-ACCESS-042 — A connected tool shows a pod's records as a table the person can work in
**Status:** manual

- Where the tool can show interactive views, when it reads records or runs a
  query, the system shall show the result as a table.
- When the table is one page of more, the system shall say which rows are shown
  out of how many.
- When the person moves to another page or sorts the table, the system shall
  read the rows with the same access the tool was given.
- When the person sorts a table that has more rows than are shown, the system
  shall sort the whole table, not only the page shown.
- When the person moves to another page or sorts the table, the system shall
  tell the tool which rows the person is now looking at.
- If a query's result was cut short, then the system shall say so.
- If reading the records fails, then the system shall show the failure.
- Where the tool cannot show interactive views, the system shall give it the
  same result as text.

> **Manual:** the view runs inside Claude's or ChatGPT's own page, so proving it
> needs a host. Verified against the MCP Apps reference host over the public
> mount with a real token; the module suites cover the tool listing, the served
> view and the payloads it draws (`mcp_access/tests/e2e`,
> `agent/tests/unit/test_pod_mcp_views.py`).

## Capability: Understand and audit access

### PS-ACCESS-030 — A person can see who can reach a resource
**Status:** covered

- When a person asks who can reach a resource, the system shall list the people,
  the roles, and the workloads that can, and what each may do.
- The system shall include access that comes from the resource's reach, not only
  from explicit grants, so the answer is complete.

**Contracts:** `pod.resource_access.get`, `pod.resource.preview`

### PS-ACCESS-031 — Refusals are informative without leaking
**Status:** covered

- If a person is refused access to a resource, then the system shall say they
  are not permitted.
- The system shall not reveal, through the refusal, the existence or contents of
  a resource in a pod or organization the person does not belong to.
- The system shall answer consistently whether a resource is absent or merely
  forbidden, where telling the difference would reveal something.

**Contracts:** `pod.get`, `table.get`, `agent.get`, `agent.conversation.get`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Organization roles and who may invite | [Getting started](getting-started.md) |
| Pod roles and custom roles | [Building a pod](building-a-pod.md) |
| Signed links to a single file | [Working with data](working-with-data.md) |
| Trust boundaries and attacker model | [Threat model](../../security/threat-model.md) |
