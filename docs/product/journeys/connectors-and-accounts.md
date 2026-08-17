# Connectors and accounts

**Journey:** A person connects the systems their work actually lives in, so the
pod can read and write there instead of being told about it second-hand.

There are two separate things here, and keeping them apart is the whole design.
An **auth config** is the organization deciding "we use Jira, and here is how we
connect to it" — set up once, by an admin. An **account** is one person's own
credential for that provider — their Jira login, connected by them, owned by
them. One installation, many personal accounts.

The promise: a person's credential is theirs. It is encrypted, it is never shown
back to anyone including them, and nothing in the pod uses it except on their
behalf.

---

## Capability: Find what can be connected

### PS-CONN-001 — A person browses what the platform can connect to
**Status:** covered

- When a person asks what connectors exist, the system shall list them without
  requiring an organization to have installed anything yet.
- When a person opens a connector, the system shall describe what it can do
  before they commit to installing it.
- The system shall let a person see what an organization has installed and which
  of those they personally have connected.

**Contracts:** `connector.list`, `connector.get`, `connector.status.get`, `connector.skill.get`

---

## Capability: Install a connector for the organization

### PS-CONN-010 — An admin installs a connector once for everyone
**Status:** covered

- When an organization admin installs a connector, the system shall make it
  available for members of that organization to connect their own accounts to.
- The system shall keep an installation scoped to its organization — installing
  in one organization shall have no effect in another.
- If a person who is not entitled to administer the organization attempts to
  install or change a connector, then the system shall refuse.

**Contracts:** `connector.auth_config.create`, `connector.auth_config.list`, `connector.auth_config.get`, `connector.auth_config.update`

### PS-CONN-011 — Provider secrets given at install stay secret
**Status:** covered

- The system shall encrypt any provider secret supplied at installation.
- The system shall never return a provider secret to any client, in any
  response, at any privilege level — including to the person who supplied it.
- When a person reads an installation, the system shall show enough to identify
  and manage it without revealing the secret.

**Contracts:** `connector.auth_config.get`, `connector.auth_config.list`

### PS-CONN-012 — Removing an installation removes what depended on it
**Status:** covered

- When an admin deletes an installation, the system shall stop every account
  connected through it from being usable.
- The system shall say what will stop working before removing it, rather than
  after.

**Contracts:** `connector.auth_config.delete`, `connector.account.list`

---

## Capability: Connect your own account

### PS-CONN-020 — A person connects their account and it belongs to them
**Status:** covered

- When a person connects their account for an installed connector, the system
  shall bind it to them and shall record `connector.connected`.
- The system shall keep an account owned by the person who connected it, even
  though the installation is shared by the organization.
- The system shall not let one person use another person's connected account.
- When a person deletes their account, the system shall stop anything in the
  organization from acting as them at that provider.

**Contracts:** `connector.account.create`, `connector.account.list`, `connector.account.get`, `connector.account.delete`, `connector.connected`

### PS-CONN-021 — Connecting through a provider's consent screen works end to end
**Status:** covered

- When a person starts connecting, the system shall send them to the provider's
  own consent screen and shall never ask them for their provider password.
- When the provider returns them, the system shall complete the connection and
  shall bring them back where they started.
- If the returned state does not match a connection this system started, then
  the system shall refuse it.
- The system shall expire an unfinished connection attempt rather than leaving
  it usable indefinitely.

**Contracts:** `connector.connect_request.create`, `connector.oauth.callback`

### PS-CONN-022 — An account that stops working says so
**Status:** covered

- If a provider rejects a credential as no longer valid, then the system shall
  mark that account as needing reconnection.
- The system shall show a person which of their accounts need reconnecting,
  before they discover it through a failed operation.
- Where a credential can be refreshed without a person, the system shall refresh
  it rather than asking them.

**Contracts:** `connector.account.get`, `connector.account.list`, `connector.status.get`

---

## Capability: Do something at the provider

### PS-CONN-030 — A person finds the operation they need
**Status:** covered

- When a person searches the operations of an installed connector, the system
  shall return matching ones with what they take and what they return.
- The system shall let a person read one operation in full before running it.
- The system shall let an organization refresh the catalogue of an installed
  connector when the provider adds capabilities.

**Contracts:** `connector.operation.search`, `connector.operation.discover`, `connector.operation.detail`, `connector.operation.details.batch`, `connector.auth_config.refresh_operations`

### PS-CONN-031 — An operation runs as the person who owns the account
**Status:** covered

- When a person runs an operation, the system shall run it using their own
  connected account.
- When an operation completes, the system shall record
  `connector.operation_executed` with whether it succeeded.
- While an agent or function runs an operation on a person's behalf, the system
  shall use that person's account and shall not fall back to anyone else's.
- If a person runs an operation for a connector they have not connected, then
  the system shall refuse and shall tell them to connect first.

**Contracts:** `connector.operation.execute`, `connector.operation_executed`

### PS-CONN-032 — A slow or failing provider does not damage the pod
**Status:** planned

- The system shall bound how long it waits for a provider, and shall report a
  timeout as a failed operation.
- If a provider returns an error, then the system shall report what the provider
  said rather than a generic failure.
- The system shall keep a provider's slowness from affecting unrelated work in
  the platform.

**Contracts:** `connector.operation.execute`

### PS-CONN-033 — An agent can only use the connectors it was granted
**Status:** covered

- When a person grants an agent or a function access to a connector, the system
  shall allow exactly that connector and refuse the rest.
- If an agent attempts an operation on a connector it was not granted, then the
  system shall refuse.
- If an operation would change something at the provider rather than read it,
  then the system shall require a standing grant or the person's approval at the
  time.

**Contracts:** `agent.permissions.replace`, `function.permissions.replace`, `connector.operation.execute`

---

## Capability: React to the provider

### PS-CONN-040 — A person sees what a provider can notify them about
**Status:** covered

- When a person asks what triggers a connector offers, the system shall list
  them with what each one carries.
- The system shall let a person read one trigger in full before wiring anything
  to it.

**Contracts:** `connector.trigger.list`, `connector.trigger.get`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Firing pod work from a provider's webhook | [Scheduling and triggers](scheduling-and-triggers.md) |
| An agent choosing to call a connector | [Agents and conversations](agents-and-conversations.md) |
| Bundling connector configuration for reuse | [Packaging and reuse](packaging-and-reuse.md) |
| How secrets are encrypted at rest | [Threat model](../../security/threat-model.md) |
