# Packaging and reuse

**Journey:** A person takes a pod that works, hands it to someone else, and it
works for them too.

A **bundle** is a pod written down — its tables, files, functions, agents,
workflows, schedules, surfaces, and apps, in a portable archive. Export it,
publish it, import it somewhere else. An **app** is the other direction: a
purpose-built interface for one pod, so the people using the work do not have to
understand the pod behind it.

The promise for the person importing: nothing is applied until they have seen
exactly what it will do to their pod, and nothing they already have is replaced.
The promise for the person exporting: their credentials do not travel with it.

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
**Status:** covered

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
**Status:** covered

- When a person starts an import, the system shall compare the bundle to their
  pod and shall present a plan of what it would add, and of what it would keep
  because the pod already has it.
- The system shall say what the plan brings in plain language, and shall keep
  the component-by-component detail on the same screen for whoever wants it.
- The system shall change nothing until the person approves the plan.
- When an import starts, the system shall record `import.started`.

**Contracts:** `pod.bundle.import.start`, `pod.bundle.import.get`, `pod.bundle.upload`, `import.started`

### PS-PACK-011 — A person can adjust and re-plan before applying
**Status:** covered

- When a person changes the values an import asks for, the system shall produce
  a fresh plan against those values.
- When a person cancels an import before applying, the system shall change
  nothing and shall release what it staged.
- The system shall expire an import left unapproved, rather than holding staged
  data indefinitely.

**Contracts:** `pod.bundle.import.replan`, `pod.bundle.import.cancel`, `pod.bundle.import.get`

### PS-PACK-012 — Applying an import either finishes or can be safely retried
**Status:** covered

- When a person approves a plan, the system shall apply its steps and shall
  report progress as it goes.
- If applying is interrupted, then the system shall be able to resume without
  duplicating what it already did.
- When an import completes, the system shall record `import.completed`.
- When an import completes, the system shall record on the pod where it came
  from, so a person can later tell what was installed and when.

**Contracts:** `pod.bundle.import.apply`, `pod.bundle.import.get`, `pod.bundle.import.events`, `import.completed`

### PS-PACK-013 — A hostile bundle cannot damage the platform
**Status:** covered

- If an archive attempts to write outside the place it is being unpacked into,
  then the system shall reject it.
- If an archive expands far beyond its compressed size, then the system shall
  reject it rather than exhausting storage.
- The system shall bound how much a bundle may contain — items, records, files,
  and total size — and shall say which limit was exceeded.
- The system shall bound how many imports one person may start in a day, and
  shall bound exports separately, so a day of one never blocks the other. The
  bound is per person rather than per organization on purpose: what it guards
  is one account's worth of worker time, and a shared allowance would let one
  member's runaway loop lock out everybody else.

**Contracts:** `pod.bundle.import.start`, `pod.bundle.upload`

### PS-PACK-014 — An imported pod works without further wiring
**Status:** covered

- When an import creates a function or an agent, the system shall apply the
  grants it came with, so it runs rather than failing on its first use.
- When an import creates a schedule or a surface, the system shall leave it in
  the state the bundle specified, rather than silently activating it.
- If an imported resource needs something the pod does not have, then the system
  shall say what is missing rather than importing something that cannot work.

**Contracts:** `pod.bundle.import.apply`, `function.permissions.get`, `agent.permissions.get`

### PS-PACK-015 — An import keeps what a pod already has
**Status:** planned

- When an import brings something the pod already has, the system shall keep
  the pod's own as it is rather than replace it.
- When an import brings a table the pod already has, the system shall add only
  the columns the table lacks.
- The system shall never remove a column, a record, a file, or anything else
  from a pod during an import.
- When an import brings records for a table the pod already has, the system
  shall leave that table's records as they are.
- The system shall say, in the plan, each thing it keeps because the pod
  already has it.

**Contracts:** `pod.bundle.import.start`, `pod.bundle.import.get`, `pod.bundle.import.apply`

---

## Capability: Publish and share a pod

### PS-PACK-020 — A person publishes a pod so others can install it
**Status:** covered

- When a person publishes a pod to a repository through their connected account,
  the system shall export it and put it there.
- The system shall report the publication as queued, working, completed, or
  failed.
- If the person's connected account cannot write to the destination, then the
  system shall fail with what the provider said rather than a generic error.

**Contracts:** `pod.bundle.publish.start`, `pod.bundle.publish.get`, `pod.bundle.publish.events`

### PS-PACK-021 — A shared bundle can be viewed before it is installed
**Status:** covered

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
- The system shall retain a bounded number of earlier builds and their source
  under the configured retention policy, preserving the live release. History
  shall identify removed builds without offering them for preview or promotion.
- An editor shall be able to preview a retained release without changing the live
  app, and promote it with its own source. If its original source is unknown,
  an export shall not substitute source from another release.
- When a person promotes something an agent produced into an app, the system
  shall carry it across without them rebuilding it.

**Contracts:** `app.create`, `app.bundle.upload`, `app.create_from_widget`, `app.get`, `app.created`

### PS-PACK-031 — An app reaches the people it is meant for
**Status:** covered

- When an app is published, the system shall serve it at its own address and
  shall record `app.published`.
- When someone opens an app, the system shall record `app.session_started`.
- The system shall give an app's user exactly the pod access their own identity
  carries, and shall not let the app widen it.
- If someone without access to the pod opens an app, then the system shall
  refuse rather than serving pod data.
- Where an app uses a hosted HTTPS address, the system shall open public builds
  anonymously.
- When a person allowed to view a private app opens its hosted HTTPS address,
  the system shall show the app, including pod, personal and restricted apps.
- When a person allowed to view a private app opens it in a workspace tab,
  the system shall show the app, including pod, personal and restricted apps.
- When a signed-out person opens a private app, the system shall offer a generic
  sign-in action.
- When a person signs in from an app's sign-in action, the system shall return
  them to the original app path, query and fragment.
- While a person is signed out, the system shall reveal no more about an absent
  app than about a private app.
- When a person's sign-in expires, the system shall refuse further access to
  private apps within a minute, until they sign in again.
- When a person's sign-in is revoked, the system shall refuse further access
  to private apps within a minute, until they sign in again.
- When an account becomes ineligible, the system shall refuse that person's
  further access to private apps within a minute.
- When a person's app permission is removed, the system shall end their access
  to that private app within a minute.
- When a person opens a private release preview, the system shall additionally
  require permission to edit that app.
- When a person loses access to a private app, the system shall refuse to load
  its pages and files even if they opened them before.
- When a person opens a private app, the system shall grant no access to other
  apps merely because they opened it.
- When an app acts on a person's behalf, the system shall allow only actions
  that person is permitted to perform.

**Contracts:** `app.get`, `app.asset.get`, `app.published`, `app.session_started`

### PS-PACK-032 — A person can retrieve what an app was built from
**Status:** covered

- When a person with pod access downloads an app's source or build, the system
  shall give them the exact archive that was uploaded.
- The system shall keep an app's source for every app it deploys, including one
  promoted from something an agent produced, so what a person gets back is what
  the app was authored from and not only its built output.
- If a person without pod access requests either, then the system shall refuse.

**Contracts:** `app.source.archive.get`, `app.dist.archive.get`

### PS-PACK-033 — An app opened inside a Lemma takes the Lemma's look
**Status:** manual

> **Verified by:** opening an app inside a Lemma in the browser, switching
> Settings > Appearance between light, dark and system (and flipping the
> device's appearance while on system), and checking that the app redraws each
> time without reloading; plus `npm test` in `lemma-frontend`, whose
> `tests/app-theme.test.ts` covers when the theme is sent and to whom. The API
> scenario suite cannot see a framed page.

- When an app opened inside a Lemma loads, including after a reload, a
  navigation inside the app, or a sign-in, the system shall send it the
  Lemma's current light or dark theme and the tokens that draw it.
- When a person changes their appearance, or the device switches between light
  and dark while they follow it, the system shall send the app the new theme,
  so the app follows without reloading.
- The system shall send the theme only to the app's own origin, and shall send
  nothing to a frame whose origin cannot be addressed.
- An app that does not listen for the theme shall be drawn exactly as before.

### PS-PACK-034 — A Lemma with apps opens on its apps
**Status:** manual

> **Verified by:** opening, in the browser on a local stack, a Lemma that has
> apps from the sidebar, from its name in a list and from a link to the Lemma,
> then one that has none, and checking where each lands and that Home never
> shows first. The API scenario suite cannot see the frame.

- When a person opens a Lemma that has apps, by its name or by a link to the
  Lemma, the system shall open it on its first app, in the order the Lemma
  lists them.
- Where a Lemma has no apps, the system shall open it on its Home.
- While the system finds out which of the two it is, it shall say the Lemma is
  opening, rather than show Home and then replace it.
- The sidebar shall list an open Lemma's apps under its name, with a way to ask
  for another.

### PS-PACK-035 — Where a person is in an app is an address they can share
**Status:** manual

> **Verified by:** on a local stack in the browser, opening a record in an app,
> copying the address into a new tab, reloading, and pressing Back and Forward;
> plus `npm test` in `lemma-frontend` (`tests/named-address.test.ts`,
> `tests/location-bridge.test.ts`, `tests/outcomes.test.ts`) and in
> `lemma-typescript` (`browser-location.test.ts`). The API scenario suite
> cannot see the frame.

- While an app is open in a Lemma, the address shall name the organization,
  the Lemma, the app and the page inside the app, in words a person can read:
  `/l/acme/sales/apps/people/1042`.
- When an app moves to another page inside itself, the system shall make it a
  step in the address, so Back returns to the page before and the app is told
  to go there.
- When a person reloads, or opens an address someone copied, the system shall
  open the app on the page the address names.
- Where two of an organization's Lemmas have names that read the same, or a
  name reads as one of a Lemma's own sections, the system shall keep that
  Lemma's id in the address.
- An address that names a Lemma by its id shall keep working, and the system
  shall show it in names where it can.
- The system shall record in analytics what kind of place an address is, and
  never the names of the organization or the Lemma, or the page inside an app.

### PS-PACK-036 — One app opens another app of the same Lemma
**Status:** manual

> **Verified by:** on a local stack in the browser, an app asking for another
> app at one of its records inside a Lemma, the same app opened on its own in a
> tab, and pressing Back after each; plus `npm test` in `lemma-typescript`
> (`browser-location.test.ts`) and in `lemma-frontend`
> (`tests/location-bridge.test.ts`). The API scenario suite cannot see the frame.

- When an app open inside a Lemma asks to open another app of that Lemma at a
  page, the system shall switch to that app at that page, as a step Back undoes.
- When the app is open on its own in a browser tab, the SDK shall go to the
  other app's own address at that page.
- When the app is open inside an AI tool's conversation, the SDK shall do
  nothing and report that it did not open, because another app loaded there
  would arrive signed out.
- The system shall act only on what the app in front asks, and shall answer
  only that app, at its own origin.
- If the app asked for is not one of the Lemma's, then the system shall say it
  is not here rather than open something else.

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Connecting the account used to publish | [Connectors and accounts](connectors-and-accounts.md) |
| What the imported functions and agents do | [Automating work](automating-work.md), [Agents and conversations](agents-and-conversations.md) |
| Grants carried by imported resources | [Sharing and permissions](sharing-and-permissions.md) |
| Rules for generated bundles and SDKs | [Generated-code policy](../../security/generated-code-policy.md) |
