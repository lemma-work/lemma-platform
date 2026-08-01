# Loading is a fill, not a screen

**Status:** Implemented (P0–P3) · **Surface area:** `lemma-frontend` only — no backend changes

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

### Pod home — four looks for one page

The worst of them, because the first one blanked the page for data it never read:

1. `PodPage` gated the whole route on `usePod(podId)` and rendered a bare centred
   loader. Nothing on that branch used the pod record, and the shell above had
   already resolved it — a blank screen in front of a composer that is static
   markup.
2. The composer painted, with `PodHomePanelsSkeleton` below it during a
   deliberate 600 ms defer.
3. `PodAgentWorkflowKanban` mounted and ran its *own* five queries, rendering the
   real "Activity" heading over an **empty** panel with a spinner in the status
   pill — and a pill reading "0 scheduled" before it knew.
4. Content.

On a fresh pod it was worse still: the skeleton appeared and then vanished
without becoming anything, because a fresh pod shows the starter section and no
activity region at all.

### Documents — the same cascade, three deep

Opening a document is three waits in a row, and each drew its own placeholder at
its own size:

1. The viewer's JS chunk — the `dynamic()` `loading:` fallback, at one height.
2. The file record — `DocumentViewer`'s own `isLoadingDoc` branch, a **fresh
   mount** at a different height, so the shimmer restarted too.
3. The rendered preview — `isLoadingPreview`, a third shape, and the header band
   appeared at this point so everything below it shifted down.

The docs list had the shorter version of it: an `h-28` centred "Loading docs"
caption replaced by a list of `h-11` rows.

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
| A `loading.tsx` per route | Route-level boundaries so nav responds on click — **one per page, not one for the segment**. See below. |
| [pod/[id]/layout.tsx](../../lemma-frontend/app/pod/[id]/layout.tsx) `:525`, `:1035` | Replace both full-screen `PageLoader` returns with the real shell — sidebar and topbar frames render, only the content pane skeletonizes. |
| [resource-layout.tsx](../../lemma-frontend/components/pod/resource-layout.tsx) | `ResourceHeader` accepts a title before data resolves; memoize so `actions` stops re-firing `setTopbar` every render; stop clearing to `{}` on unmount mid-navigation. |

### P1 — the named screens

| Screen | Change |
| --- | --- |
| Agents list | Metric strip renders in all three states (counts `—` while loading). Card skeleton built from the `AgentProfileCard` frame, with the `PodAssistantCard` slot reserved. Empty state renders inside the grid, so strip and grid never disappear. |
| Workflows | Same treatment. Counts resolve to `—`, never `0`. One settle gate for the tab row instead of four re-flows. |
| Table | Delete the duplicate skeleton at `data/page.tsx:1257` — the view owns it. Record loading becomes fixed-count row skeletons inside the real `<tbody>`, so the frame height never changes. Remove "Loading records...". Files pane gets the same row-skeleton treatment. |
| Agent detail | Replace the bare spinner with the settled shell: header declared up front, identity/wiring/instructions as skeletons in place, dock closed. Attach the ResizeObserver to the skeleton shell so the split decides before first paint. |
| Documents | One `DocumentSkeleton`, built from the viewer's own shell, serves the chunk wait and the record wait; once the record lands the real header takes over and only the body keeps `DocumentBodySkeleton`. Three waits, one visible load. The docs list waits as `h-11` rows. |
| Pod home | The `usePod` gate is deleted — the composer paints immediately. `PodHomeActivitySkeleton` is the single fill for both the defer window and the kanban's own fetch, replacing the heading-over-an-empty-panel. Nothing renders in that region until we know whether this pod has one. |

Two rules fell out of these, and they generalise:

**A sequence of waits is still one load to the reader.** Where two are
unavoidable — a code-split chunk followed by a fetch, a deferred mount followed
by that component's own queries — they must render the *same component*, or the
second reads as a new screen. Watch for it wherever `dynamic()` has a `loading:`
fallback in front of something that fetches, and wherever a parent and its child
both hold a gate over the same region.

**Don't gate a screen on data it doesn't read.** Both the pod home and the pod
shell blanked themselves waiting for a record that only decided something further
down. If removing the query would not change what renders, it must not decide
*whether* anything renders.

## Route boundaries are per page, not per segment

The first version of this work put a single `loading.tsx` at `app/pod/[id]/`
holding a card-grid skeleton. In Next's App Router that file is inherited by
every nested route, so clicking **Functions** (a list), **Settings** (a form),
the **flow editor** (a canvas), or the **pod home** (a composer) all flashed
three cards first — the same shape mismatch this document is about, promoted to
the *first* thing you see on every navigation, and applied to a dozen pages at
once.

A route boundary is the one skeleton a reader sees before anything else about the
page exists. It has to be that page's shape.

So every route declares its own, and what is shared is a vocabulary of page
*kinds* in [route-skeletons.tsx](../../lemma-frontend/components/pod/route-skeletons.tsx)
— not one skeleton stretched over all of them:

| Kind | Routes |
| --- | --- |
| `PodIndexCardsSkeleton` | agents, workflows, connectors, recipes, app pages |
| `PodIndexListSkeleton` | functions, triggers |
| `PodDetailSkeleton` | pod assistant, recipe detail |
| `PodBuilderSkeleton` | new agent / workflow / function |
| `PodEditorSkeleton` | flow editor, function editor |
| `PodSettingsSkeleton` | settings, members, usage |
| `PodConversationSkeleton` | conversations index and detail |
| `PodHomeSkeleton` | pod home — a composer, and nothing else until we know if there is an activity region |
| purpose-built | data (table workbench), files (list), agent detail, run detail |
| deliberately empty | `app/view`, `widgets/view` — the live surface is owned by the shell's keep-alive host above the router, so a skeleton here would paint over something already running |

Card indexes and lists genuinely repeat, and sharing a shape between them is
right. Forms, canvases, transcripts, and the composer home do not, and each pays
for its own. All 26 non-redirect pod routes have a boundary of their own; the 8
redirect-only routes need none, because a server redirect never renders.

The rule to keep: **a segment-level `loading.tsx` is a default that silently
applies to children you haven't thought about.** If you add one, either every
child overrides it or the segment genuinely has one shape.

### The shell must not guess the page's shape either

The same mistake had a second home. `PodShellSkeleton` and the layout's
access-check branch both drew a three-card index skeleton into the content pane,
so a cold load of a conversation URL went:

1. shell skeleton — **card grid**
2. access check — **card grid again**, a fresh mount, shimmer restarting
3. the route's own `PodConversationSkeleton` — a transcript
4. the conversation

Two loading states describing a page that was never coming, before the right one
appeared. Fixed by taking page shape away from the shell entirely:

- `PodShellSkeleton` draws the frame — nav slot, tab bar, context bar — and
  leaves the **content pane empty**. It does not know what it is about to hold.
- The access check no longer swaps `children` for a skeleton. `children` render
  while it resolves, which is what lets the route's own boundary fill the pane
  with the correct shape. Nothing leaks: the page's queries run through the same
  access hooks, and the denial branch still takes over when the answer lands.

Generalised: **only the thing that knows the shape may draw the shape.** A
layout, a shell, or a shared boundary that renders a skeleton on behalf of a page
it cannot identify will always be wrong for most of them.

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

All 97 `animate-spin` and 34 `animate-pulse` sites are gone. They sorted into
four buckets, and the split is the point — three of them were never loading at
all:

| Was | Is | Why |
| --- | --- | --- |
| A placeholder for content | `Skeleton` / a shape from `components/shared/loading` | Content is coming; show its shape |
| An action in flight | `<Button loading>` / `StepLoader` | The control owns its own busy state |
| "This is running right now" | `.lemma-live-pulse` | Liveness, not loading — a status dot outlives any fetch |
| A refresh control turning | `.lemma-spin` | An affordance; the content never left |

Two supporting fixes fell out of it. `StepLoader`'s bars were hard-coded to
`--action-primary`, so `text-current` inside a primary button did nothing —
they now take `currentColor`, with the brand colour as a zero-specificity
default. And its heights moved behind `:where()` so a caller can pass `h-3.5`
and sit flush with the icons beside it, instead of growing a status row by 4px
the moment a step starts running.

An ESLint rule (`no-restricted-syntax`, string literals *and* template strings)
now rejects both class names anywhere in the app.

### P3 — empty states

Six components collapsed to two: `EmptyState` with one axis — `inline | region |
page` — and `QuietEmptyState` for the one-line case. `InlineEmptyState`,
`SidebarEmptyState`, and `RecoveryState` are gone; `compact`/`panel` merged into
`region` and `full` became `page`.

The dashed border went with them. A region empty now takes the same solid,
quiet border as the cards it will be replaced by, so the container's outline is
the same in all three states.

## What changed on the way

Two things the audit did not predict:

- **Skeletons were nearly invisible, and then they were outlined.**
  `.lemma-skeleton` mixed `--surface-2` *toward transparent*, and on a white card
  `--surface-2` is already only a ~3% step — placeholders were faintest on
  exactly the surfaces that use them most. The fill is now tinted off
  `--text-primary`, which reads on any surface in either theme.

  That exposed a second problem the old faintness had hidden: every placeholder
  also carried a 1px border, so once the fill was visible the border read as a
  light ring around a darker shape, and a paragraph of placeholder lines became a
  stack of little boxes. **A skeleton has no border.** It stands in for a line of
  text or a filled control, and neither of those has an outline. Borders belong
  to the containers a skeleton sits inside — the card, the table frame, the tab
  strip, the shell's left edge — which are real chrome and stay put when data
  lands. That distinction is the rule: the frame is drawn because it is still
  there afterwards; the fill is not.
- **The design-audit baseline is stale, but not from this work.** The strict
  gate passes at 0 across every enforced counter. `design:audit:test` fails
  because `scripts/audit-design-system.mjs` has uncommitted changes that add a
  `multiplePrimaryButtons` counter the committed baseline predates, plus a
  `font-size` in `styles/primitives.css` from the same in-flight work.
  Regenerating the baseline would fold that unrelated drift into a snapshot, so
  it is left alone.

## How we will know it worked

- No screen changes height more than once between navigation and settled.
- The pod shell never disappears after first boot.
- Cached navigation (`staleTime` hit) shows no skeleton at all.
- One skeleton animation per load — no restarts from remounting.
- Counters never display a number they have not fetched.
