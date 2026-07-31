# Schedules move into Agents and Workflows

**Status:** Implemented · **Surface area:** `lemma-frontend` only — no backend changes

## The change in one sentence

A schedule stops being a pod-level *place you visit* and becomes a *property of
the thing it wakes up* — "when does this run on its own" — set from a modal on
the agent or workflow itself.

## Why

The agent page already asked the right question and then refused to answer it.
`Runs when · [Weekdays at 09:00] [+ Add trigger]` sat on the agent's own card,
but the button opened a sheet that could only build two of the three trigger
types, and every chip in the row was inert — no pause, no edit, no delete. The
workflow page had the same row with the same chips and its button was a *link*
to `/schedules/new?workflow=…`: you left the workflow to say something about the
workflow, and the page you landed on opened by asking which workflow you meant.

So three problems, one root cause:

1. **The context was thrown away and then asked for again.** The target is the
   page you came from. A separate page cannot know that, so step one of its
   four-step wizard is a question that had already been answered.
2. **The inline path was a subset.** [`inline-trigger-form.tsx`](../../lemma-frontend/components/pod/inline-trigger-form.tsx)
   covered time and data changes and punted app events to "More options" — a
   link away. Anything beyond creation (condition, visibility, pause, delete)
   was only on the index page, and *editing an existing trigger did not exist
   anywhere in the product*.
3. **Two nav items for one idea.** Schedules had a rail item, a route, an index
   with a four-step builder, a second builder at `/schedules/new`, a chip row on
   two detail pages, and a sheet. This is the shape Surfaces had before
   [surfaces-into-agents.md](./surfaces-into-agents.md), and the fix is the same.

## Current state

| Piece | What it did | Disposition |
| --- | --- | --- |
| [workspace-sidebar.tsx](../../lemma-frontend/components/pod/workspace-sidebar.tsx) rail item, worktree branch, "New schedule" in the create menu | Nav entry points | **Deleted** |
| `app/pod/[id]/schedules/page.tsx` (1268 lines) | Index + inline four-step builder | **Replaced** with a redirect |
| `app/pod/[id]/schedules/new/page.tsx` (940 lines) | A second builder | **Deleted** |
| [inline-trigger-form.tsx](../../lemma-frontend/components/pod/inline-trigger-form.tsx) | Sheet builder, time + data only | **Deleted** — superseded by the modal |
| `resource-automation.tsx` → `TriggersSection`, `AutomationPane`, `TriggerIdentityChip` | Already-dead third implementation | **Deleted**; `RecentConversations` moved to [recent-conversations.tsx](../../lemma-frontend/components/pod/recent-conversations.tsx) |
| `WiringRow` / `Nothing` in `agent-wiring-rows.tsx` | Row primitive imported by the workflow page | **Moved** to [wiring-row.tsx](../../lemma-frontend/components/pod/wiring-row.tsx) so triggers can use it without a cycle |
| — | — | **New:** [trigger-modal.tsx](../../lemma-frontend/components/triggers/trigger-modal.tsx), [triggers-row.tsx](../../lemma-frontend/components/triggers/triggers-row.tsx), [settings/automation](../../lemma-frontend/app/pod/[id]/settings/automation/page.tsx) |

## Target information architecture

**Agent and workflow detail — the "Runs when" row becomes the whole manager.**

```
Runs when   [⏱ Weekdays at 09:00 ·]  [◨ On insert in leads ·]        [+ Add trigger]
            ▲ chip → modal, already filled in       ▲ paused reads as a grey dot
```

Both pages render the identical `TriggersRow`; the only difference between them
is the target passed in and what "nothing" reads as ("You ask it to." for an
agent, "You start it." for a workflow).

**Pod settings → Automation** keeps the one view no agent page can give: every
trigger in the pod, what it wakes up, and whether it is running. Read-mostly —
pause, resume, delete, and a link to the owner. Creation is not offered there,
because creating one means naming a target, which is the question this whole
change exists to stop asking.

---

# Modal design

Same principles as the surface modal, and for the same reasons.

1. **A journey, not a form.** Two states. `kind` asks what should start the work;
   `details` asks only what that answer implies.
2. **Don't ask what you can derive.** The target never appears as a field. Nor
   does a name — schedules are identified by what they do and what they wake up.
3. **Constraints render as state, never as a failed save.** "On a data change"
   is disabled with *This pod has no tables yet*; "On an app event" is disabled
   for a workflow whose start is not an event, naming the fix. Neither is
   discovered on Save.
4. **One primary verb.** Pause and delete live in a header overflow, not beside
   Save.
5. **Narrower.** `sm:max-w-md` for `kind`, `sm:max-w-lg` for `details`.

```
┌────────────────────────────────────────────┐
│ ⏱  Trigger              ·Active·   ⋯   ✕   │
│ Ops Assistant runs on a rhythm.            │
├────────────────────────────────────────────┤
│  Hourly [Daily] Weekdays Weekly … Custom   │
│  Time  09:00        Timezone  UTC          │
│  Daily at 09:00 · UTC                      │
│                                            │
│  Only run when  ┌──────────────────────┐   │
│                 └──────────────────────┘   │
│                                            │
│  Runs as                                   │
│  Nobody is there to run it, so it borrows  │
│  your identity — Ops Assistant reaches the │
│  same tables, files, and connected         │
│  accounts you can.                         │
│  ┌────────────────────┐┌────────────────┐  │
│  │ ⦿ Once for the pod ││ ○ One per      │  │
│  │   One trigger,     ││   person       │  │
│  │   doing the work   ││   Yours alone. │  │
│  │   for everyone.    ││   …            │  │
│  └────────────────────┘└────────────────┘  │
├────────────────────────────────────────────┤
│ Back                     [Cancel]  [Save]  │
└────────────────────────────────────────────┘
```

## "Runs as" replaces the share control

Creation originally reused `ResourceVisibilitySelect` — which is
`ResourceShareButton`, the same dialog documents get, offering PERSONAL / POD /
RESTRICTED / PUBLIC with a copyable link. On a trigger that asks the wrong
question twice over: **a trigger has no page to open**, so "anyone signed in can
open it using the link" describes nothing, and the one choice that genuinely
matters was buried inside it, unlabelled and unexplained.

What matters is *whose access a run gets*, because nobody is present when it
fires:

- **TIME and WEBHOOK** — the run borrows the setter's identity.
  `ScheduleFired` carries `user_id=schedule.user_id`
  ([schedule_event_publisher.py:29](../../lemma-backend/app/modules/schedule/infrastructure/adapters/schedule_event_publisher.py:29)),
  and the agent or function reaches exactly what that person can.
- **DATASTORE** — on a table with row-level security the run acts as the owner
  of the record that changed, so it sees only what they can; on any other table
  it acts as the creator.

And that fact *is* the pod-versus-personal choice, which is why they are one
block rather than two. Because a trigger runs as whoever set it, `POD` means
**once for the pod** — one standing trigger doing the work for everyone, useful
for admin-shaped work — and `PERSONAL` means **one per person**, each running as
its own owner. The distinction is enforced, not cosmetic: schedule reads resolve
through `allowed_actions_expr` with `owner_user_id_col=Schedule.user_id`
([schedule_repository.py:110](../../lemma-backend/app/modules/schedule/repositories/schedule_repository.py:110)).

**A data trigger is not offered the choice at all.** A row change belongs to the
table, not to a person: if five people each held their own copy of the same
DATASTORE trigger, one insert would fire all five. That is duplicate runs, not
personal scoping — so the block collapses to the identity statement, creation
always sends `POD`, and editing never sends `visibility`, since the reader was
never shown a choice to change.

`RESTRICTED` and `PUBLIC` are not offered either — neither has a meaning for a
resource with nothing to open. A trigger already stored with one keeps it:
neither option renders as chosen, and `visibility` is omitted from the update
unless the reader picks one, so the modal never quietly widens who can see it.

The ledger drops the generic badge for the same reason and shows a plain
`Personal` chip, since the question an admin auditing the list is asking is
whether a trigger is the pod's or somebody's own.

## What editing can and cannot change

The update API (`UpdateScheduleRequest`) carries `config`, `filter_instruction`,
`is_active`, `visibility`, and the target — but **not** `account_id` or
`connector_trigger_id`. So a webhook trigger's app and event are stated as facts
with the reason, rather than rendered as fields that would silently not save.
Time and data triggers edit fully; the backend re-registers a TIME job whenever
its config changes ([schedule_service.py:491](../../lemma-backend/app/modules/schedule/services/schedule_service.py:491)).

Two smaller consequences of the API shape, both handled in the modal:

- The backend drops `None` from updates (`model_dump(exclude_none=True)`), so
  clearing a condition sends `''`, not `null`. An empty instruction is falsy on
  both sides and reads as "no filter".
- Editing needs the cadence controls rehydrated from a stored cron, which is
  what `parseCronExpression` is for — the inverse of `buildCronExpression`,
  with anything the controls cannot express round-tripping losslessly as
  `custom`.

## Permissions

`schedules: ['schedule.read', 'schedule.create']` stays as a route policy key
and now gates `/settings/automation` — resolved as a special case in the pod
layout, because the ledger is about schedules and schedule permissions belong to
builders, while the rest of `/settings` is gated for pod admins.

## Out of scope

The schedule model, the scheduler, filter evaluation, webhook ingress, and the
bundle representation of schedules. This is an IA and setup-experience change;
nothing about how a schedule fires moved.
