# Agents can start the conversation

**Status:** Implemented (first release) · **Surface area:** `lemma-backend/app/modules/agent_surfaces` (bulk), one workflow node, one agent tool, the SDKs, and a sidebar inbox

## The change in one sentence

An agent stops being something you talk *to* and becomes something that can talk *first* — reaching a person on whichever channel they actually use, and always leaving a copy somewhere that cannot fail.

## Why

`surface.send` existed, worked, and had no callers.

The endpoint required `agent.update` — an editor permission — so the only thing in the entire product that could reach it was a human clicking through the surface modal's *message* state. No CLI command. No workflow node. No agent tool. The SDK wrapped it, but a function would have needed an editor's token to use it.

The agent-side twin, `surface_send_message`, only ever reached `ctx.deps.conversation_id` — the person the agent was already talking to. And it was only attached when the conversation carried `surface_platform` metadata, which a scheduled run never does.

So the two things people actually asked for had no path:

1. **"Tell me when X happens."** A schedule-born run creates a conversation nobody is watching. Lemma itself was not a destination — no inbox, no unread state, and `SurfacePlatform` has seven values, none of which is the web app.
2. **"Ask Priya for the PO number."** Nothing could address a second person.

Underneath both sat one assumption, and it was load-bearing: **egress meant "reply to the last inbound event."** Every adapter method took a `ParsedInboundSurfaceEvent`; `_resolve_egress_target` refused without `link.last_event`. A message the agent *starts* has no inbound event behind it, so it could not be expressed — not "was hard to express", could not be expressed.

Three further consequences fell out of that one assumption:

- **The outbound was never written down.** `send_agent_message_for_conversation` called the adapter and persisted nothing, so answering *"yes"* six hours later reached an agent with no memory of the question.
- **A notification could silently orphan itself.** `_should_reset_dm_conversation` keyed off `link.updated_at`, which a proactive send also bumps — so an agent message would suppress the 24h reset and leak yesterday's context into today.
- **The membership check failed open.** `if self.pod_membership_port is not None` meant a mis-wired service skipped the check entirely, turning a wiring bug into *"any user id can be messaged"*.

## The missing noun

The model already had "how a human reaches the bot" — `SurfaceReach.handle`, resolved lazily and cached. It had no name for the inverse.

That fact existed, but only as a three-way join: `AgentSurfaceExternalUser` (who they are) + `AgentSurfaceConversationLink` (which thread) + `last_event` (the reply token buried inside it). Discoverable only per-surface, only after they had spoken first, and never answerable as a question in its own right.

**A reach is one row: this pod can reach this person, on this channel, at this address.**

```
member_reaches(pod_id, user_id, kind, surface_id, external_user_id,
               target, status, last_inbound_at, window_expires_at, opted_out_at)
```

Pod-scoped on purpose. `agent_surface_external_users` is correctly *cross*-pod — one Telegram account belongs to a person across every pod they're in — and conflating the two is exactly how a shared bot cross-posts between organizations.

Reaches are never created by hand. Every inbound DM writes one.

### `SurfaceTarget`, and the refactor deliberately not done here

The address itself is `SurfaceTarget`: the eleven fields every platform's send path actually reads, projected off the event. `reply_target` and `metadata` are carried whole rather than key-by-key — they are already JSONB on the wire, they are small, and projecting field-by-field would silently drop whatever a platform starts depending on next. `raw_payload` is deliberately absent; its only consumer is Outlook's inbound `requires_message_fetch` enrichment, and it can be large.

The obvious move was to change every adapter to take a target instead of an event. That is **134 signature sites and 167 `event.*` reads across seven platforms** — roughly 800 lines of behaviour-preserving churn with real regression risk and no user-visible value on its own.

So the direction is inverted: the target is the **stored** form, and it rebuilds an egress-shaped event at the adapter boundary. The durable design decision — that an address is a thing you can hold, independent of any message — lands now. The signature hoist becomes a safe, isolated follow-up that can be reviewed on its own merits.

## Lemma is a destination

Every chat channel can fail. A bot gets blocked, a token expires, WhatsApp's 24-hour window closes, someone mutes the app. If the only channels are third-party, *"nobody was told"* is always a possible outcome — and it is a silent one.

So the app is a reach too, and it is the one that cannot fail. `notifications` is written on **every** notify, whether or not a chat platform also took the message. The chat send is the enhancement; the inbox is the delivery.

It is a *peer* of surfaces rather than an eighth `SurfacePlatform`. The web app is not a third-party bot install: no credentials, no webhook, no external identity. Making it one would have meant minting fake `AgentSurfaceExternalUser` rows for real Lemma users, which is a lie the rest of the system would eventually read.

Unread state lives on the row rather than being derived from conversations. Deciding *"which conversations count as notifications"* by reading `conversation_metadata` is precisely the ambiguity the table exists to remove.

## One way to tell someone something

```
notify(pod, recipient, body, origin) →
    membership check (fails closed)
  → reaches, freshest first
  → first deliverable chat channel wins
  → conversation resolved / opened
  → message persisted
  → notification row written, always
```

**First success wins, not fan-out.** Three copies of the same message across three apps is how a genuinely useful feature comes to read as spam. Reaches are ordered by inbound recency, so "freshest" means *wherever they were last talking to us*, which is the best available guess at where they are looking.

### The conversation strategy

A message you cannot reply to is an alert, not a conversation. So every notification lands in a conversation the recipient owns, with the outbound text persisted in it.

- **A live thread is continued** — last touched within 30 minutes.
- **Anything colder opens a new conversation**, seeded with its origin, and the person's thread is repointed at it with a compare-and-set so a concurrent inbound cannot split one thread across two conversations.

The 30-minute window is deliberately much tighter than the surface's 24h DM reset. That setting exists to stop threads living forever, not to decide whether a new subject belongs in an old one — a digest arriving the morning after yesterday's support chat is a new subject, and appending it reads as a non-sequitur.

Persisting happens **before** sending. If the platform call fails the person still has the message in Lemma; the reverse order would put a message on their phone that the agent has no memory of.

## Reaching someone else is a different act

Telling you about work you asked for, and putting words in front of a colleague who did not ask, are not the same operation and should not share a permission.

The recipient sees the pod's bot — not *"the agent someone else's schedule is running"* — and extends it the trust they extend to Lemma. That is a phishing primitive if shipped carelessly. So:

- **`MESSAGING` is its own toolset**, not implied by any other.
- **Attribution is mandatory**, naming both the agent and the human whose authority the run carries.
- **`send_policy` is an audience**, not a boolean: `NOBODY` (default) / `SELF` / `POD_MEMBERS`. The default matches what every existing surface already did — a new capability must not switch itself on for surfaces nobody has revisited. `allow_send` still reads and still round-trips for a release.
- **The reply is theirs.** `notify`'s `conversation_id` is a *hint*, honoured only when the recipient owns that conversation. An agent running as one person cannot drop a colleague into its own thread, where they would read context they were never granted. Their reply opens a conversation they own, under their permissions.

### Cold opens are a platform fact

*"Proactive messaging never cold-opens a thread"* was stated as a universal truth in [surfaces-into-agents.md](./surfaces-into-agents.md) and was only ever true of chat bots. Email genuinely can address someone who never wrote first. WhatsApp can, with a pre-approved template, and separately closes a 24-hour free-form window after the person's last message.

That now lives as data on `platform_capabilities.py` — `can_cold_open`, `reply_window_hours` — instead of as a rule in prose that each new call site has to remember.

## Schema

| Change | Kind |
| --- | --- |
| `member_reaches` | new table |
| `notifications` | new table |
| `agent_surface_conversation_links.last_inbound_at` | new column, backfilled from `updated_at` |
| `agent_surfaces.config.send_policy` | JSONB shape, back-compatible |

One revision: `0010_proactive_messaging`. The backfill is correct by definition — until that revision, only inbound events wrote that row, so `updated_at` *was* the last inbound time.

Untouched on purpose: `agent_messages` (`MessageKind.NOTIFICATION` already existed and is already replayed into model history, so persisting the outbound cost a write and not a migration), `agent_conversations` (`origin_type`/`origin_id` already carry a unique partial index), `agent_surface_external_users`, `workflow_run_waits`, and `schedules`.

## What is deliberately not here

**`Ask` — the unified request-for-input.** An agent can message a colleague and they can reply in their own thread. What is missing is the answer resolving back into the *asking* run. Lemma already has 80% of this and does not know it: `FormNodeConfig.assignee_pod_member_id` plus `workflow.run.waiting_assigned_to_me` is a per-person queue of pending questions that nothing pushes to, and `ask_user` is a third parallel mechanism for the same idea.

Folding all three into one entity is the right shape, and it carries a genuine hazard worth designing rather than discovering: **authority inversion.** B answers with data B can read; that value then sits in A's run context, which RLS never authorized A to see. The answer needs a read-check against the *asker*, not just a write-check against the answerer. That is a release of its own. Shipping it half-built is how the codebase ends up with a fourth parallel mechanism.

**Rate limiting** is modelled (`max_messages_per_recipient_per_hour`) but not enforced. The expected failure is a badly-prompted agent in a retry loop, not a malicious one — but there is no circuit breaker on the send path the way there is on schedules. Worth closing before `POD_MEMBERS` is switched on anywhere real.

**Delivery receipts.** Fan-out and retry want a row per attempt; a single-reach delivery does not, and a table written against a guess is worse than none. The consequence to know: `RedisSurfaceEventDedupStore` claims *inbound* messages only, so there is no outbound idempotency key and a worker retry can still double-post to a chat platform.

**`surface.send` is untouched.** It stays the surface-*specific* primitive — "reach this person on Slack" — while `notify` answers "reach this person". Collapsing them would silently change what an existing endpoint's path parameter means.

**Quiet hours.** Opt-out lives on the reach (`opted_out_at`). Anything time-of-day would need its own table; flagged now so it is not discovered later as a JSONB blob.

## Open questions

1. **Should a scheduled run notify by default?** Today it does not — a run must explicitly call `message_person` or use a `NOTIFY` node. That keeps a five-minute cron from turning the badge permanently red, at the cost of leaving a silent scheduled run as invisible as it was before. The alternative is notifying on failure only, which is probably the real answer.
2. **Does `notify` need a digest mode?** First-success-wins solves cross-channel spam; it does nothing about ten notifications from one workflow in one minute.
3. **Is `Notification` a `ResourceType`?** It is user-scoped, so probably not — but `Ask` almost certainly is, since it is assignable and permission-checked.

## See also

- [surfaces-into-agents.md](./surfaces-into-agents.md) — surfaces as a property of an agent (Truth 8 is superseded here)
- [schedules-into-triggers.md](./schedules-into-triggers.md) — where the "Runs as" identity model comes from
