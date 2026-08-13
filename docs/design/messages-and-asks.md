# Messages and Asks

**Status:** Proposed · **Supersedes:** the design in [PR #264](https://github.com/lemma-work/lemma-platform/pull/264), treated here as a spike · **Surface area:** `agent_surfaces`, `agent`, `workflow`, one new worker, both SDKs, one inbox

## The change in one sentence

An agent gets exactly two verbs for reaching a human — **tell** and **ask** — sharing one delivery pipeline underneath, and *ask* reuses the two suspend/resume mechanisms Lemma already has instead of becoming a third.

## Why

PR #264 proved the demand and found the real bugs. It also shipped five ways to put text in front of a person, and the audience control it advertises is enforced nowhere. Both of those follow from one framing error, and the error is worth naming precisely because it is easy to make again.

**#264 modelled the channel and left the intent implicit.** `notify` picks *where* a message lands. But the question that actually determines everything downstream is *what kind of act this is*:

| | Does a run wait on it? | Who resolves it? | What does "delivered" mean? |
| --- | --- | --- | --- |
| **Tell** | never | nobody | it reached them |
| **Ask** | yes | a named person | they answered |

Those are different entities. Collapsing them means `message_person` has to say, in its own docstring, *"do not wait on it here"* — a capability defined by what it cannot do. Meanwhile the thing users asked for ("ask Priya for the PO number") is an Ask, so it stayed unbuilt and got listed under *deliberately not here*.

The doc for #264 saw this coming. It worried about becoming "a fourth parallel mechanism for the same idea" and then shipped one anyway, because the framing gave it nowhere else to go.

### Lemma already knows how to wait for a human. Twice.

This is the fact that changes the design:

- **`workflow_run_waits`** — `wait_type`, `external_ref`, `assigned_pod_member_id`, `payload`, and a partial unique index enforcing *at most one ACTIVE wait per run*. A genuinely good table. `WorkflowRunWaitType.HUMAN` is already an ask.
- **`AgentInputRequired`** — `ask_user` and `request_approval` raise it, the run ends cleanly into WAITING, the approvals endpoint records the answer, and a fresh run replays the synthesized response from history. The resume path keys off `tool_call_id`.

Neither needed to be invented. Both were skipped. A third mechanism was written instead, and it is the one that cannot resume.

### The gate is on the wrong noun

`SurfaceSendPolicy.audience` lives on the surface. `message_person` and `notify` are surface-independent — a schedule-born run has no surface at all. So the policy **structurally cannot** gate the thing two shipped skill docs say it gates. That is not a missing `if`; it is the field being attached to an entity that is not in the call path.

Permissions attach to the thing that *has an identity*. A surface is a transport. The agent is the actor.

## Three layers

Separating them is the whole proposal. Each has exactly one implementation.

```
intent      Message | Ask          what kind of act this is
   │
delivery    Dispatch (outbox)      one row per attempt, retried, idempotent
   │
address     Reach                  where a person can be received
```

### Layer 1 — `Reach`: where a person can be received

Keep #264's noun. It is the genuinely new and correct one: *"can this pod reach Deepak at all, and where"* had no answer before, only a three-way join. Three corrections:

**Reuse `SurfacePlatform`.** `ReachKind` re-declares all seven values plus `APP`, and `for_platform()` is `cls(platform.value)` — it raises on an eighth platform, inside a `try/except: logger.debug`, so adding a platform silently stops recording reaches. The address is `(platform, surface_id)`. There is no eighth enum.

**Delete `APP` as a channel.** Nothing reads it. `ensure_app_reach` writes rows that no code path consumes, because the notification is written unconditionally and `_pick_chat_reach` skips APP. It is scaffolding for a fallback that is really a *guarantee*, and a guarantee does not need a row. The inbox is Layer 3.

**Every column gets a writer, or it does not ship.** #264 ships `status` with two of three values unwritten, `opted_out_at` with no writer at all, and `mark_stale_for_user` with zero callers — while `on_identity_event` clears `resolved_user_id` and leaves the reach pointing at the old address. A recycled phone number is then a cross-user delivery. Reach health is a lifecycle with real transitions:

| Transition | Trigger |
| --- | --- |
| → `ACTIVE` | inbound DM (creates or revives; never clears opt-out) |
| → `STALE` | `UserMobileChangedEvent` — the same handler that clears the identity cache |
| → `BLOCKED` | a dispatch fails with a permanent platform error (403, blocked, unknown recipient) |
| → opted out | the person says so, on their own surface or in settings |

`window_expires_at` stays — WhatsApp's 24h rule is real, and holding it as data rather than a branch in the send path is one of #264's better calls.

### Layer 2 — `Dispatch`: delivery is a row, not a function call

This is the structural change. `NotificationService.notify` currently does membership check → reach pick → conversation resolve → persist → **platform HTTP** → write row, all inline, in one transaction. That single shape causes four separate problems:

- **No fallback.** It picks one reach and stops. A blocked Telegram bot means chat delivery is dead for that person forever, silently, because nothing demotes the reach either.
- **No idempotency.** #264 names this: a worker retry double-posts to a chat platform.
- **HTTP inside a transaction**, holding a pooled connection across a Slack round-trip — against the grain of `execute_chat`, which explicitly scopes short UoWs *around* platform I/O.
- **Ordering contradicts the stated invariant.** "The inbox is the delivery, chat is the enhancement" — but the row is written last, after the send. Crash in between and the phone has it, the inbox does not.

Model the attempt:

```
dispatches(id, pod_id, subject_type, subject_id, reach_id, idempotency_key,
           status, attempt, next_attempt_at, permanent_error, delivered_at)
```

`subject_type` is `MESSAGE | ASK`; `subject_id` points at the Layer-3 row. Then:

1. The synchronous path writes the **subject** and the first **dispatch**, and commits. It performs no network I/O. This is where the durability guarantee actually lives, and it is now one transaction with nothing slow in it.
2. A `streaq` worker claims the dispatch, calls the adapter, and records the outcome. `streaq_task` already defaults `max_tries`; backoff and retry are the runtime's job, not ours.
3. A permanent failure marks the reach `BLOCKED` and enqueues a dispatch against the **next** reach. That is what "first success wins" was supposed to mean — an ordered walk with real fallback, not one attempt with a hopeful name.
4. `idempotency_key = (subject_id, reach_id)`, unique. A retried worker cannot double-post.

Delivery receipts are not a separate table written against a guess — #264's reason for deferring them. They are `dispatches`, which we need anyway.

**And this forces the refactor #264 correctly identified and then deferred.** A worker dispatching from a stored address has no inbound event and never will. `SurfaceTarget.to_event()` reconstructs a fake `ParsedInboundSurfaceEvent` with `message_text=""` and a comment hoping no adapter reads it. As a temporary seam that is a defensible trade. As the permanent shape of every outbound message it is a lie the adapters will eventually believe. Do the 134-signature hoist — as its own PR, before this one, reviewable on its own merits.

### Layer 3 — Intent: `Message` and `Ask`

**`Message`** is one-way and terminal.

```
messages(id, pod_id, recipient_user_id, sender_user_id, agent_id,
         title, body, attribution, origin_type, origin_id,
         conversation_id, read_at)
```

`attribution` is a stored column, not a string the caller prepends. #264 renders it as `f"{attribution}\n\n{body}"` inside `notify` and then two of its three callers pass `None` — the `NOTIFY` executor and the HTTP endpoint. The endpoint even takes `user: CurrentUser` and does `del user`. Attribution that a caller can omit is not mandatory, and the doc calls it mandatory. Derive it server-side from the authenticated principal and the run's agent; there is no code path that supplies it.

**`Ask`** is a question with an owner and an answer.

```
asks(id, pod_id, asked_of_user_id, asker_user_id, agent_id,
     prompt, input_schema, status, answer, answered_at,
     origin_type, origin_id, conversation_id)
```

The critical property: **`Ask` introduces no new suspend/resume machinery.** It plugs into both existing ones.

*Workflow.* `WorkflowRunWaitType.HUMAN` waits get `external_ref = ask_id`. The `workflow_run_waits` row stays exactly where it is, keeping the one-ACTIVE-wait-per-run index and the whole resume path; `asks` becomes the person-facing entity it points at. Machine waits (`AGENT`, `FUNCTION`, `TIME`) are untouched, because they are not asks. `FormNodeConfig.assignee_pod_member_id` stops being a queue nothing pushes to.

*Agent.* `ask_user` gains an optional `person`. When set, the ask is delivered to that person; when unset, behaviour is byte-identical to today. The pause is the same `AgentInputRequired(tool_call_id)`, the conversation goes WAITING the same way, and the resolution endpoint replays the same synthesized response — **it never cared who submitted the answer, only that it was recorded against the tool call.** Cross-member ask is a routing change, not a mechanism.

So `message_person` does not exist. `ask_user(person=...)` covers asking; `notify` covers telling.

#### Authority inversion, answered rather than deferred

#264 named the hazard and deferred on it: B answers with data B can read, and the value lands in A's run context where RLS never authorized A to see it. The proposed fix there was "an RLS re-check against the asker," which is not implementable — you cannot re-check a free-text string against a table it was never read from.

The answer is the one `FormNode` already uses: **an Ask carries an `input_schema`, and the answer is validated against it.** A returns only the fields A declared. That converts an unbounded leak into the same trust model as B forwarding an email — B can still deliberately type a secret into a free-text field, and no schema stops that. It is a real residual risk and it should be written down rather than designed around: the answer is recorded with the answerer's identity and timestamp, so it is auditable after the fact.

This also kills free-text cross-member asks as the default, which is the right product default anyway: "what is the PO number" wants a field, not a chat.

### The inbox is a view, not a channel

Drop `notifications` as a distinct table. A person's inbox is *unread Messages + open Asks assigned to them*, and that union is better product behaviour than a single `read_at`:

- A read Message stops nagging.
- An **open Ask keeps nagging until answered**, because "I saw it" is not "I answered it". A single `notifications` table cannot express that difference, which is why #264's inbox shows a question and a report identically.

`GET /inbox` returns the union, newest first. The badge is `unread messages + open asks`. Both queries hit a partial index; neither is a scan.

## Where the gate lives

| Capability | Gated by | Why there |
| --- | --- | --- |
| `surface_send_message` (reply on this surface) | `surface.config.allow_agent_replies: bool` | the tool literally only exists on a surface |
| Tell / ask **the run's own owner** | nothing beyond running | it is the reply, to the person the work is for |
| Tell / ask **another pod member** | `Agent.messaging_policy.audience` + `AgentToolset.MESSAGING` | the agent is the actor with an identity |
| `POST /pods/{id}/notify`, `/asks` | new `member.notify` permission | not an act of editing agents *or* of writing conversations |

Three specific consequences:

**Revert `SurfaceSendPolicy` to a boolean.** #264 grew it into an audience whose third rung is enforced nowhere and whose second rung duplicates the boolean. `allow_send` was correct; it just needed a better name. An audience on a surface can only ever be wrong.

**`conversation.write` is the wrong permission for `/notify`.** It sits in `POD_USER_PERMISSIONS`, so today every pod user can push unattributed text to any colleague's Slack as the pod's bot. #264's reasoning for relaxing off `agent.update` is sound — sending a message is not editing an agent — but the answer is a permission that names the act, not the nearest low-tier one that happens to exist.

**Rate limiting is enforced, not modelled.** `max_messages_per_recipient_per_hour` exists in #264 as a field nothing reads. With Layer 2 it becomes a check at dispatch enqueue, keyed `(agent_id, recipient_user_id)`, in Redis — the same place every other counter in this codebase lives. A badly-prompted agent in a retry loop is the expected failure and it is cheap to stop.

## Schema

| | Kind | Notes |
| --- | --- | --- |
| `member_reaches` | new | #264's shape minus `kind` (use `platform`), minus the `APP` row |
| `dispatches` | new | the outbox; unique on `(subject_id, reach_id)` |
| `messages` | new | one-way, with stored `attribution` |
| `asks` | new | `input_schema` + `answer`; `workflow_run_waits.external_ref` points here |
| `notifications` | **not created** | superseded by the `messages` ∪ `asks` view |
| `agent_surface_conversation_links.last_inbound_at` | new column | lifted from #264 unchanged — see below |
| `agents.messaging_policy` | JSONB | audience moves off the surface |

## What to take from #264 as-is

Four changes in that branch are correct, independent of everything above, and should land now as their own small PRs rather than waiting on this design:

1. **`last_inbound_at`.** The DM-reset bug is real: `_should_reset_dm_conversation` keyed off `updated_at`, which a proactive send also bumps, so an agent message would suppress the 24h reset and leak yesterday's context into today. The backfill argument holds — until that column exists, only inbound wrote the row.
2. **Fail-closed membership.** `if self.pod_membership_port is not None` turned a wiring bug into *"any user id can be messaged"*, in both `send_to_member` and `notify`.
3. **`can_cold_open` / `reply_window_hours`.** Pure data, no behaviour, immediately useful. "Bots can't cold-open" being a chat-platform fact rather than a universal truth is a genuine correction to [surfaces-into-agents.md](./surfaces-into-agents.md).
4. **The conversation-hint ownership check.** An agent running as one person must not drop a colleague into its own thread. #264 gets this exactly right, with a test named after it, and the rule survives into `asks` unchanged.

Also worth carrying forward, as prose rather than code: #264's honest note that `origin_type` is **provenance** being used as a proxy for **attention**, and that Lemma cannot answer "is anyone reading this". That analysis is correct and this design does not improve on it. It just narrows the blast radius — under this design a run telling its owner something is never gated on attention, because the inbox makes a redundant badge the worst case rather than a missed message.

## Phasing

Each phase ships alone and is useful alone.

| Phase | Contents | Unlocks |
| --- | --- | --- |
| **0** | The four salvaged fixes above | bug fixes, no new surface area |
| **1** | The adapter signature hoist (`SurfaceTarget` in, `event` out) | dispatch from a worker becomes honest |
| **2** | `Reach` + `dispatches` + `messages` + inbox. **Own-owner only.** | *"tell me when the nightly run finishes"* — the headline use case |
| **3** | `asks`: `ask_user(person=)`, FORM waits repointed, cross-member | *"ask Priya for the PO number"* — the one people actually asked for |
| **4** | Cross-member `messages`, agent-level policy, rate limits enforced | the capability with the phishing hazard, last, on proven plumbing |

Phase 2 delivers #264's headline value with none of its cross-member risk, because "a schedule tells its owner" needs no audience policy at all. The dangerous capability lands in Phase 4, on a pipeline that by then has retries, idempotency, reach demotion and receipts.

## Deliberately not here

**Digests.** First-success-wins solves cross-channel spam; nothing here solves ten notifications from one workflow in one minute. With `dispatches` a digest is a coalescing window on the outbox rather than a redesign, so it is cheap to add later and expensive to guess at now.

**Quiet hours.** Opt-out lives on the reach. Time-of-day needs its own table and a timezone per user, which Lemma does not have. Flagged so it is not discovered later as a JSONB blob.

**Templated WhatsApp cold opens.** `can_cold_open=False` for WhatsApp is a statement about *free-form*, and stays true until templates are modelled. Email already cold-opens.

**Retiring `POST /surfaces/{name}/send`.** It stays. #264 is right that collapsing it would silently change what an existing path parameter means. It becomes the one *surface-specific* primitive, and the docs should say so instead of listing it as an alternative to `notify`.

**Ambient notification on schedule failure.** `schedules.consecutive_failures` exists and nothing reads it. Probably the real answer to #264's open question about whether scheduled runs should notify by default — but it is a schedules feature that happens to use this pipeline, not part of building it.

## Open questions

1. **Does an `Ask` expire?** A workflow run blocked on an unanswered question is a run that never finishes. `workflow_run_waits` has no TTL today and the same gap exists for forms, so this is pre-existing — but cross-member asks will make it visible fast. A `due_at` plus an escalation reach is the obvious shape and is not designed here.
2. **Is `Ask` a `ResourceType`?** It is assignable and permission-checked, which argues yes. `Message` is user-scoped and almost certainly is not. #264 raised this and it is still open.
3. **Can an agent ask a *non-member*?** Email can address anyone, and "get a quote from this supplier" is a real request. Everything above assumes pod membership as the trust boundary. Relaxing it is a much larger conversation about what an external participant *is*, and the answer is probably a different entity.
4. **One `messaging_policy` per agent, or per (agent, surface)?** Per-agent is proposed here because the agent is the actor. An agent that should message colleagues in Slack but never over WhatsApp is expressible as a reach-level opt-out instead — but nobody has asked for it yet, and inventing the axis before the demand is how `send_policy` ended up on the surface.

## See also

- [surfaces-into-agents.md](./surfaces-into-agents.md) — surfaces as a property of an agent; Truth 8 ("proactive messaging never cold-opens") is superseded by `can_cold_open`
- [schedules-into-triggers.md](./schedules-into-triggers.md) — where the "runs as" identity model comes from, and why a schedule-born run has an owner but no reader
