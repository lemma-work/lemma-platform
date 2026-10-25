# Recipe — call a connector operation from an app

Run a third-party action (send email, post message, …) on the user's connected
account, from the app. (← `apps.md`)

## The model (see `connectors.md`)

A **connector** (catalog: `gmail`, `slack`…) has an org **auth-config**; each user
has an **account** under it; the connector exposes **operations**. The app executes
an operation **on the user's account** (delegated) — it never holds credentials.
You address operations by the **auth-config name**, which you find with
`lemma connectors overview`.

## Discover, then execute

Every `connectors.operations.*` call takes a **scope** first — `{ organizationId,
authConfigName }` — because auth configs are org-scoped, not pod-scoped. The pod knows
its org, so resolve it once:

```ts
// A pod connector is addressed by its slug; the pod declares it (`lemma
// connectors pod-list`). It decides whose account runs the call: the signed-in
// person's own (PER_MEMBER) or the one the pod shares (SHARED).
const slug = "gmail";

// 1. operations this connector offers (kind-specific):
const ops = await client.connectors.pod.operations(slug);           // → array
//   ...or narrow by intent: pod.operations(slug, { query: "send email", limit: 5 })

// 2. the operation's input schema (drive your form / validate the payload):
const detail = await client.connectors.pod.operation(slug, "gmail_send_email");

// 3. execute — no account argument; the pod connector picks it:
const result = await client.connectors.pod.execute(
  slug,
  "gmail_send_email",
  { recipient_email: "bob@example.com", subject: "Update", body: "…" },
);
```

`pod.operations` returns the items array directly. Org-level discovery
(`connectors.operations.list(scope)`) still browses an installation before a pod
declares it, but cannot run anything.

## Make it a safe action

- **Discover, don't guess** operation names — list/search first; names are
  kind-specific (`package` and `composio` differ).
- **Confirm side-effects.** An operation that sends/posts/charges is irreversible —
  gate it behind an explicit confirm and show what will happen.
- **Handle the no-account case.** When the person has no account for a PER_MEMBER
  connector, or the shared one is gone or expired, the call fails with a typed
  error (`CONNECTION_REQUIRED`, `REAUTH_REQUIRED`, `CONNECTOR_NEEDS_ACCOUNT`)
  whose body carries `connect_url`. Link them there; the app can't connect on
  their behalf.
- For server-side calls (a function/agent acting for the user), the same operation
  runs through the in-function SDK with a granted connector — see `functions.md`.

> Exact surface: `cat node_modules/lemma-sdk/dist/namespaces/connectors.d.ts`; the org
> auth-config names come from `lemma connectors overview`.
