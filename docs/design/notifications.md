# Agents can reach a person, and the reply lands somewhere useful

**Status:** Implemented · **Surface area:** `lemma-backend/app/modules/agent_surfaces` (bulk), one agent toolset, one capability, the workflow FORM hook, both SDKs, a sidebar bell.

## The change in one sentence

An agent running on your behalf can ask a teammate for something, on whichever
app they actually use — and the answer finds its way back without the run ever
blocking on a human.

## Why

An agent could only get something from the person already in front of it.

- `ask_user` / `request_approval` pause the run and resume with the answer, but
  only for the person **already in this conversation, on this surface**. A
  schedule- or workflow-born run has no such person.
- `surface.send` → [`send_to_member`](../../lemma-backend/app/modules/agent_surfaces/services/ingress_service.py)
  could push a string at a member, but required `agent.update` — an editor
  permission — so its only possible caller was a human clicking through the
  surface modal. No CLI command, no workflow node, no agent tool. It persisted
  nothing and returned a bare bool.
- A workflow `FORM` node assigned to a pod member was a pure **pull** queue. The
  wait row existed, `workflow.run.waiting_assigned_to_me` listed it, the flows
  page rendered it — and nothing ever told the assignee. A run could sit for
  three days on somebody who had no idea.
- There was no in-app notification system at all.

The cases people actually ask for: *"write this report and send it to Priya for
review"*, and *"run the standup"* — four messages to four people, collect what
comes back.

## The shape

**A notification is the durable record that the pod told a person something, and
what their agent should do with the reply.**

```
agent A (runs as Anukul)          notification row              agent B (runs as Priya)
  message_user(to=Priya,   ──►  status=OPEN                ──►  delivered on Priya's
    message=…,                   delivery=DELIVERED             own surface thread
    background_instruction=…)    background_instruction              │
         │                              ▲                           │ Priya replies
    snooze(1800s)                       │                     normal ingress → run
         │                              │                           │
    wakes, check_messages ──────────────┴──── respond_to_notification(summary)
```

The asker **never blocks on a human**. It fires N messages, `snooze`s — which
[already existed](./agent-snooze.md) — wakes, and reads results.

### Why this is smaller than `Ask`, and safer

[PR #264](https://github.com/lemma-work/lemma-platform/pull/264) reached 8k lines
trying to solve delivery *and* answer-resolution together, then cut the second
half as "a release of its own". The `Ask` entity it deferred carried a genuine
hazard: **authority inversion** — B answers with data B can read, and that value
lands in A's run context, which RLS never authorized A to see.

Here, resolution is done by the *recipient's own* agent, under the *recipient's*
authority. B's agent writes under B's RLS; A reads under A's. Nothing crosses.
The permission boundary holds by construction rather than by a re-check somebody
has to remember to write.

That single decision is why this is ~2,400 lines rather than 8,000.

### Should the wait mechanisms be centralized? No.

The three are not the same thing:

| | `workflow_run_waits` | `agent_conversation_waits` | `agent_approval_decisions` |
|---|---|---|---|
| what suspends | a workflow run at a node | an agent turn mid-tool-call | an agent turn mid-tool-call |
| resume is | `submit_form` / event / timer | timer | a decision row |
| assignee, JSON schema, validated submit | yes | no | no |
| expiry ceiling | 6h machine / 72h human | none (self-resolving) | none |

Merging them is a two-module migration across working sweeps, expiry ceilings and
RLS for no user-visible gain — and under this design the asker's wait is just a
`snooze`, so no new human-wait semantics were needed at all. What the workflow
and the agent genuinely share is *"a person must be told, durably, and the UI
must list it"*. That is `notifications`, and both write to it.

`ask_user` is untouched. It is a different verb: *ask the person in front of me
and pause.* `message_user` is *reach someone else and keep going.*

## The entity owns the ask

Every resolving transition passes one `_require_open` gate and raises
`NotificationTransitionError` (409) otherwise. Two people answering from two
devices, an agent answering one the asker already cancelled, a sweep expiring one
answered a second earlier — all refused, rather than overwriting an answer
somebody may already have acted on.

`respond` additionally refuses a free-text answer to a `WORKFLOW_FORM` row. Only
`resolve_through_action`, called by the engine *after* the node's schema
validated the submission, may close one — otherwise a form has two answer paths
and exactly one of them validates.

Legality lives in the domain, not in the controller or the tool.

## Schema

One new table, one new column. `0013_notifications`, revising `0012_agent_snooze`.

**Two status columns, not one.** `status` is where the *person* is; `delivery_status`
is where the *channel* is. They are independent — DELIVERED and still OPEN (they
haven't answered), UNDELIVERABLE and still RESPONDED (they saw it in the app).
One column smearing both cannot answer *"who did we fail to reach?"*, which is
the only question the delivery axis exists for.

`read_at` is a timestamp, not a status: reading something does not answer it.

`idempotency_key`, unique per pod (`wf:{run}:{node}`, `run:{run}:{tool_call}`),
closes a real hole — the surface dedup store claims *inbound* only, so without it
a worker retry double-posts to a chat platform.

**`agent_surface_conversation_links.last_inbound_at`**, backfilled from
`updated_at`. The backfill is correct by definition: until this revision only
inbound events wrote that row.

## Delivery

```
notify → membership check (FAILS CLOSED)
       → persist the row (always, first)
       → resolve channel → resolve/open the recipient's conversation
       → persist the outbound message → send → mark delivered
```

Channel order — a read model over tables that already exist, not a new
`member_reaches` table (it is derivable):

1. **A surface they chose** (`UserPreferences.default_surfaces`), already
   authoritative for *inbound* routing. Same precedence for egress means people
   are reached where they already talk to us.
2. **A surface we've seen them on**, freshest `last_inbound_at` first — where
   they last spoke *to us*, not where we last spoke *at them*.
3. **Email**, the only family that can address someone who never wrote first.
4. **Nothing** — a legitimate outcome, never a silent one. The reason travels to
   the API so the UI can say *"Priya hasn't connected Telegram yet."*

**First success wins.** Three copies across three apps is how a useful feature
comes to read as spam.

**Persist before send.** A failed platform call must still leave the person a
message and the agent a record; the reverse order puts a message on somebody's
phone that the pod has no memory of.

Reusing the recipient's existing platform *thread* is why no cold-open is needed
for chat — and why PR #264's `SurfaceTarget` refactor (134 adapter signature
sites) is **not** here. Email gets its own narrow ~40-line path.

## Reaching someone else is a different act

The recipient sees the pod's bot and extends it the trust they extend to Lemma.
That is a phishing primitive if shipped carelessly.

- **`MESSAGING` is its own opt-in toolset**, not in `POD_DEFAULT_AGENT_TOOLSETS`,
  and withheld from sub-agents.
- **Attribution is mandatory** — every message names both the agent and the human
  whose authority the run carries.
- **The toolset grant is the whole permission.** There is deliberately no
  second, surface-level switch: gating on `send_policy` too would mean a pod
  editor had to find and flip a setting on a bot before a grant they had already
  made took effect — a rule nobody would guess, expressed in the wrong place.
  `send_policy.allow_send` keeps its original meaning (the surface's own
  current-conversation `surface_send_message` tool) and nothing more.
- **Reaching the run's own owner needs no permission at all.** The run already
  carries their delegated authority; telling you about work you asked for is not
  an act that needs authorizing.
- **Pod membership is enforced fail-closed** in the service, on every send.
- **Rate limited**, per recipient per hour, in Redis — enforced, not just modelled.

## Workflow FORM nodes now push

Assigning a form emits a notification carrying the node's resolved schema, so the
inbox renders the real form instead of deep-linking away. Submitting closes it;
cancelling the run closes it. An **unassigned** form notifies nobody — there is no
one person to tell, and broadcasting makes every form everyone's problem.

Every failure on this path is swallowed: a workflow must not fail because a Slack
token expired. The wait still exists and the pull queue still works.

## Bugs this had to fix to work at all

- **`send_to_member` failed open.** `if self.pod_membership_port is not None`
  meant a mis-wired service skipped the membership check entirely — a wiring bug
  became *"any user id can be messaged"*. Inverted.
- **The DM reset keyed off `updated_at`**, which an outbound message also bumps,
  so a notification would silently suppress the 24h reset and leak yesterday's
  context into today.
- **The outbound was never persisted**, so answering *"yes"* six hours later
  reached an agent with no memory of the question.

## Deliberately not here

**Quiet hours and digest mode.** Ten notifications from one workflow in one
minute will be ten messages. Opt-out wants a column on a reach row, which is the
strongest argument for `member_reaches` later.

**Realtime push for the badge.** It polls at 60s. A stale badge costs nothing and
a subscription for one integer is a lot of moving parts.

**Cold-opening a chat platform.** A person who has never messaged the bot is
unreachable on chat — a platform fact, not a bug. Delivery falls back to email,
then the in-app inbox, and `undeliverable_reason` tells the user what to do.

**`surface.send` is untouched.** It stays the surface-*specific* primitive;
`notify` picks the channel.

## See also

- [agent-snooze.md](./agent-snooze.md) — the pause this composes with; its
  *"waking to a proactive message"* open question is what this closes.
- [surfaces-into-agents.md](./surfaces-into-agents.md) — Truth 8 ("proactive
  messaging never cold-opens a thread") is now data on `platform_capabilities`.
