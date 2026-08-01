# Loading is a fill, not a screen

**Status:** Scoped, not started · **Surface area:** `lemma-frontend` only — no backend changes

## The change in one sentence

A region declares its settled layout once, and loading / empty / loaded become
three *fills of the same box* instead of three different boxes that replace each
other.

## Why the platform feels jerky

Every screen currently answers "what do I show while this loads" on its own, and
the answer is a different shape from the answer to "what do I show when it's
empty", which is a different shape again from the settled layout. So a single
page load is two or three layout replacements, not one paint.

Four things stack up:

1. **Five competing loading idioms.** Bare `Loader2 + animate-spin` (97 sites in
   53 files), `animate-pulse` text (34 sites in 15 files), hand-rolled
   `lemma-skeleton` blocks (40 sites), the brand loaders in
   [loader.tsx](../../lemma-frontend/components/brand/loader.tsx) (21 importers),
   and "nothing at all". Which one a screen uses is historical accident.
2. **The loading shape does not match the settled shape.** Skeletons are written
   by eye — `h-48` cards, `w-24` pills — not derived from the components they
   stand in for, so data arrival re-flows the page even when the skeleton was
   "right".
3. **Empty is a third shape.** [`EmptyState`](../../lemma-frontend/components/shared/empty-state.tsx)
   renders a dashed-border panel, loaded renders solid cards, the skeleton
   renders a solid panel. The container's border style changes as data arrives.
4. **Nothing is debounced or floored.** There is no delay before a skeleton
   appears and no minimum time it stays, so a 60 ms cached response still flashes
   one frame of skeleton and a 400 ms response shows a skeleton that vanishes
   before it is readable. The only exception in the codebase is
   `ThinkingIndicator`'s 350 ms delay — and that one causes its own jump (below).

## The specific breakages

### The shell blanks on every pod route entry

[`pod/[id]/layout.tsx:525`](../../lemma-frontend/app/pod/[id]/layout.tsx) and
[`:1035`](../../lemma-frontend/app/pod/[id]/layout.tsx) both return a full-screen
`PageLoader` — the whole chrome (sidebar, topbar, content) collapses to a
centered wordmark and then repaints in full. This is the single largest jerk in
the product, and it fires on pod access checks that are usually sub-100 ms.

Worse, the topbar is *declared by the page*, not the shell.
[`ResourceHeader`](../../lemma-frontend/components/pod/resource-layout.tsx#L124-L140)
registers title/back/actions in a `useLayoutEffect` whose cleanup is
`setTopbar({})`. On a route change the outgoing page clears the bar, and the
incoming page only sets it *after its data resolves* — so the context bar goes
blank mid-navigation and refills. (Its dependency array also includes the
`actions` JSX element, recreated every render, so `setTopbar` fires on every
render of every resource page.)

There are also **zero** `loading.tsx`, `error.tsx`, or `Suspense` boundaries in
`app/`. Nav has no instant response: Next waits for the client component to
mount, then that page's own `isLoading` fires.

### Agents list — three layouts in sequence

[`ai/page.tsx`](../../lemma-frontend/app/pod/[id]/ai/page.tsx)

| State | What renders | Height |
| --- | --- | --- |
| Loading (`:142`) | 3 pills `h-7 w-24` + 3 cards `h-48` in the grid | tall |
| Empty (`:163`) | one dashed panel, `px-5 py-10`, **no metric strip** | short |
| Loaded (`:180`) | real metric strip (natural widths) + `PodAssistantCard` prepended + N cards at natural height | medium |

A pod with one agent goes tall → short → medium. The metric strip appears,
disappears, and reappears at a different width. `PodAssistantCard` is not in the
skeleton at all, so every card in the grid shifts one slot when data lands.

### Workflows — same, plus counters that count up from zero

[`flows/page.tsx`](../../lemma-frontend/app/pod/[id]/flows/page.tsx)

Loading (`:354`) is 5 pills + 3 cards; empty (`:411`) is a dashed panel with no
tab row; loaded (`:434`) is `lemma-index-tabs` + grid — the same three-shape
problem.

On top of that, four independent queries (`useFlows`, `useFunctions`, runs,
`useWorkflowRunWaitAssignments`) resolve at four different times, and each one
re-flows the tab row. `attentionCount = loadingWaits ? 0 : …` (`:379`) renders a
literal **0** while its query is in flight, then jumps to the real number — the
UI states a fact it does not know yet.

### Table — a four-stage cascade

1. [`data/page.tsx:1257`](../../lemma-frontend/app/pod/[id]/data/page.tsx) →
   `DatastoreTableSkeleton`
2. `DatastoreTableView` mounts and renders
   [`DatastoreTableSkeleton` again](../../lemma-frontend/components/data/datastore-table-view.tsx#L290)
   — a **fresh mount**, so the breathe animation restarts from zero. Visually a
   stutter with no state change behind it.
3. Table chrome paints; the body is one `animate-pulse` row reading
   "Loading records..." ([`:475`](../../lemma-frontend/components/data/datastore-table-view.tsx#L475)).
   The body collapses from 8 skeleton rows to 1 text row — the viewport height
   drops hard.
4. Records arrive, rows expand again, the footer count changes.

The files pane has its own version: spinner (`:1222`) → empty state (`:1228`) →
list (`:1237`), three different heights.

### Agent detail — no shell at all while loading

[`agents/[agentId]/page.tsx:197`](../../lemma-frontend/app/pod/[id]/agents/[agentId]/page.tsx)
returns a bare centered `Loader2` — no header, no cards, no dock — and then the
entire two-pane layout snaps in. Because no `ResourceHeader` is declared on that
branch, the pod topbar is blank for the whole load.

The page also measures itself with a callback ref (`:109-120`) that can only
attach once loading ends, so the split resolves its stacked/side-by-side decision
one frame *after* first paint.

### Messages

- **`ThinkingIndicator`** ([`assistant-parts.tsx:127`](../../lemma-frontend/components/lemma/assistant/assistant-parts.tsx#L127-L134))
  returns `null` for 350 ms, then appears. It occupies no space until it does, so
  the transcript jumps a line at 350 ms and jumps again when the first token
  replaces it.
- **Initial load** ([`assistant-experience-conversation.tsx:202`](../../lemma-frontend/components/lemma/assistant/assistant-experience-conversation.tsx#L202-L206))
  is a centered `InlineLoader` at `py-10`, which is then replaced by the
  transcript — a full-height swap inside a scroll container that is
  simultaneously being pinned to the bottom.
- **The whole transcript animates on every switch.** `animate-in fade-in
  slide-in-from-bottom-1` is on the container (`:225`), keyed by conversation id,
  so switching conversations slides the entire history rather than the new rows.
- **The conversation route** ([`conversations/[conversationId]/page.tsx:315`](../../lemma-frontend/app/pod/[id]/conversations/[conversationId]/page.tsx))
  shows a full-pane "Loading conversation" loader and then swaps in the entire
  embedded assistant — header, transcript, and composer all appear at once.
- **The conversation list** ([`pod-conversation-list.tsx:61`](../../lemma-frontend/components/conversations/pod-conversation-list.tsx#L61-L72))
  goes spinner + text → `InlineEmptyState` → list: three heights in a sidebar
  whose rows should be one dense line each.

### The query layer makes it worse

[`providers.tsx:17`](../../lemma-frontend/app/providers.tsx) sets `staleTime: 60s`
and `refetchOnWindowFocus: false` — both correct — but no
`placeholderData: keepPreviousData`. So any query whose **key** changes (switch
table, open folder, change agent filter, pick a conversation) drops to
`isLoading`, unmounts the content, and flashes the full skeleton, even when the
new data is 150 ms away and the layout is identical.

### Six empty-state components, no rule

`EmptyState` (three variants at `py-5` / `py-10` / `py-24`), `InlineEmptyState`,
`QuietEmptyState`, `SidebarEmptyState`, `RecoveryState`, plus the assistant's own
`EmptyState`. 26 files import from
[empty-state.tsx](../../lemma-frontend/components/shared/empty-state.tsx), and
single screens mix three of them — the workflows page uses `panel`, `compact`,
and `QuietEmptyState` in one render.

## The rule

> **One shape, three fills.** A region's box — its height floor, borders, padding,
> and grid — is declared once and owned by the settled layout. Loading, empty, and
> loaded change what is *inside* that box, never the box.

Corollaries:

- A skeleton is derived from the component it replaces, not drawn by eye. The
  model already exists: [`DatastoreTableSkeleton`](../../lemma-frontend/components/data/datastore-table-skeleton.tsx)
  reuses `.data-table-workbench` so the frame is literally the same CSS.
- Chrome never waits on content. Header, tabs, and counters render immediately;
  unknown counts render as `—`, never `0`.
- One boundary per region, not one per query. Four queries feeding one page
  settle as one state.
- `isPending` (no data yet) → skeleton. `isFetching` (refresh, key change) →
  keep the old content, dim it. Never a skeleton on top of data you already have.
- Nothing appears for under 400 ms: 120 ms delay before a skeleton shows, 400 ms
  minimum once it does.

## Work

### P0 — primitives and shell (fixes the global jerk)

| Item | Change |
| --- | --- |
| `components/shared/loading/skeleton.tsx` | One `<Skeleton>` atom. Replaces the 40 hand-written `lemma-skeleton` divs. |
| `components/shared/loading/async-region.tsx` | `<AsyncRegion status skeleton empty>` — owns the 120 ms delay, 400 ms floor, min-height lock, and crossfade. The only place any screen branches on load state. |
| [loader.tsx](../../lemma-frontend/components/brand/loader.tsx) | Delete `LoadingState` and `LoadingSkeleton` — generic centered boxes that match no real screen. Keep `StepLoader` / `InlineLoader` as motion atoms for buttons and inline text; keep `PageLoader` for cold boot at `/` only. |
| [providers.tsx](../../lemma-frontend/app/providers.tsx) | Add `placeholderData: keepPreviousData` for list queries. |
| `app/pod/[id]/loading.tsx` (+ peers) | Route-level boundaries so nav responds on click. |
| [pod/[id]/layout.tsx](../../lemma-frontend/app/pod/[id]/layout.tsx) `:525`, `:1035` | Replace both full-screen `PageLoader` returns with the real shell — sidebar and topbar frames render, only the content pane skeletonizes. |
| [resource-layout.tsx](../../lemma-frontend/components/pod/resource-layout.tsx) | `ResourceHeader` accepts a title before data resolves; memoize so `actions` stops re-firing `setTopbar` every render; stop clearing to `{}` on unmount mid-navigation. |

### P1 — the named screens

| Screen | Change |
| --- | --- |
| Agents list | Metric strip renders in all three states (counts `—` while loading). Card skeleton built from the `AgentProfileCard` frame, with the `PodAssistantCard` slot reserved. Empty state renders inside the grid, so strip and grid never disappear. |
| Workflows | Same treatment. Counts resolve to `—`, never `0`. One settle gate for the tab row instead of four re-flows. |
| Table | Delete the duplicate skeleton at `data/page.tsx:1257` — the view owns it. Record loading becomes fixed-count row skeletons inside the real `<tbody>`, so the frame height never changes. Remove "Loading records...". Files pane gets the same row-skeleton treatment. |
| Agent detail | Replace the bare spinner with the settled shell: header declared up front, identity/wiring/instructions as skeletons in place, dock closed. Attach the ResizeObserver to the skeleton shell so the split decides before first paint. |

### P1 — messages

- `ThinkingIndicator` reserves its line height from t=0 (invisible, not absent);
  the 350 ms reveal fades in without moving the transcript.
- Initial load renders 2–3 message-shaped skeletons, bottom-anchored, so the
  first real message replaces a same-height block.
- Move `animate-in` off the transcript container onto newly appended rows only.
- Conversation route renders the assistant shell (header + composer) immediately
  and skeletonizes only the transcript.
- Conversation list: one dense skeleton line per row, matching the settled row.

### P2 — sweep

Replace the 97 `animate-spin` and 34 `animate-pulse` sites with `Button loading`
(already supported), `InlineLoader`, or `Skeleton`. Add an ESLint rule banning
both class names outside `components/shared/loading/`.

### P3 — empty states

Collapse six components to `EmptyState` (one axis: `inline | region | page`) plus
`QuietEmptyState`. Drop the dashed border on region empties so the container's
border does not change when data arrives.

## How we will know it worked

- No screen changes height more than once between navigation and settled.
- The pod shell never disappears after first boot.
- Cached navigation (`staleTime` hit) shows no skeleton at all.
- One skeleton animation per load — no restarts from remounting.
- Counters never display a number they have not fetched.
