# Connectors and accounts

**Journey:** A person connects the systems their work actually lives in, so the
pod can read and write there instead of being told about it second-hand.

There are three separate things here, and keeping them apart is the whole
design. An **auth config** (an installation) is the organization deciding "we
use Jira, and here is how we connect to it" — set up once, by an admin. An
**account** is one person's own credential for that provider — their Jira
login, connected by them, owned by them. A **pod connector** is a pod declaring
that it uses an installation, under a name of its own, and saying whose account
its calls run as: each member's own (*per member*), or one account its owner
shared with the pod (*shared*). One installation, many personal accounts, and
every operation runs through a pod connector.

The promise: a person's credential is theirs. It is encrypted, it is never shown
back to anyone including them, and nothing in the pod uses it except on their
behalf — or, where they themselves shared it with a pod, on that pod's behalf
and nowhere else. Which account a call runs as is the pod connector's decision,
never something a caller, a workload or a model can name.

Some providers offer a second identity that is not a person's at all — a GitHub
App installed on an organization acts as itself, against the repositories that
organization granted it. Where a connector has one, the pod may act as *it*
instead of as a person, which is how work survives the person who set it up. It
does not weaken the promise above: the App is not somebody's credential, its
reach is whatever the organization granted the installation and no more, and
which of the two identities a given operation uses is fixed by the connector
rather than chosen per call. PS-CONN-031 says which is which.

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
  connected through it, and every pod connector declared on it, from being
  usable.
- The system shall say what will stop working — which pods' connectors, and
  how many accounts — before removing it, rather than after. Pods the admin
  cannot open are counted, never named or described; and when the system
  could not read everything that stops, it says so rather than that nothing
  does.

**Contracts:** `connector.installation.impact`, `connector.auth_config.delete`, `connector.account.list`, `pod.connector.list`

### PS-CONN-013 — An app the catalog has never heard of can still be connected
**Status:** covered

- When an admin supplies the address of an API described by an OpenAPI
  document, an MCP server, or a SQL database, the system shall make its
  operations available to the organization as it would any catalog connector.
- The system shall derive the operation set from what the address describes
  rather than from the catalog, and shall let an admin re-derive it when the
  provider's own surface changes.
- The system shall refuse an address it cannot safely reach — private, loopback
  and link-local addresses among them.
- The system shall refuse a configuration field it does not recognize rather
  than storing it.
- Where the connected system describes its own authorization, the system shall
  offer to sign in to it; where it does not, the system shall accept a token.
  Which of the two an installation needs shall be readable from the
  installation itself.

**Contracts:** `connector.auth_config.create`, `connector.auth_config.get`, `connector.auth_config.refresh_operations`, `connector.operation.discover`

### PS-CONN-014 — A connector the platform cannot sign in to asks for credentials instead of offering to connect
**Status:** manual

- Where the platform holds no credentials of its own for a connector, the
  system shall say so on the catalog entry rather than presenting it as ready
  to connect.
- Where such a connector can also be reached with a credential the person
  already has, the system shall offer that rather than requiring the
  organization to register an application of its own.
- Otherwise the system shall offer the installation as a configuration the
  organization completes, and shall state which fields that particular provider
  requires — which differ per provider, and are not assumed.
- If a person attempts to install such a connector using the platform's own
  credentials, then the system shall refuse before creating anything, and shall
  say what to supply instead.
- When an installation fails at the provider, the system shall carry the
  provider's own explanation back to the person rather than reporting only that
  something went wrong.

> **Manual:** the distinction is a property of the brokering provider's account,
> not of this system, so proving it needs a real toolkit that provider holds no
> credentials for. Verified by hand against the development account; the refusal
> and the catalog flag are covered by module tests.

**Contracts:** `connector.list`, `connector.get`, `connector.auth_config.create`, `connector.connect_request.create`

---

## Capability: Connect your own account

### PS-CONN-020 — A person connects their account and it belongs to them
**Status:** covered

- When a person connects their account for an installed connector, the system
  shall bind it to them and shall record `connector.connected`.
- The system shall keep an account owned by the person who connected it, even
  though the installation is shared by the organization.
- The system shall not let one person use another person's connected account,
  unless its owner has shared it with a pod — and then only through that pod's
  connectors.
- When a person deletes their account, the system shall stop anything in the
  organization from acting as them at that provider, and shall remove it from
  every pod it was in use in.

**Contracts:** `connector.account.create`, `connector.account.list`, `connector.account.get`, `connector.account.delete`, `connector.connected`, `pod.connector_account.list`

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
- The system shall let a pod member read what a pod connector offers, and it
  shall offer what its installation does.
- The system shall let an organization refresh the catalogue of an installed
  connector when the provider adds capabilities.

**Contracts:** `connector.operation.search`, `connector.operation.discover`, `connector.operation.detail`, `connector.operation.details.batch`, `connector.auth_config.refresh_operations`, `pod.connector.operation.list`, `pod.connector.operation.get`

### PS-CONN-031 — An operation runs as the identity its pod connector is bound to
**Status:** covered

- The system shall run an operation only through a pod connector, named by the
  pod's own slug for it, and shall take no account from the caller.
- When a person runs an operation through a per-member pod connector, the
  system shall run it using their own connected account, and shall record that
  the pod now uses that account.
- When anyone runs an operation through a shared pod connector, the system shall
  run it as the one account its owner shared with the pod, for every caller
  alike.
- When an operation completes, the system shall record
  `connector.operation_executed` with whether it succeeded.
- While an agent or function runs an operation on a person's behalf through a
  per-member pod connector, the system shall use that person's account and
  shall not fall back to anyone else's.
- Where a connector's operations run under an application identity the
  organization installed rather than under any person's account, the system
  shall use that identity for every caller alike, and shall keep it inside what
  the organization granted that installation. A person's own account shall not
  be substituted for it, nor it for a person's account.
- The system shall not let the choice of account vary by caller, by request, or
  by anything a workload can influence: it is the pod connector's mode.
- If a person runs an operation through a per-member pod connector they have no
  account for, then the system shall refuse with `CONNECTION_REQUIRED` and a
  link to connect one — never with somebody else's account.
- If a person runs an operation through a pod connector the pod does not have,
  then the system shall refuse with `POD_CONNECTOR_NOT_FOUND`.

**Contracts:** `pod.connector.operation.execute`, `pod.connector_account.list`, `connector.operation_executed`

### PS-CONN-032 — A slow or failing provider does not damage the pod
**Status:** covered

- The system shall bound how long it waits for a provider, and shall report a
  timeout as a failed operation.
- If a provider returns an error, then the system shall report it as the
  provider's failure, distinct from any refusal of the system's own, rather
  than as a generic failure.
- The system shall keep a provider's slowness from affecting unrelated work in
  the platform.

**Contracts:** `pod.connector.operation.execute`

### PS-CONN-033 — An agent can only use the connectors it was granted
**Status:** covered

- When a person grants an agent or a function `connector.use` on a pod
  connector, the system shall allow exactly that pod connector and refuse the
  rest.
- If an agent attempts an operation through a pod connector it was not granted,
  then the system shall refuse, and nothing shall reach the provider.
- The grant shall be on the pod connector, never on an account: which account
  it runs as stays the connector's decision.
- If an operation would change something at the provider rather than read it,
  then the system shall require a standing grant or the person's approval at the
  time.

**Contracts:** `agent.permissions.replace`, `function.permissions.replace`, `pod.connector.operation.execute`

---

## Capability: Use a connector in a pod

### PS-CONN-034 — A pod declares the connectors it runs, and whose account each uses
**Status:** covered

- When a pod editor declares a pod connector, the system shall bind it to one
  of the organization's installations under a slug unique in the pod, as either
  per member or shared.
- If a member who may not manage the pod's connectors attempts to declare one,
  then the system shall refuse.
- When a person brings one of their own accounts into a pod, the system shall
  keep it to themselves unless they share it. Only an account's owner shall be
  able to share it with the pod.
- If anyone points a shared pod connector at an account its owner has not
  shared, then the system shall refuse.
- When an account's owner withdraws it from the pod, the system shall stop
  every shared pod connector running as it at once, refusing its operations
  with `CONNECTOR_NEEDS_ACCOUNT` until an account is shared again.

**Contracts:** `pod.connector.create`, `pod.connector.list`, `pod.connector.get`, `pod.connector.update`, `pod.connector.delete`, `pod.connector_account.add`, `pod.connector_account.update`, `pod.connector_account.list`

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
