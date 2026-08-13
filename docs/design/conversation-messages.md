# A conversation is one list, loaded once

**Status:** Proposed · **Surface area:** `lemma-frontend` + `lemma-typescript` — no backend changes

## The change in one sentence

Every surface that shows conversation messages reads them from one store that
already has everything, instead of four surfaces each fetching the transcript two
or three times and patching the copies together on every render.

## Why chat feels like it is constantly reloading

The backend's message model is clean: one flat row, one `kind`
([`MessageKind`](../../lemma-backend/app/modules/agent/domain/value_objects.py#L273) —
`TEXT`, `NOTIFICATION`, `THINKING`, `TOOL_CALL`, `TOOL_RETURN`), tool fields at
the top level, no nested content object. The SDK maps that faithfully. Nothing is
wrong below the React layer.

Above it, four independent things believe they own the transcript:

| Layer | Owns | Fetches messages? |
| --- | --- | --- |
| Route (`conversations/[conversationId]/page.tsx`) | the id in the URL | no |
| `AIAssistantProvider` | `?assistantConversationId`, open/closed, load gating | **yes** |
| `useAssistantController` (SDK) | `activeConversationId`, mapped messages | **yes** |
| `useAssistantSession` (SDK) | the live stream, raw message store | **yes** |

They coordinate by writing to each other and then waiting for React to settle.
Two functions exist whose entire body is a double `requestAnimationFrame` —
[`waitForControllerReset`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L242)
and [`waitForConversationReset`](../../lemma-frontend/app/pod/[id]/conversations/[conversationId]/page.tsx#L33) —
used as a synchronisation primitive between two state machines that should have
been one. That is the shape of the whole problem.

## Every surface that renders a conversation message

Nine, across three different hydration strategies. This is the inventory the plan
has to satisfy — a fix that only lands on the main route leaves six surfaces
behind.

**Through `AIAssistantProvider` (one controller, shared):**

1. `/pod/[id]/conversations/[conversationId]` — the main chat
2. `/pod/[id]/conversations/new` — same component, different lifecycle
3. [`PodAssistantSidebar`](../../lemma-frontend/components/ai/pod-assistant.tsx#L462) — the docked side panel
4. [`ConversationPresentationStage`](../../lemma-frontend/components/pod/conversation-presentation-stage.tsx) — chat beside a presented resource

**Their own controller instance, bypassing the provider entirely:**

5. [`agent-test-panel.tsx:201`](../../lemma-frontend/components/agents/agent-test-panel.tsx#L201) — the agent page's "try it"
6. [`run-steps.tsx:76`](../../lemma-frontend/components/flows/run-detail/run-steps.tsx#L76) — an agent step inside a flow run

**A fourth path entirely:**

7. SDK [`AgentThread`](../../lemma-typescript/src/react/AgentThread.tsx) → `useConversationMessages` — what customer apps embed

**List views (titles only, but same cache):**

8. [`pod-conversation-list.tsx`](../../lemma-frontend/components/conversations/pod-conversation-list.tsx)
9. [`recent-conversations.tsx`](../../lemma-frontend/components/pod/recent-conversations.tsx)

Plus [`PodAssistant`](../../lemma-frontend/components/ai/pod-assistant.tsx#L320) — a
140-line floating variant, exported and **rendered by nothing**. Dead.

Surfaces (Slack, Telegram, WhatsApp, Teams) render server-side through
[`platforms/rendering.py`](../../lemma-backend/app/modules/agent_surfaces/platforms/rendering.py)
and are out of scope here, but they are the reason the message model is flat and
kind-discriminated. Keep it that way.

## The specific breakages

### 1. The transcript is fetched twice, and the second copy does nothing

The controller already returns fully-formed messages.
[`mapConversationMessage`](../../lemma-typescript/src/react/useAssistantController.ts#L452-L468)
passes through `metadata`, `message_metadata`, `kind`, `tool_call_id`,
`tool_name`, `tool_args`, and `tool_result`. And
[`mapConversationMessages`](../../lemma-typescript/src/react/useAssistantController.ts#L490-L505)
already merges each `TOOL_RETURN` into its originating `TOOL_CALL`, setting
`state: "result"` and the result payload, then drops the return row.

The frontend does it again anyway.
[`ai-assistant-context.tsx:420`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L420)
opens a second `useQuery` for the same 100 messages, and
[`hydrateToolReturnMessages`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L92)
walks every message to set `state` and `result` — to the values they already
hold.

It is worse than redundant. The hydration matches on `toolCallId`, and the merged
invocation still carries that id, so `changed` flips true whenever the
conversation contains **any** tool call. Every evaluation therefore returns a new
array with new object identities for every message. `displayMessages` is a
dependency of `sendMessage`, `retryFailedMessage`, `resolveUserApproval`, the
auto-navigation effect, and the `contextValue` memo — so the entire context value
changes identity, re-rendering every `useAIAssistant` consumer in the app: the pod
layout, both sidebars, the pod home page, the widgets page, the app pages route.

> **Verify before deleting.** This is read from source, not observed. One-line
> check: log `changed` inside `hydrateToolReturnMessages` against a conversation
> with tool calls. If it is always true and the invocations are already
> `state: "result"`, the layer is confirmed dead.

### 2. …and refetched again after every single run

[`ai-assistant-context.tsx:635`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L635)
refetches all 100 messages every time `controller.isLoading` goes false — i.e.
after every agent turn.
[`resolveUserApproval`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L782)
fires another. The data is already in the store; the stream delivered it.

### 3. Opening a conversation always blanks the screen first

[`useAssistantController.ts:1318`](../../lemma-typescript/src/react/useAssistantController.ts#L1318)
calls `clearRuntimeMessages()` and sets `isLoadingMessages` **unconditionally**,
before checking whether the transcript is already in hand. So every conversation
switch is content → blank → skeleton → content, warm cache or not. This is the
single most-felt reload in the product, and it fires on every click in the
history list.

### 4. Sending the first message navigates you out from under the stream

You compose at `/conversations/new`. The controller creates the conversation, the
reply starts streaming, and
[`page.tsx:216`](../../lemma-frontend/app/pod/[id]/conversations/[conversationId]/page.tsx#L216)
`router.replace`s to the real id mid-flight. The `assistantMessage` launch path
([`:223-256`](../../lemma-frontend/app/pod/[id]/conversations/[conversationId]/page.tsx#L223))
does the same thing again, and has to `closeAssistant` → `clearMessages` → await
two animation frames → `sendMessage` → `router.replace` to get through it.

### 5. Load gating is a five-variable expression nobody can hold in their head

`isControllerEnabled`, `controllerGates`, `shouldPrepareSideViewMessages`,
`activeConversationMessagesCached`, `readySideViewMessageLoadGeneration ===
sideViewMessageLoadGeneration` — resolving to
[`shouldLoadActiveConversationMessages`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L406).
The comment above it documents a bug being worked around: *"the gate dips to false
for a render, the controller drops its messages, and the side view shows a
'Loading messages' spinner before re-hydrating identical data."* That bug is
breakage #3. The gate is scaffolding around it.

### 6. Conversation identity is two-way bound to the URL

The provider writes `?assistantConversationId` in an effect
([`:500-557`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L500)) and
reads it back to call `openConversation` in another
([`:480-498`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L480)),
guarded by two refs (`suppressAssistantUrlRestoreRef`,
`skipNextAssistantUrlSyncRef`) that exist purely to break the loop. Meanwhile the
route owns the id for surfaces 1–2 and the query param owns it for 3–4, so the
same fact is stored in two places with different lifetimes.

### 7. The app teleports you away mid-read

[`ai-assistant-context.tsx:697`](../../lemma-frontend/components/ai/ai-assistant-context.tsx#L697):
if a tool result carries a `resourceId`, `setTimeout(..., 500)` navigates you off
the conversation. A half-second delayed jump, deduplicated by a `Set` of seen tool
call ids and an `allowAutoNavigationRef` latch because it kept firing on replayed
history. The display-resource cards already give people a deliberate way in.

### 8. Scroll is hand-driven and fights the user

[`assistant-experience.tsx:333`](../../lemma-frontend/components/lemma/assistant/assistant-experience.tsx#L333)
calls `scrollIntoView` on every message change, switching between `"auto"` and
`"smooth"` depending on whether a run is active;
[`:350`](../../lemma-frontend/components/lemma/assistant/assistant-experience.tsx#L350)
force-pins on every conversation change; `handleSubmit` fires a third scroll.
During streaming that is a scroll animation per token flush.

### 9. The agent panel hydrates three times and concatenates the result

[`agent-test-panel.tsx`](../../lemma-frontend/components/agents/agent-test-panel.tsx)
runs its own `useAssistantController` (:201), *plus* `useConversationMessages`
(:207), *plus* a react-query `useMessages` (:223) — three fetches of one
transcript. It merges two of them by id into a synthetic `controllerView` (:232),
then builds `finalOutputMessages` (:252) by **concatenating raw and mapped
messages** — two copies of the same conversation in one array, deduplicated
nowhere. It only escapes visible duplication because the consumer reads the last
assistant text rather than rendering the list.

### 10. Long answers have no hierarchy, and tables are unreadable

[`assistant-experience-helpers.tsx:188`](../../lemma-frontend/components/lemma/assistant/assistant-experience-helpers.tsx#L188):
`h1`, `h2`, and `h3` all render as `<p className="text-sm font-semibold">`. Every
heading in a structured answer is identical 14px bold text. `design.md` is
explicit that hierarchy comes from **size and the space above, never from
weight** — this does the exact inverse, and flattens a well-organised answer into
one undifferentiated block.

[`:222`](../../lemma-frontend/components/lemma/assistant/assistant-experience-helpers.tsx#L222):
tables get `min-w-max`, so a single prose cell turns the table into a horizontal
scroller inside a chat column — nothing ever wraps. Every `th`/`td` gets a full
`border`, producing a grid of boxes against a design system whose first rule is
*alpha hairline rules, not borders*.

This is not an accident of taste. The design audit **exempts** chat from
enforcement — `strictEnforcedExcludesProtectedAssistantUi: true`, with the seven
`assistant-experience-*` files listed as protected. Chat is the only surface in
the product the design system does not police, and it shows.

### 11. Everything moves at once the moment a run ends

Five layout changes fire in the same commit when `isRunActive` flips false, none
of them coordinated with the others:

1. **The trace collapses.** `collectCompletedRunTraceGroups` folds the finished
   run's rows, and [`CompletedRunTraceGroup`](../../lemma-frontend/components/lemma/assistant/assistant-message-group.tsx#L51)
   opens `useState(false)` — so the thinking and tool steps that were on screen a
   frame earlier are re-parented into a collapsed group and vanish.
2. **The tool rollup expands.** `shouldShowHeader` included `isRunActive`, so a
   short run showed a collapsed "Working" line while running and unfurled its
   tool cards on completion — growing at the same instant (1) shrank.
3. **Every text block grows.** `showActions={!withinTrace && !isCurrentRunActive}`
   added a copy/timestamp bar per block, its own comment admitting it had moved a
   streaming-time gap to completion time.
4. **Rows remount.** Re-parenting into the group throws away everything the
   reader had expanded.
5. **The viewport slides.** The scroll effect used `"auto"` while busy and
   `"smooth"` otherwise, so a scroll animation played only at the end.

### 12. The answer blinks out between the stream and the durable message

[`useAssistantRuntime`](../../lemma-typescript/src/react/useAssistantRuntime.ts)
mirrors session messages into its store through an effect, which runs *after*
commit. The session clears its streamed token buffer the moment the durable
message upserts. For one commit the buffer is empty and the durable message has
not been mirrored — so the text is in neither, and disappears.

This was known for reasoning: `resolveStreamingThinking` is a hand-built bridge
holding the last streamed thought until its durable `THINKING` message lands, and
`streaming-thinking-handoff.test.ts` pins it. Answer text has the identical gap
and no bridge. The fix is the shared cause, not a second bridge: merge the
session's view into the store at derive time so the message is present in the
commit it arrives.

## The target

**One store.** `useAssistantSession` holds raw API messages — history and
streamed, upserted by id
([`upsertConversationMessage`](../../lemma-typescript/src/assistant-events.ts#L168)).
That is already the single source of truth; it just is not treated as one.
`useAssistantController` derives mapped messages from it. Nothing above the
controller fetches messages, ever.

**One owner of "which conversation".** The route owns it where there is a route
(surfaces 1–4). The component owns it where there is not (5–7). The controller
follows; it never pushes back. `?assistantConversationId` is deleted — the docked
sidebar reads the route's id, and its open/closed state is UI state, not identity.

**Transcripts are replaced, not blanked.** Switching conversations keeps the old
list on screen until the new one resolves; loading is a fill, not a screen — the
same rule [loading-and-empty-states.md](./loading-and-empty-states.md) already
established for the rest of the product.

**One renderer.** All nine surfaces render through `AssistantExperienceView` with
one markdown component map, held to the same design audit as everything else.

## The plan

Ordered so each phase is independently shippable and independently revertable.

### Phase 0 — Confirm (half a day)

Instrument `hydrateToolReturnMessages` and the agent panel's merge; confirm both
are no-ops on a real conversation with tool calls, approvals, and a display
resource. Capture a before-trace: fetch count and render count for opening a
30-message conversation, cold and warm. Everything after this is measured against
that number.

### Phase 1 — Delete the duplicate hydration (1 day, `lemma-frontend`)

Remove `rawMessagesQuery`, `hydrateToolReturnMessages`, `rawToolReturnPayload`,
and `normalizedToolResult` from the provider; `displayMessages` becomes
`controller.messages`. Remove `useMessages` + `controllerView` +
`finalOutputMessages` from the agent panel. If Phase 0 finds a field the mapper
genuinely drops, fix it **in the mapper**, not in a consumer.

*Kills breakages 1, 2, 9. Removes two 100-message fetches per conversation and one
per completed run, and stops the whole-app re-render cascade.*

### Phase 2 — Keep the transcript on screen (1–2 days, `lemma-typescript`)

In `selectConversation`, only `clearRuntimeMessages()` when the target
conversation's messages are not already in the store; when they are, swap
`activeConversationId` and skip the loading state. Add a `messages` selector keyed
by conversation so the store can hold more than one transcript.

Then the load gate collapses: `shouldLoadActiveConversationMessages` becomes
`enabled`, and `sideViewMessageLoadGeneration` /
`readySideViewMessageLoadGeneration` / `activeConversationMessagesCached` all go.

*Kills breakages 3 and 5. This is the one people will feel most.*

### Phase 3 — One owner of conversation identity (2–3 days)

Delete `ASSISTANT_CONVERSATION_PARAM` and both URL-sync effects, and with them
`suppressAssistantUrlRestoreRef` and `skipNextAssistantUrlSyncRef`. The sidebar
takes the conversation id as a prop from the route.

Make `/conversations/new` a real state rather than a route that redirects: the
route renders the composer, `sendMessage` creates the conversation, and the id is
swapped with `history.replaceState` — no `router.replace`, no remount, no double
rAF. Both `waitForXReset` helpers get deleted.

Fold the `assistantMessage` launch path into the same mechanism.

*Kills breakages 4 and 6.*

### Phase 4 — Stop moving things (1 day)

Delete the auto-navigate-on-tool-result block and its two refs and blocklist.
Replace the `scrollIntoView` effects with CSS bottom-pinning plus one explicit
"jump to latest" affordance, so streaming never fights a user who has scrolled up.

*Kills breakages 7 and 8.*

### Phase 5 — Make it read well (1 day)

Real heading scale (size and leading, weight stays 500 per `design.md`). Tables
that wrap by default, hairline rules instead of gridlines, horizontal scroll only
when genuinely needed. Then **remove the assistant exemption from the design
audit** and burn down whatever it reports.

*Kills breakage 10, and stops it recurring.*

### Phase 6 — Converge the stragglers (1–2 days)

Point `run-steps` and `agent-test-panel` at a shared read-only transcript hook
rather than a full controller each. Decide `AgentThread`'s relationship to
`useAssistantController` — today it is a separate path with separate behaviour,
which means customer apps get a different chat from the one we ship. Delete the
unrendered `PodAssistant`.

## What gets deleted

Approximate, from the current tree:

| Thing | Where | Lines |
| --- | --- | --- |
| `hydrateToolReturnMessages` + helpers + query | `ai-assistant-context.tsx` | ~110 |
| URL sync + restore effects + refs | `ai-assistant-context.tsx` | ~85 |
| Auto-navigation on tool result | `ai-assistant-context.tsx` | ~60 |
| Load-gate generation machinery | `ai-assistant-context.tsx` | ~45 |
| `waitForControllerReset` / `waitForConversationReset` | both files | ~22 |
| Triple hydration + concat | `agent-test-panel.tsx` | ~50 |
| Unrendered floating assistant | `pod-assistant.tsx` | ~140 |
| Manual scroll effects | `assistant-experience.tsx` | ~40 |

Roughly 550 lines out of the ~13,800 in the chat path. The count that matters
more is effects: of the 36 `useEffect`s across the four layers, the phases above
remove about a third — five of the provider's eight (URL restore, URL sync, raw
refetch, auto-navigation, side-view generation), two of the route's five, and
both manual scroll effects. Every one of them is a place where two layers were
negotiating over the same fact.

## How we know it worked

- Opening a cached conversation: **zero** network requests, **zero** blank frames.
- Opening a cold conversation: one request, one skeleton, one paint.
- Completing a run: zero refetches.
- Sending the first message in a new conversation: no remount, no route change
  visible to the reader, stream never interrupts.
- Scrolling up during a stream: the view stays where you put it.
- `npm run design:audit` passes with the assistant exemption removed.
- Render count for a 30-message transcript with tools, versus the Phase 0 baseline.

## Open questions

1. **`AgentThread` and the SDK's public surface.** `useConversationMessages`,
   `useAssistantSession`, and `useAssistantController` are all exported. Phase 2
   changes controller behaviour; is that a breaking change for embedders, or is
   the controller effectively internal today?
2. **Multi-transcript store size.** Keeping previous transcripts resident is what
   makes Phase 2 work. Cap it — LRU of 3? — or let react-query own eviction?
3. **Does anything actually want auto-navigation?** Removing it in Phase 4
   assumes not. Worth confirming against the onboarding flows that pass
   `?assistantMessage`, which are the most likely dependents.
