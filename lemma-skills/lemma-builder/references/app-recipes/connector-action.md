# Recipe — call a connector operation from an app

Run a third-party action (send email, post message, …) through one of the pod's
connectors, from the app. (← `apps.md`)

## The model (see `connectors.md`)

The pod declares **pod connectors** by slug (`gmail`, `support-inbox`), each on
one of the org's installations. The app addresses an operation by **pod connector
slug** — find them with `lemma connectors pod-list` — and the connector decides
whose account runs it: the signed-in person's own (PER_MEMBER) or the one shared
with the pod (SHARED). The app never holds credentials or names an account. The
person needs `connector.use` on the connector (POD_USER and above by default).

## Discover, then execute

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
  kind-specific (`http` and `composio` differ).
- **Confirm side-effects.** An operation that sends/posts/charges is irreversible —
  gate it behind an explicit confirm and show what will happen.
- **Handle the no-account case.** When the person has no account for a PER_MEMBER
  connector, or the shared one is gone or expired, the call fails with a typed
  error (`CONNECTION_REQUIRED`, `REAUTH_REQUIRED`, `CONNECTOR_NEEDS_ACCOUNT`)
  whose body carries `connect_url`. Link them there; the app can't connect on
  their behalf.
- For server-side calls (a function/agent acting for the user), the same operation
  runs through the in-function SDK with a granted connector — see `functions.md`.

> Exact surface: `cat node_modules/lemma-sdk/dist/namespaces/connectors.d.ts`; the
> pod's connector slugs come from `lemma connectors pod-list`.
