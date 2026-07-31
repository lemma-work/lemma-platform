# Lemma TypeScript SDK

`lemma-sdk` is the headless TypeScript SDK for Lemma. Use `lemma-sdk` for the core client and shared helpers, and use `lemma-sdk/react` as the main app-building surface for hooks and auth primitives.

`AuthGuard` and its app-entry surfaces stay in `lemma-sdk/react`. The product direction remains hooks-first, while the registry ships stock Lemma UI blocks when you want installable agent conversation, records, file, workflow, collaboration, or shell surfaces.

## Install

```bash
npm install lemma-sdk
```

If your app wants stock Lemma UI installs, configure the registry with:

```bash
npx lemma-sdk init-shadcn
```

That adds this namespace to your app's `components.json`:

```json
{
  "registries": {
    "@lemma": "https://cdn.jsdelivr.net/gh/lemma-work/lemma-platform@main/lemma-typescript/public/r/{name}.json"
  }
}
```

If your app does not have `components.json` yet, run `npx shadcn@latest init` first or after the command above.

## Core Client

```ts
import { LemmaClient } from "lemma-sdk";

const client = new LemmaClient({
  podId: "<pod-id>",
});

await client.initialize();

const tables = await client.tables.list();
const records = await client.records.list("tickets");
```

Pod-scoped namespaces include `tables`, `records`, `agents`, `conversations`, `workflows`, `schedules`, `functions`, `files`, the apps facade (`desks`), `integrations`, `resources`, and `datastore`. New runtime code should use agents plus conversations.

### Live datastore changes (WebSocket)

`client.datastore.watchChanges(...)` opens a live change stream over a WebSocket and invokes `onChange` for every record insert/update/delete the caller is allowed to see. The server enforces visibility: RLS (per-user) tables deliver only the caller's own rows; shared tables deliver every member's changes.

```ts
const handle = client.datastore.watchChanges({
  table: "tickets",            // omit to watch every readable table in the pod
  onChange: (frame) => {
    // frame.operation: "insert" | "update" | "delete"
    // frame.table_name, frame.record_id, frame.payload (delete carries {})
    console.log(frame.operation, frame.table_name, frame.record_id);
  },
  onStatus: (s) => console.log(s), // "connecting" | "open" | "reconnecting" | "closed"
  onReady: ({ since }) => console.log("live, resume cursor:", since),
  onError: (err) => console.error(err),
});

// later
handle.close(); // stop the stream and prevent further reconnects
```

- **Auth.** The browser WebSocket API can't set request headers, so the access token is carried as an `?access_token=` query parameter (resolved from the session). For same-site cookie sessions, pass `useCookie: true` and omit the token.
- **Resilient.** Reconnects with full-jitter backoff (500ms → 30s) and resumes from the last seen `stream_id`, so a brief drop replays missed changes rather than losing them. A rejected session is refreshed once before reconnecting. Cap attempts with `maxRetries` (unlimited by default).
- **Resume.** Each frame carries a `stream_id`; pass it back as `since` to resume after a known point. `onReady` reports the current cursor when the stream goes live.
- **React.** In a component, prefer the hooks (`lemma-sdk/react`): `useLiveRecords({ client, tableName })` for a live record list that merges deltas **in place** (no flicker, no polling), or `useWatchChanges({ client, table, onChange })` to drive your own state/cache/`queryClient.invalidateQueries`. Both wrap this stream and close it on unmount.
- **Standalone.** Outside a `LemmaClient` instance, import `watchDatastoreChanges(apiUrl, auth, podId, options)` and pass any `ChangeStreamTokenProvider`.

## Browser bundle (no-build HTML)

For a no-build HTML app or a conversation widget, load the prebuilt bundle from the host instead of npm-importing. The host injects pod context as `window.__LEMMA_CONFIG__` (podId / apiUrl / authUrl), so the client takes no arguments:

```html
<script src="https://api.lemma.work/public/sdk/lemma-client.js"></script>
<script>
  const client = new window.LemmaClient.LemmaClient(); // reads window.__LEMMA_CONFIG__
  const state = await client.initialize();
  if (state.status !== "authenticated") client.auth.redirectToAuth();
  const records = (await client.records.list("tickets", { limit: 50 })).items;
</script>
```

The bundle exposes the same client and namespaces as the npm package (a legacy `window.Lemma` alias also points at it). `import.meta.env`/`VITE_LEMMA_*` are only for the React/Vite path; the browser bundle resolves config from `window.__LEMMA_CONFIG__`.

### Agent web components (`lemma-ui.js`)

For a drop-in agent UI in a no-build app, load the **opt-in** UI bundle *after* the client bundle. It registers two framework-agnostic custom elements that drive the same agent core, reuse `window.LemmaClient`, and read `window.__LEMMA_CONFIG__`:

```html
<script src="https://api.lemma.work/public/sdk/lemma-client.js"></script>
<script src="https://api.lemma.work/public/sdk/lemma-ui.js"></script>

<lemma-agent-task agent="triage" input='{"id":"123"}' auto-run></lemma-agent-task>
<lemma-agent-thread agent="support" style="height:480px"></lemma-agent-thread>
```

- `<lemma-agent-task>` — `agent`, `pod?`, `input` (string/JSON), `auto-run`, `parse-output` (default true); method `run(input?)`; emits a `lemma-output` event (`detail: { output, text }`) on completion.
- `<lemma-agent-thread>` — `agent`, `pod?`, `conversation-id?`; method `send(text)`.
- Shadow DOM, themed by CSS custom properties (`--lemma-bg`, `--lemma-surface`, `--lemma-accent`, `--lemma-radius`, `--lemma-font`, …) and `::part(...)`.

In a Vite/React app, import the elements instead of the bundle: `import { defineLemmaElements } from "lemma-sdk/elements"; defineLemmaElements();` (or `import "lemma-sdk/ui-bundle"`). The bundle is kept separate from `lemma-client.js` so apps that don't use the components don't pay for it. Workflow ships no element by design — render its form with `useWorkflowForm` / `<WorkflowForm>`.

Org and user surfaces include `users`, `organizations`, `pods`, `podMembers`, `podJoinRequests`, and `podSurfaces`.

## React Package

Install React if your app needs hooks:

```bash
npm install react react-dom
```

`lemma-sdk/react` is headless-first. It also exports a polished app-entry family—`AuthGuard`, `AppLoader`, `AppSignIn`, and `AppAccess`—with self-contained styles. Larger stock application surfaces remain installable registry blocks.

```tsx
import {
  AppLoader,
  AuthGuard,
  useConversationMessages,
  useConversations,
  useRecordForm,
  useRecords,
  useWorkflowRun,
} from "lemma-sdk/react";
```

### Hook Matrix

| Area | Hooks | Stability | Use when |
| --- | --- | --- | --- |
| Auth | `AuthGuard`, `AppLoader`, `AppSignIn`, `AppAccess`, `useAuth`, `useCurrentUser`, `usePodAccess` | Stable | Gate an app, show a consistent app entry experience, read signed-in user state, or request pod access. |
| Tables | `useTables`, `useRecords`, `useRecord`, `useJoinedRecords`, `useRelatedRecords`, `useReverseRelatedRecords`, `useReferencingRecords`, `useDatastoreQuery`, `useRecordAggregates` | Stable | Build custom table browsers, details views, related-record views, raw SQL-backed reads, and chart/KPI queries. |
| Record mutations | `useCreateRecord`, `useUpdateRecord`, `useDeleteRecord`, `useBulkRecords` | Stable | Create, update, delete, or bulk-delete rows from headless UI. Function-backed mutations via `createVia`/`updateVia` options. |
| Record forms | `useRecordSchema`, `useRecordForm`, `useForeignKeyOptions`, `useSchemaForm` | Stable | Render schema-driven record forms, enum fields, and foreign-key selectors. `useRecordForm` is the canonical table-bound form hook; `useSchemaForm` remains available for raw JSON-schema flows. |
| Files | `useFiles`, `useFile`, `useUploadFile`, `useUpdateFile`, `useDeleteFile`, `useCreateFolder`, `useFileSearch`, `useFileTree`, `useFilePreview`, `useGlobalSearch` | Stable | Browse private or pod folders, mutate file state, search indexed files, load directory trees, preview content, and compose multi-source app search. |
| Conversations | `useConversations`, `useConversationMessages`, `useAssistantSession`, `useAssistantRuntime`, `useAssistantController` | Stable except controller/runtime | Build custom chat, conversation lists, streaming output, and final-answer views for default assistant conversations or named agents. |
| Agents | `useAgentInputSchema` | Stable | Inspect structured input/output schemas for named agents. Agent execution goes through conversations. |
| Agent & form presets | `useAgentTask` / `AgentTask`, `AgentThread`, `useWorkflowForm` / `WorkflowForm` | Stable | Headless, render-prop presets over the core: `useAgentTask`/`<AgentTask>` for a one-shot run (working → schema-parsed output), `<AgentThread>` for a full multi-turn chat you style yourself, `useWorkflowForm`/`<WorkflowForm>` to bind a run parked on a HUMAN/FORM wait to its fields + submit (pair with `useWorkflowResume`). |
| Workflows | `useWorkflowStart`, `useWorkflowRun`, `useWorkflowRuns`, `useWorkflowRunWaitAssignments`, `useWorkflowResume` | Stable | Start, poll, resume, cancel, retry, inspect workflow runs, and show human form waits assigned to the current pod member. |
| Schedules | `useSchedules`, `useCreateSchedule`, `useUpdateSchedule`, `useDeleteSchedule` | Stable | Schedule workflows or agents on time, webhook, datastore, or application-trigger events. |
| Workflow compatibility | `useFlowSession`, `useFlowRunHistory` | Deprecated naming | Kept for existing callers; prefer workflow-named hooks for new code. |
| Functions | `useFunctionRun`, `useFunctionRuns`, `useFunctionSession` | Stable except raw session | Run functions, poll function runs, and list function history. |
| Members and org | `useMembers`, `useAddPodMember`, `useUpdatePodMemberRole`, `useRemovePodMember`, `useOrganizationMembers` | Stable | Read pod and organization members, add existing org members into a pod, look up pod members by user id or email, update pod-member roles, and remove pod access. The current checked-in client does not yet expose direct email-to-pod invites. |

### Generated CRUD hooks (cache-correct)

`lemma-sdk/react` also ships **generated** TanStack-Query hooks for plain CRUD on the
pod-scoped resources `record`, `agent`, `table`, `schedule`, `function`, and `workflow`,
plus the app resource exposed as `desk` — `useRecordList` / `useRecordGet` / `useRecordCreate` / `useRecordUpdate` /
`useDeleteRecord` … (`use<Resource><Verb>` per resource). They are generated from the
backend's `x-lemma` route metadata, so a mutation **auto-invalidates the matching list
queries** — no manual refetch. They require a `@tanstack/react-query` `QueryClientProvider`
at the app root (and it as an optional peer dependency).

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRecordList, useRecordCreate } from "lemma-sdk/react";

const queryClient = new QueryClient();
// <QueryClientProvider client={queryClient}> ... </QueryClientProvider>

const recordsQuery = useRecordList(client, podId, "tickets");      // cached read
const createRecord = useRecordCreate(client, podId);               // write
createRecord.mutate({ tableName: "tickets", payload: { title: "New" } });
// recordsQuery refreshes automatically on success.
```

Prefer these for straight CRUD; use the hand-written hooks above (`useRecords` with
`loadMore`, `useRecordForm`, streaming/session hooks) when you need richer ergonomics.

### Headless Helpers

Alongside hooks, `lemma-sdk` exports shared headless helpers for common app logic:

- record display helpers: `formatRecordDisplayValue`, `humanizeRecordFieldName`, `detectRecordStatusColumn`
- form/schema helpers: `buildRecordSchemaFields`, `buildSchemaFormFields`

### Common Hook Shapes

For business-facing examples and a decision guide mapping "I want to..." to the right hook, see [docs/hooks-guide.md](docs/hooks-guide.md).

List hooks generally expose:

- `items` named for the resource, such as `records`, `runs`, or `members`
- `isLoading`
- `error`
- `nextPageToken`
- `refresh(...)`
- `loadMore(...)` where pagination is useful

Run hooks generally expose:

- `run`
- `status`
- `isPolling`, `isStreaming`, or `isRunning`
- `output`
- `finalOutput`
- `start(...)`
- `refresh(...)`
- follow-up helpers such as `resume(...)`, `submitInput(...)`, `cancel(...)`, or `retry(...)`

### Headless Examples

Records:

```tsx
import { LemmaClient } from "lemma-sdk";
import { useRecords } from "lemma-sdk/react";

const client = new LemmaClient({ podId: "<pod-id>" });

function TicketList() {
  const tickets = useRecords({
    client,
    tableName: "tickets",
    limit: 25,
    sortBy: "created_at",
    order: "desc",
  });

  if (tickets.error) return <p>{tickets.error.message}</p>;

  return (
    <ul>
      {tickets.records.map((ticket) => (
        <li key={String(ticket.id)}>{String(ticket.title ?? ticket.id)}</li>
      ))}
    </ul>
  );
}
```

Agent conversation final output:

```tsx
import { useConversationMessages, useConversations } from "lemma-sdk/react";

function SupportThread({ client }: { client: LemmaClient }) {
  const conversations = useConversations({
    client,
    agentName: "support_agent",
  });

  const messages = useConversationMessages({
    client,
    conversationId: conversations.selectedConversationId,
    autoResume: true,
  });

  return (
    <div>
      {conversations.conversations.map((conversation) => (
        <button
          key={conversation.id}
          onClick={() => conversations.selectConversation(conversation.id)}
        >
          {conversation.title || "Untitled conversation"}
        </button>
      ))}
      <pre>{messages.finalOutputText || messages.outputText || "Select a conversation."}</pre>
    </div>
  );
}
```

Loading conversation history does not select a conversation. Use
`initialConversationId`, `selectConversation`, or `selectLatestConversation`
when the UI explicitly opens one. `useAssistantController` follows the same
rule through `openedConversationId`, `openConversation`, and
`closeConversation`; its active/selection fields remain compatibility aliases.

#### Raw conversation message shapes

`useConversationMessages` derives `finalOutputText` for you. If you read messages
directly instead — `client.conversations.*`, the browser SDK, or the HTTP API
(e.g. an HTML app or a non-React poller) — you get the raw turn stream, and
picking the answer out of it is easy to get wrong. A single assistant turn emits
**multiple messages, all with `role: "assistant"`**, distinguished by `kind`:

| `kind` | meaning |
|---|---|
| `thinking` | the model's reasoning — **not** the answer; body in `text` |
| `tool_call` | a tool invocation (`tool_name`, `tool_args`) |
| `tool_return` | a tool result (`tool_name`, `tool_result`) |
| `notification` | status/system note; body in `text` |
| `text` | model-authored prose; body in `text` |

The user-visible **final answer is the `text` message with `metadata.is_final_answer === true`**, and its content is in the `text` field (not `content`). Don't just match the first/last `role: "assistant"` message — that often returns a `thinking` or `tool_call` fragment.

```ts
const { items } = await client.conversations.messages.list(conversationId);
const finalAnswer = [...items]
  .reverse()
  .find((m) => m.kind === "text" && m.metadata?.is_final_answer === true);
const answer = finalAnswer?.text ?? "";
```

Start an agent conversation:

```tsx
import { useConversationMessages } from "lemma-sdk/react";

function AgentButton({ client }: { client: LemmaClient }) {
  const conversation = useConversationMessages({
    client,
    agentName: "triage_agent",
    autoResume: true,
  });

  return (
    <button
      disabled={conversation.isStreaming}
      onClick={() => {
        void (async () => {
          const thread = await conversation.createConversation({
            title: "Triage ticket ticket_123",
            instructions: "Triage the ticket, propose the next owner, and explain the confidence level.",
            setActive: true,
          });
          await conversation.sendMessage(JSON.stringify({
            ticket_id: "ticket_123",
            prompt: "Triage this ticket.",
          }), {
            conversationId: thread.id,
            metadata: { source: "support_queue", ticket_id: "ticket_123" },
          });
        })();
      }}
    >
      {conversation.status ?? "Message agent"}
    </button>
  );
}
```

Workflow run:

```tsx
import { useWorkflowRun } from "lemma-sdk/react";

function WorkflowButton({ client }: { client: LemmaClient }) {
  const workflow = useWorkflowRun({
    client,
    workflowName: "approve_ticket",
  });

  return (
    <button
      disabled={workflow.isPolling}
      onClick={() => {
        void workflow.start({ ticket_id: "ticket_123" });
      }}
    >
      {workflow.status ?? "Start workflow"}
    </button>
  );
}
```

## Registry

The registry is optional UI scaffolding for teams that want stock Lemma blocks on top of the headless SDK.

After running `npx lemma-sdk init-shadcn`, install blocks like:

```bash
npx shadcn@latest add @lemma/lemma-records-view
npx shadcn@latest add @lemma/lemma-detail-panel
npx shadcn@latest add @lemma/lemma-record-form
npx shadcn@latest add @lemma/lemma-global-search
npx shadcn@latest add @lemma/lemma-file-browser
npx shadcn@latest add @lemma/lemma-document-workspace
npx shadcn@latest add @lemma/lemma-comments
npx shadcn@latest add @lemma/lemma-assistant-experience
```

The registry currently ships 19 canonical blocks plus the shared `lemma-ui` primitive bundle:

| Area | Items |
| --- | --- |
| Core operator blocks | `lemma-records-view`, `lemma-detail-panel`, `lemma-record-form`, `lemma-status-flow` |
| Search, files, and pages | `lemma-global-search`, `lemma-breadcrumbs`, `lemma-file-browser`, `lemma-markdown-editor`, `lemma-page-tree`, `lemma-document-workspace` |
| Collaboration and analytics | `lemma-comments`, `lemma-activity-feed`, `lemma-insights`, `lemma-action-surface`, `lemma-workflow-runner` |
| Agent and shell | `lemma-assistant-experience`, `lemma-members`, `lemma-notification-bell`, `lemma-user-menu` |

Registry blocks now install against a shared `lemma-ui` primitive layer that ships with this registry. Consumers no longer need a pre-existing app-local `@/components/ui/*` shadcn tree just to use Lemma blocks.

The registry is currently served from jsDelivr against this public repo:

- registry root: `https://cdn.jsdelivr.net/gh/lemma-work/lemma-platform@main/lemma-typescript/public/r/registry.json`
- item shape: `https://cdn.jsdelivr.net/gh/lemma-work/lemma-platform@main/lemma-typescript/public/r/{name}.json`

For more stable installs, pin the registry URL to a tag or commit SHA instead of `@main`.

Blocks that install a CSS file, such as records view, should be imported by your app's global stylesheet:

```css
@import "@/styles/lemma-records-view.css";
```

Core registry blocks:

- `lemma-records-view` for a lean records browser by default, with explicit workspace presets for grid, list, grouped, kanban, and linear operator flows
- `lemma-detail-panel` for standalone record detail rendering with shared records-detail internals
- `lemma-record-form` for schema-aware create and edit flows with searchable foreign-key controls
- `lemma-status-flow` for interactive status transitions and lifecycle display
- `lemma-global-search` for a stock command-bar style omnibox
- `lemma-file-browser` and `lemma-document-workspace` for file browsing, editing, preview, and document-native workflows
- `lemma-comments`, `lemma-activity-feed`, and `lemma-insights` for collaboration and reporting
- `lemma-action-surface` and `lemma-workflow-runner` for long-running actions and workflow history
- `lemma-assistant-experience`, `lemma-members`, `lemma-notification-bell`, and `lemma-user-menu` for agent conversation and shell surfaces

### Block Defaults

The registry now treats generic blocks as read-first and low-chrome by default.

- base blocks should render useful data without assuming a full workspace shell
- presets are the place for opinionated operator UX such as inline detail, multi-view boards, and heavier toolbars
- `appearance`, `density`, and `radius` remain available as local override props on major blocks, but they are optional; prefer setting visual defaults in your app shell or wrapper components instead of passing them everywhere

`lemma-records-view` now defaults to:

- one explicit view instead of an inferred multi-view workspace
- no search bar unless you opt in through `chrome.search` or pass search config
- no filter launcher unless you opt in through `chrome.filters` or provide default filters
- no create button unless you opt in through `chrome.create` or pass create config
- no row-selection chrome unless you opt in through `chrome.selection` or provide bulk actions
- `detailMode="sheet"` for the base block, with inline detail reserved for explicit presets or explicit props
- no schema-name heuristics that silently promote a table into `kanban` or `linear`

For records workspaces, the split is:

- use the base block for simple table/list browsing
- pass `availableViews` only when you want a view switcher
- pass `chrome={{ search: true, filters: true, create: true, viewSwitcher: true, selection: true }}` when you want workspace controls on the base block
- use `preset="triage" | "issues" | "crm" | "docs"` when you want a stock operator workspace

`lemma-members` now defaults to a read-only membership list. Add management behavior explicitly with `allowAdd`, `allowRoleEdit`, and `allowRemove`.

`lemma-global-search` supports:

- configured `tables[]` with `searchFields`, `displayField`, `subtitleField`, `href`, `onSelect`, and `openMode`
- optional file search with `searchMethod`, `href`, `onSelect`, and `openMode`
- progressive table/file result groups, smooth loading/error source states, hidden empty sources, keyboard navigation, and built-in `cmd/ctrl+k` handling
- `minQueryLength`, `debounceMs`, `appearance`, `density`, trigger label, and placeholder customization
- agent handoff by `agentName`, with optional query/results message shaping and conversation routing

Document blocks support:

- Notion/Coda-style block documents through `lemma-document-workspace`, with ProseKit JSON content, page/modal modes, title and summary chrome, file/reference/agent blocks, save state, metadata, backlinks, and agent-context rails
- pod-file-native creation, reading, editing, and preview through one workspace, with folder targeting, title/summary setup, pod-file metadata, and `mode="page" | "modal"`
- non-document file previews through the same workspace, including image, PDF, text, markdown, converted HTML, and download fallback behavior
- records and attachments should pass pod file paths into `lemma-document-workspace`; records should not own document bodies directly

People blocks support:

- `LemmaMemberChip`, `LemmaAvatarGroup`, `LemmaMemberSelect`, and `LemmaUserField`
- a stock `LemmaMembers` surface that is read-only by default, and upgrades into a membership admin workspace only when `allowAdd`, `allowRoleEdit`, or `allowRemove` are enabled
- pod member labels for owner, creator, assignee, participant, and author fields
- searchable member picking backed by `useMembers`

Workflow primitives support:

- lifecycle/status rendering and transitions through `lemma-status-flow`
- direct, function-backed, workflow-backed, and agent-backed launches through `lemma-action-surface`
- inline, row, and panel presentation modes for long-running actions with inspectable progress
- native workflow run inspection through `lemma-workflow-runner`

Navigation and file blocks support:

- route, record, and file-path breadcrumb builders through `lemma-breadcrumbs`
- native pod-file hierarchy navigation through `lemma-page-tree`
- private/pod datastore navigation, upload, rename, move, folder creation, picker mode, publish-to-pod, and delete handling through `lemma-file-browser`
- write, preview, and split modes through `lemma-markdown-editor`

Collaboration and analytics blocks support:

- record-scoped discussion through `lemma-comments`
- unified audit and history timelines through `lemma-activity-feed`
- dashboard-style stat cards and charts through `lemma-insights`

Agent conversation blocks support:

- agent-name-first creation through `agentName`; conversation lists span the pod by default, accept an agent name to filter, and accept an explicit `null` for only the default pod assistant
- shared `appearance` and `density` controls on the conversation experience surface
- `chromeStyle`, `statusPlacement`, `radius`, model picker, conversation list, and render overrides for deeper customization
- bounded default heights for `page` and `side-panel` modes so the message viewport scrolls instead of stretching with content; pass `className="h-full min-h-0"` inside an explicit-height parent when you want a fill-layout agent conversation like inbox CRM

```tsx
import { LemmaRecordsView } from "@/components/lemma/lemma-records-view";
import { LemmaAssistantExperience } from "@/components/lemma/assistant/assistant-experience";
import { LemmaActionSurface } from "@/components/lemma/lemma-action-surface";
import { LemmaGlobalSearch } from "@/components/lemma/lemma-global-search";

<LemmaRecordsView
  client={client}
  podId={podId}
  tableName="tickets"
/>;

<LemmaRecordsView
  client={client}
  podId={podId}
  tableName="deals"
  defaultView="list"
  availableViews={["list", "grid"]}
  chrome={{ search: true, filters: true, create: true, viewSwitcher: true, selection: true }}
  hiddenFields={["id", "created_at", "updated_at"]}
  foreignKeyLabels={{ company_id: "name" }}
  onCreateOptions={{
    submitVia: "function",
    submitFunctionName: "create-deal",
  }}
/>;

<LemmaRecordsView
  client={client}
  podId={podId}
  tableName="deals"
  preset="crm"
/>;

<LemmaGlobalSearch
  client={client}
  podId={podId}
  tables={[
    {
      tableName: "deals",
      label: "Deals",
      searchFields: ["name", "status", "source"],
      displayField: "name",
      subtitleField: "status",
      href: (record) => `/deals?record=${record.id}`,
    },
  ]}
  files={{ enabled: true, openMode: "new-tab" }}
  agent={{
    agentName: "sales-copilot",
    label: "Ask CRM",
    resultLimit: 8,
  }}
  minQueryLength={3}
  debounceMs={450}
/>;

<LemmaActionSurface
  client={client}
  podId={podId}
  label="Run triage"
  kind="workflow"
  workflowName="triage-lead"
/>;

<LemmaAssistantExperience
  client={client}
  agentName="sales-copilot"
/>;
```

## Auth

The SDK uses cookie or session auth by default.

Useful helpers:

- `buildAuthUrl(...)`
- `buildFederatedLogoutUrl(...)`
- `resolveSafeRedirectUri(...)`
- `setTestingToken(...)`
- `clearTestingToken()`

`resolveSafeRedirectUri(...)` allows same-origin redirects by default and falls
back to `/` for invalid, auth-loop, or untrusted cross-origin targets. Pass
`allowedOrigins`, `allowedOriginSuffixes` (for example, an app domain
suffix), or `allowLoopback` for explicit local callback flows.

When `client.podId` is set and the signed-in user is not a pod member, `AuthGuard` can render the request-access flow and create or show pod join requests.

`usePodAccess` exposes the same membership/request-access state as a hook for custom UI.

## Migration Notes

From `0.2.30` onward:

- `lemma-sdk/react` should be treated as hooks plus auth primitives.
- `AuthGuard` remains in `lemma-sdk/react`.
- Stock UI should be installed from the shadcn registry.
- Conversation UI source and CSS are no longer part of the React SDK internals.
- `react-markdown` and `remark-gfm` are registry-block dependencies for conversation UI, not core SDK dependencies.
- New workflow code should prefer `useWorkflowRun`, `useWorkflowRuns`, and `useWorkflowResume` over the older `flow`-named hooks.

From `0.2.37` onward:

- Agent APIs are the runtime abstraction. `client.assistants` is removed; use `client.agents` for definitions and `client.conversations` for turns/messages.
- Conversations are pod-scoped under the agent conversation API; message sends stream conversation events until final output.
- Files are a path-based tree, not a namespace enum. `/me` is each user's private per-user tree (only the owner sees their `/me` files); every other path is pod-shared, and folder grants cascade to all descendants. Scope reads and uploads by `directoryPath`/`path` rather than a namespace. Files are auto-indexed searchable documents (built-in RAG), and file search supports directory-scoped queries via `scope_path` + `scope_mode` (`SUBTREE` for a folder and its descendants, `DIRECT` for immediate children).

From `0.2.39` onward:

- Workflow install/trigger APIs moved to `client.schedules`; create schedules with `workflow_name` or `agent_name` plus `schedule_type`.
- Human form nodes can assign waits to a pod member id. Use `useWorkflowRunWaitAssignments` or `client.workflows.runs.waitingAssignedToMe()` to show work waiting for the signed-in member.
- Pod member role updates, lookup, and removal use `pod_member_id`. Use `client.podMembers.lookupByUserId(...)` or `lookupByEmail(...)` when starting from user identity.

## Local Development

From the root of this repository:

```bash
npm install
npm run build
npm run registry:build
```

To build the single local sandbox app:

```bash
cd examples/inbox-crm
npm run build
```

`examples/inbox-crm` is the only kept example app in this repo. It is a local sandbox for visualizing the current direction, not a promise that every copied component inside it is a published registry block.

This repo includes:

- `registry.json` for registry source definitions
- `public/r` for the generated flat registry output
- `.github/workflows/deploy-registry-pages.yml` for GitHub Pages deployment
- `.github/workflows/publish-npm.yml` for npm publishing
