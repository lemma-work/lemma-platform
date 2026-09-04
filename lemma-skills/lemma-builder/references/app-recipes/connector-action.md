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
const pod = await client.pods.get(client.podId!);
const scope = { organizationId: pod.organization_id, authConfigName: "my-gmail" };

// 1. operations available for this installed auth config (kind-specific):
const ops = await client.connectors.operations.list(scope);           // → array
//   ...or narrow by intent: operations.list(scope, { query: "send email", limit: 5 })

// 2. the operation's input schema (drive your form / validate the payload):
const detail = await client.connectors.operations.get(scope, "gmail_send_email");

// 3. execute on the user's account (account auto-resolves; 4th arg pins one):
const result = await client.connectors.operations.execute(
  scope,
  "gmail_send_email",
  { recipient_email: "bob@example.com", subject: "Update", body: "…" },
  // accountId,   // optional — omit to use the signed-in user's own account
);
```

`operations.list` returns the items array directly; `discover` is the same call
returning the full response. `details(scope, names?)` fetches several schemas at once.

## Make it a safe action

- **Discover, don't guess** operation names — list/search first; names are
  kind-specific (`package` and `composio` differ).
- **Confirm side-effects.** An operation that sends/posts/charges is irreversible —
  gate it behind an explicit confirm and show what will happen.
- **Handle the no-account case.** If the user hasn't connected an account, the call
  fails — detect it and link them to connect (the app can't connect on their
  behalf): surface `lemma connectors accounts create` / the connect flow.
- For server-side calls (a function/agent acting for the user), the same operation
  runs through the in-function SDK with a granted connector — see `functions.md`.

> Exact surface: `cat /sdk/lemma-typescript/src/namespaces/connectors.ts`; the org
> auth-config names come from `lemma connectors overview`.
