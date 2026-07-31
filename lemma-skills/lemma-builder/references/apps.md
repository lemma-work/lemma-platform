# Apps

A **pod app** is a custom browser UI deployed into the pod — the product humans
operate daily: queues, detail views, workflow inboxes, dashboards, agent chat
beside the work. The app (or a surface) is usually *the product*; design it like
the thing people live in, not an afterthought bolted on last.

> New here? Read `pod-model.md` first. This doc shows how that model appears from
> inside a browser app, then how to plan, build, test, and ship one.

## The pod model, from inside an app

An app is **pod-authenticated**: it runs as the signed-in user and sees exactly
what the pod model allows them to see — nothing special, nothing extra.

- **Identity & RLS.** The SDK calls run as the current user. An **RLS-on** table
  returns only their rows; a **shared** table returns the team's rows. You never
  manage `user_id` — the backend scopes reads/writes. (To show cross-user data you
  need an admin path; most apps don't.) When an *agent* opens the app it acts as
  the delegated user, with that user's grants.
- **Files & search.** `client.files` reads the same `/` (shared) and `/me`
  (personal) tree, the same auto-indexed documents, the same scoped search, and
  the same derived `…/document.md` / `…/pages/*.jpg` artifacts — render a PDF page
  as an `<img>`, search a folder, show converted markdown.
- **Connectors.** `client.connectors.operations.execute(...)` runs an operation on
  the user's connected account (delegated) — the app never holds credentials.
- **Runtime context.** The host injects `window.__LEMMA_CONFIG__` (`podId`,
  `apiUrl`, `authUrl`) at serve time, so the same app runs unchanged against
  local / staging / cloud. Auth is one bearer (`localStorage["lemma_token"]`) or a
  cookie session — the tooling below seeds it for you.

The browser SDK namespaces mirror the model: `tables`, `records`, `files`,
`functions`, `agents`, `conversations`, `workflows`, `schedules`, `connectors`,
`apps`, `podMembers`, `datastore.query(sql)` for aggregates, and
`datastore.watchChanges({ onChange })` for a live WebSocket stream of record
inserts/updates/deletes (the only real-time option on the HTML path). That stream is
the **client-side** reaction — it keeps the UI fresh as rows change; it's distinct from
a server-side `DATASTORE` schedule, which reacts by *doing work* (`schedules-and-triggers.md`).

## Choose a path: HTML or Vite

| | **HTML app** (no build) | **Vite app** (React) |
|---|---|---|
| Use when | One page: dashboard, report, a queue, a form | Multi-page app: routing, state, reused components, maintained over time |
| Author | one `index.html`, vanilla/CDN JS | React + `lemma-sdk` + native blocks |
| SDK | load `lemma-client.js` from `window.__LEMMA_CONFIG__.apiUrl` (see snippet) → `window.LemmaClient`; pod context from the same injected config | `import { LemmaClient } from "lemma-sdk"`, `import.meta.env.VITE_LEMMA_*`, `AuthGuard` |
| Deploy | `lemma apps init ./d --html` → edit → `lemma apps deploy d ./d/index.html` — no build, no env | `lemma apps init` → `npm run dev` → `lemma apps deploy` (builds) |

**Default to HTML for a single page** (the host injects pod context and the SDK
loads from it — nothing to build); reach for **Vite** when the app genuinely needs
routing/components/state. A conversation **widget** uses the same source primitive as
an HTML app. Promotion preserves its authored fragment and wraps it as a standalone
document without embed-only padding or height messaging — see the `lemma-widget` skill.

### HTML app — the whole loop

```bash
lemma apps init ./board --html --title "Board"   # one polished, pod-aware index.html
# edit ./board/index.html
lemma apps deploy board ./board/index.html        # no build, no env — uploaded as-is
```

```html
<!-- Load the SDK from the API origin in the injected window.__LEMMA_CONFIG__,
     then boot. The SDK lives only on the API host; the app's own subdomain does
     NOT serve /public/sdk, so a relative src 404s on deploy. Never hardcode a host
     either (that points local apps at prod) — always derive from cfg.apiUrl.
     This is what `lemma apps init --html` scaffolds. -->
<script>
  (function () {
    var cfg = window.__LEMMA_CONFIG__ || {};
    var base = (cfg.apiUrl || window.location.origin).replace(/\/$/, "");
    var s = document.createElement("script");
    s.src = base + "/public/sdk/lemma-client.js";
    s.onload = boot;
    s.onerror = function () { /* render an error state */ };
    document.head.appendChild(s);
  })();
  async function boot() {
    const client = new window.LemmaClient.LemmaClient();   // no args: config is injected
    const state = await client.initialize();               // { status, user }
    if (state.status !== "authenticated") client.auth.redirectToAuth();
    const rows = (await client.records.list("tickets", { limit: 50 })).items;
  }
</script>
```

For agent chat on an HTML app with zero framework, see
`app-recipes/agent-chat.md` (the `lemma-ui.js` web components).

## Plan first — write `DESIGN.md`

Before scaffolding, write `DESIGN.md` in the app folder. Answer:

- **Purpose & persona** — who opens this and what job do they finish here?
- **Page map** — each route: its purpose, the data shown (tables/files/workflows
  *by name*), and the actions available.
- **Per-page scenarios** — concrete walkthroughs ("open queue → filter
  status=waiting → open ticket → read agent triage → submit approval → queue
  advances").
- **The first 30 seconds** — what the default screen shows. Start with real work
  (queue, assigned forms, next action), never a marketing intro.
- **Layout & states** — nav + panels; designed loading/empty/error/permission
  states for every primary surface.

Proven layouts to steal: **queue-first** (ranked list · detail pane · agent/run
panel · action row), **workflow inbox** (assigned forms + run context + agent
output), **document review** (doc queue · preview + extracted data · validation
form), **team ops** (records by owner/status/SLA · members · linked runs).

## Scaffold, develop, deploy (Vite)

```bash
lemma apps init ./support-app --pod <pod> --title "Support App" \
  --nav sidebar --agent triage-agent --chat-mode right-sidebar
cd support-app && npm run dev        # Vite dev — auto-logged-in as you, any browser
lemma apps deploy support-app --yes  # build + upload
lemma apps open support-app          # open the DEPLOYED app in the agent browser
```

- **Portable by default — never bake pod context.** The scaffolded client is
  `new LemmaClient()` (no args): the host injects `window.__LEMMA_CONFIG__`
  (`podId`/`apiUrl`/`authUrl`) at serve time, so the **same build runs on any
  pod/server**. Deploy the identical bundle to a local pod and a cloud pod and each
  reports its own pod — no rebuild, no per-pod env. `.env.local`'s `VITE_LEMMA_*` is
  only a fallback for `vite dev` (where the host isn't in the loop). Do **not** pass
  `podId: import.meta.env.VITE_LEMMA_POD_ID` into the client — that re-bakes a pod
  and breaks portability.
- `init` materializes a bundled **Vite + React + `lemma-sdk`** template offline
  (pass `--template <git|path>` to override). It writes `.env.local` (dev fallback),
  a working `tsconfig`, `lemma.app.json`, `AGENTS.md`, and a `.lemma.<server>.env`
  binding for the active server (so later `lemma` commands target this pod). Options:
  `--nav sidebar|topbar|single-page`,
  `--style soft|neobrutal|editorial|terminal`, `--chat-mode page|popup|right-sidebar`,
  `--members/--no-members`, `--sdk-path <local-lemma-typescript>` (offline SDK),
  `--proxy` (same-origin `/api` dev proxy — use this in the sandbox/agent browser),
  `--no-install`.
- **Latest SDK, automatically.** `init` resolves the **latest published `lemma-sdk` from
  npm** and pins it (`^x.y.z`) in `package.json` (or a local `file:` when a sibling
  `lemma-typescript` checkout / `--sdk-path` is present). Start every new app with `init` —
  don't hand-write `package.json` or copy an old app's stale SDK pin.
- `deploy` builds with the project env, zips `dist/` + source, uploads, serves.
- **Auth/testing — one bearer.** The dev server's dev-only plugin resolves the
  current token via `lemma auth print-token` and seeds `localStorage["lemma_token"]`
  before the app boots, so opening the dev URL in **any** browser (or the agent
  browser) is authenticated — no login UI, never written to a file, never shipped
  in `vite build`. Inside the workspace sandbox use `--proxy` and
  `agent-browser open http://localhost:<port>`; for a deployed app use
  `lemma apps open <slug>` (registers the agent bearer scoped to the API origin).

Apps round-trip in bundles by shape: **Vite** → `apps/<name>/<name>.json` +
`apps/<name>/source/`; **HTML** → `apps/<name>/<name>.json` + `apps/<name>/html.html`.
**Trap:** never put a single HTML file under `source/` — a `source/` dir is read as
a Vite project and import demands `package.json`.

## The TypeScript SDK

```ts
import { LemmaClient } from "lemma-sdk";
// No args: podId / apiUrl / authUrl come from the host-injected
// window.__LEMMA_CONFIG__ at runtime (VITE_LEMMA_* is a dev-only fallback).
export const client = new LemmaClient();
```

Wrap the app in **`AuthGuard`** (from `lemma-sdk/react`). It uses the host-injected
app name for its loader, sign-in, access-request, pending, and retry states;
pass `appName` only to override that identity in local development. Prefer **hooks**
(`lemma-sdk/react`) — the verified catalog:

- **Auth/user**: `useAuth`, `useCurrentUser`, `usePodAccess`
- **Records**: `useRecords`, `useRecord`, `useCreateRecord`, `useUpdateRecord`,
  `useDeleteRecord`, `useBulkRecords`, `useRecordForm`, `useRecordSchema`,
  `useRecordAggregates`, `useJoinedRecords`, `useRelatedRecords`,
  `useForeignKeyOptions`, `useDatastoreQuery`, `useTables`
- **Realtime**: `useLiveRecords` (a list that stays live via the table WebSocket —
  fetches once, merges deltas in place, no polling), `useWatchChanges` (raw subscription
  for your own state/cache). Needs SDK ≥ 0.4.1.
- **Files**: `useFiles`, `useFile`, `useFileTree`, `useFileSearch`,
  `useFilePreview`, `useUploadFile`, `useCreateFolder`, `useDeleteFile`,
  `useUpdateFile`
- **Agents/chat**: `useConversations`, `useConversationMessages`,
  `useAgentInputSchema`, `useAgentTask`/`<AgentTask>` (one-shot),
  `<AgentThread>` (multi-turn)
- **Workflows**: `useWorkflowStart`, `useWorkflowRuns`, `useWorkflowRun`,
  `useWorkflowForm`/`<WorkflowForm>`, `useWorkflowResume`,
  `useWorkflowRunWaitAssignments`, `useFlowRunHistory`
- **Functions**: `useFunctionRun`, `useFunctionRuns`, `useFunctionSession`
- **Members/search**: `useMembers`, `useGlobalSearch`

Plus **generated CRUD hooks** (`use<Resource>List/Get/Create/Update/Delete` for
records/agents/tables/schedules/apps/functions/workflows) — TanStack-Query hooks
where a write **auto-refreshes the matching list** (need a `QueryClientProvider`,
which the scaffold mounts). The SDK source ships in the sandbox at
**`/sdk/lemma-typescript`** — read it for exact signatures
(`cat /sdk/lemma-typescript/src/react/useRecords.ts`).

## Calling the API well — fetch once, subscribe, don't re-render the world

Most app bugs are API-call discipline, not logic. Four rules:

- **Fetch once, not in a loop.** Use the hooks (`useRecords`, `useDatastoreQuery`, or the
  generated `use<Resource>List`) — they fetch on mount and hand you `refresh()` /
  `loadMore()`. **Never** call `client.records.list(...)` during render, or in a
  `useEffect` whose deps rebuild an object/array every render — that's the infinite-refetch
  loop. For counts/group-bys use `datastore.query(sql)` instead of pulling pages and
  reducing in JS.
- **Let writes invalidate themselves.** The generated CRUD hooks under a
  `QueryClientProvider` (the scaffold mounts one) **auto-refresh the matching list** on a
  mutation — don't hand-wire a refetch, and don't re-fetch the whole list on every
  keystroke (filter/derive client-side, or debounce).
- **Realtime = subscribe, never poll.** For a live list use **`useLiveRecords`** (fetches
  once, then merges row deltas in place over the table WebSocket); for custom state use
  **`useWatchChanges`**. For live agent output use `useConversationMessages` /
  `useAgentTask` (SSE streaming). **Never** `setInterval(refetch)` — polling flickers the
  UI, re-renders the world, and hammers the API (pod-model heuristic #4: *never poll a
  table*).
- **Don't flicker, don't reload.** Merge changes **in place keyed by `record_id`** (not a
  whole-list replace), give rows a stable `key={row.id}`, and create the client **once** at
  module scope + gate auth **once** with `AuthGuard`. Re-creating the client or calling
  `initialize()` / `redirectToAuth()` on every render is the "app reloads on every call"
  smell.

The live-list implementation is in `app-recipes/rls-table.md`.

## Recipes — the common patterns (load only what you need)

Each is a small, self-contained, copy-paste doc. `apps.md` stays lean; open the
one you need:

| Recipe | What it shows |
|---|---|
| `app-recipes/agent-chat.md` | Put **agent chat** in an app — `<lemma-agent-task>`/`<lemma-agent-thread>` web components (HTML), `<AgentTask>`/`<AgentThread>` + `useConversations`/`useConversationMessages` (React), and reading the final answer correctly. |
| `app-recipes/rls-table.md` | Read/write a table with **live auto-refresh** — `useRecords` + filters/sort, the generated CRUD hooks, and how RLS scopes what shows. |
| `app-recipes/workflow-form.md` | A **workflow inbox** — render FORM waits assigned to the user with `useWorkflowRunWaitAssignments` + `<WorkflowForm>`/`useWorkflowResume`. |
| `app-recipes/file-viewer.md` | **Files in an app** — upload, scoped search, and showing a converted page image / markdown. |
| `app-recipes/connector-action.md` | Call a **connector operation** from an app (delegated account), with discovery + a safe action button. |

(The same "lean doc + recipe catalog" convention can extend to other resources as
patterns accrue — add a recipe file, link it here.)

## Native components (registry blocks)

Stock Lemma UI on top of the headless SDK — try these before building custom:

```bash
npx lemma-sdk init-shadcn
npx shadcn@latest add @lemma/lemma-records-view @lemma/lemma-assistant-experience
```

Workhorses: `lemma-records-view` (records as list/grid/kanban/calendar/… with
search/filter/create + detail sheet), `lemma-record-form`, `lemma-detail-panel`,
`lemma-assistant-experience` (full agent chat), `lemma-workflow-runner`,
`lemma-file-browser`/`lemma-document-workspace`, `lemma-global-search`, plus shell
blocks (`lemma-members`, `lemma-user-menu`, `lemma-breadcrumbs`, …). They install as
editable source under `@/components/lemma/…` — restyle to your tokens; don't fork
their data wiring.

## Design — principles, not a fixed system

An app is used for hours: make it **immediately legible, calm, and distinctive**.
Scaffold-default UI (grey sidebar, flat white cards, system font) is not done.
These are principles — pick a look that fits the use case; you're not required to
adopt any one design system.

- **Pick one tone and execute it everywhere.** A support queue and a legal review
  app shouldn't look the same. `--style` presets (soft/neobrutal/editorial/terminal)
  are starting points. `lemma-frontend/design.md` and the per-app accent helper
  (`lib/app/app-accent.ts`) are an *optional* token set to start from, not a mandate.
- **Tokens before components.** One root variable set: surfaces (≈3 depths),
  borders (subtle/strong), text (primary/secondary/muted), one accent + hover, one
  color per status, a 4/8/12/16/24/32 spacing scale. A dominant base + one sharp
  accent beats a timid even palette.
- **Legibility & density.** Three font sizes max in dense UIs; tabular/mono figures
  for ids, timestamps, metadata so columns align; panel layouts with intentional
  widths; selected row = accent left-border, not a heavy fill.
- **Status as badge**, not color alone (`OPEN`/`WAITING`/`DONE`/`ERROR`).
- **Responsive & accessible.** Works at **375px**; visible keyboard focus; actions
  never hover-gated; 44px touch targets.
- **Designed states everywhere** — skeletons (not spinners), helpful empty copy +
  next action, visible/actionable errors, permission states.

## Test it (and view it)

- `npm run build` passes.
- In `npm run dev` (auto-authed; in the sandbox open with
  `agent-browser open http://localhost:<port>` and scaffold with `--proxy`): walk
  one full DESIGN.md scenario — auth load, queue, select, a write, start/advance a
  workflow, submit an assigned form, file preview. Confirm `200`/`201`, not `401`.
- **View it with your own eyes.** Use the browser snapshot loop (`browser` skill) +
  the **view-image** capability on a screenshot to actually *see* the rendered app;
  and view-image works directly on pod/workspace files too (a `…/pages/page_0001.jpg`
  child, an uploaded image) — see `file-viewer.md`.
- Deploy, then `lemma apps open <slug>` and repeat the core scenario served.
- **Confirm the deploy landed** — don't trust CLI "success": note the release
  id/timestamp, re-`lemma apps get <name>`, hard-refresh with cache-busting, and
  check the live DOM for a unique string from this revision. If stale, redeploy.

## Limits & gotchas

- Apps run inside an iframe in the shell — keep them self-contained; no top-level
  navigation assumptions.
- **Don't poll for fresh data.** A `setInterval` refetch flickers and hammers the API;
  use `useLiveRecords` / `useWatchChanges` (the table WebSocket). For a filtered live
  list, pass `reconcile: "refetch"` or an `accept` predicate (`app-recipes/rls-table.md`).
- Vite `deploy` requires `dist/index.html`; the template ships no lockfile (plain
  `npm install`). HTML apps skip the build and need no `VITE_*` env.
- **Reading agent replies:** one turn emits several `role:"assistant"` messages by
  `kind` (`thinking`/`tool_call`/`tool_return`/`notification`/`text`). The answer is
  the `text` message with `metadata.is_final_answer === true` (content in `.text`).
  In React use `useConversationMessages().finalOutputText`; never match first/last.
  (See `app-recipes/agent-chat.md`.)
- `public_slug` is globally unique — conflicts auto-suffix on import.

## See also

- The pod model → `pod-model.md` · planning a pod → `pod-design.md`
- Recipes → `app-recipes/*.md` · inline widgets → the `lemma-widget` skill
- Testing in a browser → the `browser` skill · operate the pod → the `lemma-user` skill
