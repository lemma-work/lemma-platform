# Packaging and reuse

**Journey:** A person takes a pod that works, hands it to someone else, and it
works for them too.

A **bundle** is a pod written down — its tables, files, functions, agents,
workflows, schedules, surfaces, and apps, in a portable archive. Export it,
publish it, import it somewhere else. An **app** is the other direction: a
purpose-built interface for one pod, so the people using the work do not have to
understand the pod behind it.

The promise for the person importing: nothing is applied until they have seen
exactly what it will do to their pod. The promise for the person exporting:
their credentials do not travel with it.

---

## Capability: Take a pod with you

### PS-PACK-001 — A person exports a pod as a bundle
**Status:** covered

- When a person exports a pod, the system shall produce an archive of its
  resources and shall give them a link to download it.
- When an export completes, the system shall record `bundle.exported`.
- The system shall report the export as queued, working, ready, or failed, so a
  person knows whether to keep waiting.
- The system shall require the person to be signed in to download, in addition
  to holding the link.

**Contracts:** `pod.bundle.export.start`, `pod.bundle.export.get`, `pod.bundle.download`, `bundle.exported`

### PS-PACK-002 — A bundle carries the work, not the secrets
**Status:** planned

- The system shall never include a credential, provider secret, or connected
  account in an exported bundle.
- Where a pod's resources depend on a connected account, the system shall record
  what kind of account is needed, so the importer knows what to connect.
- The system shall carry the grants that resources hold, so an imported pod is
  as functional as the one it came from.

**Contracts:** `pod.bundle.export.start`, `pod.bundle.import.get`

---

## Capability: Bring someone else's pod in

### PS-PACK-010 — A person sees the plan before anything changes
**Status:** planned

- When a person starts an import, the system shall compare the bundle to their
  pod and shall present a plan of what it would create, change, and remove.
- The system shall change nothing until the person approves the plan.
- The system shall require explicit confirmation for anything destructive, named
  individually rather than as a single blanket approval.
- When an import starts, the system shall record `import.started`.

**Contracts:** `pod.bundle.import.start`, `pod.bundle.import.get`, `pod.bundle.upload`, `import.started`

### PS-PACK-011 — A person can adjust and re-plan before applying
**Status:** planned

- When a person changes the values an import asks for, the system shall produce
  a fresh plan against those values.
- When a person cancels an import before applying, the system shall change
  nothing and shall release what it staged.
- The system shall expire an import left unapproved, rather than holding staged
  data indefinitely.

**Contracts:** `pod.bundle.import.replan`, `pod.bundle.import.cancel`, `pod.bundle.import.get`

### PS-PACK-012 — Applying an import either finishes or can be safely retried
**Status:** planned

- When a person approves a plan, the system shall apply its steps and shall
  report progress as it goes.
- If applying is interrupted, then the system shall be able to resume without
  duplicating what it already did.
- When an import completes, the system shall record `import.completed`.
- When an import completes, the system shall record on the pod where it came
  from, so a person can later tell what was installed and when.

**Contracts:** `pod.bundle.import.apply`, `pod.bundle.import.get`, `pod.bundle.import.events`, `import.completed`

### PS-PACK-013 — A hostile bundle cannot damage the platform
**Status:** planned

- If an archive attempts to write outside the place it is being unpacked into,
  then the system shall reject it.
- If an archive expands far beyond its compressed size, then the system shall
  reject it rather than exhausting storage.
- The system shall bound how much a bundle may contain — items, records, files,
  and total size — and shall say which limit was exceeded.
- The system shall bound how many imports one organization may start in a day.

**Contracts:** `pod.bundle.import.start`, `pod.bundle.upload`

### PS-PACK-014 — An imported pod works without further wiring
**Status:** planned

- When an import creates a function or an agent, the system shall apply the
  grants it came with, so it runs rather than failing on its first use.
- When an import creates a schedule or a surface, the system shall leave it in
  the state the bundle specified, rather than silently activating it.
- If an imported resource needs something the pod does not have, then the system
  shall say what is missing rather than importing something that cannot work.

**Contracts:** `pod.bundle.import.apply`, `function.permissions.get`, `agent.permissions.get`

---

## Capability: Publish and share a pod

### PS-PACK-020 — A person publishes a pod so others can install it
**Status:** planned

- When a person publishes a pod to a repository through their connected account,
  the system shall export it and put it there.
- The system shall report the publication as queued, working, completed, or
  failed.
- If the person's connected account cannot write to the destination, then the
  system shall fail with what the provider said rather than a generic error.

**Contracts:** `pod.bundle.publish.start`, `pod.bundle.publish.get`, `pod.bundle.publish.events`

### PS-PACK-021 — A shared bundle can be viewed before it is installed
**Status:** planned

- When someone opens a shared bundle link, the system shall let them see what it
  contains before installing it.
- When a shared link is viewed, the system shall record `share_link.viewed`.
- The system shall not require the viewer to be a member of the pod that
  published it.

**Contracts:** `pod.bundle.download`, `share_link.viewed`

---

## Capability: Give the work an interface

### PS-PACK-030 — A person builds an app for a pod
**Status:** covered

- When a person creates an app in a pod, the system shall record it and shall
  record `app.created`.
- When a person uploads a build, the system shall keep it as an immutable
  release and shall serve it as the app's current version.
- The system shall keep earlier releases, so a person can tell what was serving
  at a given time.
- When a person promotes something an agent produced into an app, the system
  shall carry it across without them rebuilding it.

**Contracts:** `app.create`, `app.bundle.upload`, `app.create_from_widget`, `app.get`, `app.created`

### PS-PACK-031 — An app reaches the people it is meant for
**Status:** gap

- When an app is published, the system shall serve it at its own address and
  shall record `app.published`.
- When someone opens an app, the system shall record `app.session_started`.
- The system shall give an app's user exactly the pod access their own identity
  carries, and shall not let the app widen it.
- If someone without access to the pod opens an app, then the system shall
  refuse rather than serving pod data.

> **Gap:** `app.get` returns the full app record to any signed-in person,
> including one in neither the pod nor its organization — while `app.list` on
> the same controller refuses. See `DEV-PACK-001`.

**Contracts:** `app.get`, `app.asset.get`, `app.published`, `app.session_started`

### PS-PACK-032 — A person can retrieve what an app was built from
**Status:** planned

- When a person with pod access downloads an app's source or build, the system
  shall give them the exact archive that was uploaded.
- If a person without pod access requests either, then the system shall refuse.

**Contracts:** `app.source.archive.get`, `app.dist.archive.get`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Connecting the account used to publish | [Connectors and accounts](connectors-and-accounts.md) |
| What the imported functions and agents do | [Automating work](automating-work.md), [Agents and conversations](agents-and-conversations.md) |
| Grants carried by imported resources | [Sharing and permissions](sharing-and-permissions.md) |
| Rules for generated bundles and SDKs | [Generated-code policy](../../security/generated-code-policy.md) |
