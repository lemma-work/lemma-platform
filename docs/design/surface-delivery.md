# Surface delivery

## The model

A surface conversation has exactly one outbound seam: the agent produces
**envelopes**, and the platform renders them. An envelope is what a person
receives in one go — text, files, choices, a decision to make. How many
envelopes a run may produce, and who closes the last one, is a property of the
platform read from the capability registry. It is never a second code path.

Everything in this document follows from that one sentence, and the current
implementation violates it in three separate places.

The product contract already says this. `docs/product/journeys/surfaces-and-notifications.md`
opens with two rules, and the second is the one at issue:

> **Every platform gets the full product**: asking a question, approving an
> action, sending a file — if it works in the workspace it works on the surface,
> natively where the platform supports it and as plain text where it does not,
> but never dropped.

PS-SURF-021 ("Questions and approvals work on every platform") is marked
**covered**. It is not covered. The gap is not a missing feature — it is that
there is no seam where "every platform gets X" could be enforced, so each
platform got X separately, and four of them got most of it.

---

## Where it is now

### The delivery matrix

Eighteen outbound verbs on the adapter port, seven platforms, 126 cells. The
base class answers an unimplemented cell with `return False`, `return None`, or
nothing at all — so a hole is indistinguishable from a platform that declined.

| | Slack | Teams | Telegram | WhatsApp | Gmail | Outlook | Resend |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `send_message` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `send_questions` (native) | ✅ | ✅ | ✅ | ✅ | — | — | — |
| `send_approval` (native) | ✅ | ✅ | ✅ | ✅ | — | — | — |
| `acknowledge_interaction` | ❌ | ❌ | ✅ | ❌ | n/a | n/a | n/a |
| `send_file_attachment` | ✅ | ❌ | ✅ | ✅ | — | — | — |
| `send_voice_note` | ❌ | ❌ | ✅ | ❌ | n/a | n/a | n/a |
| `send_display_resource` | ✅ | ✅ | ✅ | ✅ | 💀 | 💀 | 💀 |
| `stream_progress` | ✅ | ✅ | ✅ | ✅ | n/a | n/a | n/a |
| `append_stream_text` | ✅ | — | — | — | n/a | n/a | n/a |
| `send_cold_email` | n/a | n/a | n/a | n/a | — | — | ✅ |

`—` is a deliberate decline the design accounts for (email delivers files
through the reply tool's `attachment_paths`, not as a chat attachment). `❌` is a
hole that degrades silently. `💀` is implemented and unreachable: all three email
adapters have a working `send_display_resource`, and the tool never calls it
because [user_interaction/pydantic_adapter.py:143](../../lemma-backend/app/modules/agent/tools/user_interaction/pydantic_adapter.py:143)
returns early for email. Three live implementations of a method nothing invokes
is the clearest single symptom of the problem this document is about: with
eighteen verbs and no seam, nobody can tell which cells are load-bearing.

### What the matrix costs, concretely

**`acknowledge_interaction` is implemented once.** Only Telegram overrides it
([telegram/service.py:277](../../lemma-backend/app/modules/agent_surfaces/platforms/telegram/service.py:277));
Slack, Teams and WhatsApp inherit the base no-op at
[base.py:143](../../lemma-backend/app/modules/agent_surfaces/platforms/base.py:143).
On those three, tapping Approve produces no confirmation, does not clear the
buttons, and — because the catch-all at
[surface_interactions.py:241](../../lemma-backend/app/modules/agent_surfaces/services/surface_interactions.py:241)
routes every exception into that same no-op — a *failed* submission is
completely silent while the run stays `WAITING` forever. The three states
Telegram can express (`expired`, `other`, "I couldn't complete that") exist
nowhere else: `interaction_state` is only ever set by the Telegram parser.

**A typed reply during a pending approval is a denial by default.**
[pending_interaction_resume.py:106](../../lemma-backend/app/modules/agent_surfaces/services/pending_interaction_resume.py:106)
matches the whole stripped string against ten English words. Everything else is
`DENY`, and [surface_inbound_message.py:154](../../lemma-backend/app/modules/agent_surfaces/services/surface_inbound_message.py:154)
consumes the message so it never becomes a new instruction. "yeah go ahead" is
a denial. "wait, why do you need that?" is a denial with the question thrown
away. This is the single worst user-facing behaviour in the system.

**`approve for session` does not survive the text fallback.**
[models.py:191](../../lemma-backend/app/modules/agent_surfaces/domain/models.py:191)
offers only approve/deny, and the typed-reply parser has no session vocabulary.
Any platform where the native render fails silently loses a decision the web
client has offered since day one, and the agent re-prompts for every repeat of
the same action.

**`display_resource` returns success on email and delivers nothing.**
[user_interaction/pydantic_adapter.py:143](../../lemma-backend/app/modules/agent/tools/user_interaction/pydantic_adapter.py:143)
returns early for email — after the tool has already returned
`success=True, "FILE resource ready for display."` The model believes it
delivered the file. The docstring claiming the observer folds these into the
email reply is stale; [progress_observer.py:123](../../lemma-backend/app/modules/agent_surfaces/services/progress_observer.py:123)
says explicitly that it no longer handles `display_resource` at all. Compare
`ask_user` and `request_approval`, which fail fast on email through the shared
`surface_can_pause_for_a_person` predicate. The correct pattern exists; one tool
never adopted it.

**"Never swallowed" is a `logger.debug`.**
[progress_waiting.py:124](../../lemma-backend/app/modules/agent_surfaces/services/progress_waiting.py:124)
comments that a prompt reaching nobody must be surfaced loudly, then logs at
debug. A run stuck `WAITING` because delivery failed is invisible at INFO.

**WhatsApp's reply window is enforced on one path out of two.**
`reply_window_hours` is checked only in
[notification_delivery.py:232](../../lemma-backend/app/modules/agent_surfaces/services/notification_delivery.py:232).
A run that pauses for approval more than 24 hours after the person's last
message has both its native render and its text fallback refused by Meta, and
the only trace is the debug line above.

---

## The three axes that got conflated

### 1. Control and transport

Email genuinely needs different **control**. The agent must compose one whole
reply, so the agent decides when the turn's output is final. Chat does not care;
the observer can decide. That is a real difference and the right call.

But it was implemented as an entire parallel **transport**. The email reply tool
does not merely choose the moment — it independently resolves recipient,
subject, `In-Reply-To`, `References` and attachments, and calls the provider
itself. So "the agent's answer reaches the person" now has five implementations:

1. Chat, non-Slack — observer buffers text → `send_agent_message_for_conversation` → `adapter.send_message`
2. Slack — the answer *is* the stream: `_finish_stream_with_answer` → `append_stream_text` + `finish_progress`, never touching `send_message`
3. Email, agent-driven — `gmail_reply_email` / `outlook_reply_email` / `resend_reply_email`, three tools over three `reply_email` service methods
4. Email, observer fallback — `adapter.send_message` → `_send_email`, a fourth composition path
5. Cold email — `send_cold_email` → `_send_email`, a fifth

Paths 3 and 4 read *different stores for the same four facts*. The agent tool
reads `ctx.deps.surface_metadata`, populated from the conversation's
`surface_event_metadata` ([resend/service.py:292](../../lemma-backend/app/modules/agent_surfaces/platforms/resend/service.py:292)).
The observer fallback reads `event.reply_target`, populated from the link's
`last_event` ([resend/service.py:87](../../lemma-backend/app/modules/agent_surfaces/platforms/resend/service.py:87)).
When they drift, the fallback threads to a stale message, the human's reply
carries a different `References` root, and the next inbound mints a **new
conversation**. Silent history loss, no error anywhere.

The table states the conflation plainly:

| | who decides when to send | how bytes reach the platform |
| --- | --- | --- |
| Chat | observer | `adapter.send_message` |
| Slack | observer (streaming) | stream API |
| Email | **the agent** | **the reply tool's own sender** |

The email row changed both columns when it only needed to change the first.

### 2. Thread shape and platform family

`SurfaceMode` has exactly two members, `DM` and `EMAIL`
([entities.py:51](../../lemma-backend/app/modules/agent_surfaces/domain/entities.py:51)),
and defaults from `surface_type.is_email`
([entities.py:327](../../lemma-backend/app/modules/agent_surfaces/domain/entities.py:327)).
There is no `CHANNEL` member, so a channel-capable surface is *necessarily* `DM`
however it is configured. Routing meanwhile produces three `conversation_kind`s —
`DM`, `CHANNEL`, `EMAIL` ([surface_routing.py:283](../../lemma-backend/app/modules/agent_surfaces/services/surface_routing.py:283)).
Nothing reconciles the two enums, and the conversation lifecycle gates on the one
that cannot express the distinction.

The axis that actually matters is not platform family. It is whether a thread id
is **multiplexed** or **topic-scoped**:

- A chat DM carries every conversation you will ever have on one permanent
  thread id. It needs a TTL to cut it into conversations.
- An email thread *is* one topic and gets a fresh id per topic. A TTL on it is
  meaningless.
- A channel thread is topic-scoped like email — but sits on the chat code path.

So [surface_conversation_links.py:136](../../lemma-backend/app/modules/agent_surfaces/services/surface_conversation_links.py:136)
applies the DM reset (default 24h) to Slack and Teams **channel threads**, because
the surface's mode is `DM` for the whole install. Reply in a Slack thread a day
later and the human sees the entire thread while the agent gets a fresh
conversation with no history — partially papered over by a 15-message
`fetch_thread_context` that the prompt explicitly instructs the agent to treat
as untrusted background and not act on. The agent's own prior turns come back as
text it is told to distrust.

The same guard runs the other way for email: the agent-change checks at lines
137–143 sit *inside* the `mode is DM` branch, so an email conversation never
resets for any reason. Re-routing to a different agent keeps the old one. No TTL
is correct for email; skipping the agent-change check along with it is not.

### 3. Delivery and chrome

Five of the eighteen verbs are not conversation delivery at all:
`publish_home_view`, `open_channel_setup_modal`, `open_dm_agent_modal`,
`send_starter_prompt`, `send_channel_setup_prompt`, `set_thread_title`. They are
setup and platform chrome. They inflate the port and make every non-Slack
platform read as half-implemented, when the truth is that Slack has an App Home
and nobody else does.

Two more paths bypass the adapter layer entirely: the Telegram manager bot calls
`sendMessage` raw at [telegram_manager_service.py:261](../../lemma-backend/app/modules/agent_surfaces/services/telegram_manager_service.py:261)
and [telegram_manager_updates.py:209](../../lemma-backend/app/modules/agent_surfaces/services/telegram_manager_updates.py:209).

---

## The model, stated

### One envelope

The adapter port keeps three outbound verbs:

```
deliver(envelope)      -> DeliveryReceipt
progress(update)       -> None
acknowledge(interaction, outcome) -> None
```

An **envelope** is one thing a person receives, carrying any combination of:

| Part | Rendered natively where possible, degraded where not |
| --- | --- |
| `text` | markdown, converted per `markdown_mode` |
| `files` | attachment under the cap; link into Lemma over it |
| `choices` | buttons / cards / inline keyboard; numbered text list otherwise |
| `decision` | approve / approve-for-session / deny controls; text prompt otherwise |
| `resources` | native preview; link otherwise |

`send_questions`, `send_approval`, `send_voice_note`, `send_file_attachment` and
`send_display_resource` stop being verbs and become envelope *contents*. This is
the change that matters most: "does this platform support choices" becomes a
render-time degradation inside one method with one guaranteed fallback, instead
of an unimplemented method whose default answer is `False`.

They survive as `_render_*` hooks a platform overrides and only `deliver` calls
— Python cannot express "protected", so two tests do: one asserts the port
exposes no public verb for a kind of content, the other statically scans the
module for a direct call to a hook. `send_message` and `send_cold_email` stay
public deliberately. The first is the text primitive every degradation lands on
and the only way to speak before a conversation exists; the second opens a
thread rather than landing in one, so it has no envelope to belong to.

Callers stop *attempting* delivery to learn what happened. Resolving a pod file
returns the envelope parts it becomes plus the facts a card needs, so an
oversize file is a card from the start rather than a failed attachment patched
after the fact — and a PDF's page image leads the same envelope instead of
racing the document as its own send.

An envelope is delivered or it raises. There is no third outcome, and no caller
has to remember to check a boolean.

### Cardinality is a capability, not a code path

`PlatformCapabilities` already carries `progress_style`. It gains a sibling:

```
delivery_cardinality: MANY | ONE
```

`MANY` — chat. The observer emits envelopes as the run produces them.
`ONE` — email. Envelopes accumulate into a single buffer, flushed once at run
end. Everything an agent would have sent as a separate message becomes a part of
the one reply: `display_resource` files become attachments, an `ask_user` becomes
a numbered list in the body, narration becomes the opening paragraph.

The email reply tool stops being a transport. It becomes what it always was in
substance: **the agent filling in the single envelope's body and attachments**,
then ending its turn. Same sender, same threading coordinates, one source of
truth. Finding 1's two-metadata-store drift disappears because there is one
send path to drift from.

This also resolves `display_resource` on email honestly. Today it returns
success and delivers nothing. Under `ONE` it attaches to the pending envelope
and the return value is true.

### Interactivity is a capability, not a prohibition

`ONE` cardinality does not mean "no questions". It means the question cannot
hold the run open, which is a different statement. Today `ask_user` and
`request_approval` fail fast on email and the agent is told to guess a default —
so a genuinely destructive action on an email surface either happens unapproved
or does not happen at all, and the person is never asked.

The envelope model gives a third option that is strictly better and costs one
field: `can_pause` stays false for email, but a `decision` part still renders —
as a pair of links into Lemma that resolve through the same
`resolve_user_approval_internal` endpoint every other surface uses. The run ends
after the reply, the person clicks in Lemma, and the decision starts a fresh run
exactly as a Slack button tap does. Email gets approvals without email gaining
the ability to block a worker.

### Acknowledgement is mandatory

`acknowledge` has no default implementation. A platform that parses interactions
must implement it, enforced by the architecture gate rather than by review. Its
outcomes are a closed set — `accepted`, `expired`, `not_yours`, `failed` — and
each maps to something the person sees. The failure path in
`handle_interaction` stops being a silent catch: an interaction that cannot be
resolved says so where it was tapped.

### Chrome is a separate protocol

Setup, App Home, modals and thread titles move to an optional
`SurfaceChromePort` that a platform may implement or not. The delivery port
stops reporting them as gaps. The Telegram manager bot goes behind the same
port rather than calling the API raw.

---

## Conversation lifecycle

`SurfaceMode` is replaced by a field that names the thing it actually decides:

```
thread_shape: MULTIPLEXED | TOPIC_SCOPED
```

`MULTIPLEXED` — a chat DM. Subject to `dm_conversation_reset_after_hours`.
`TOPIC_SCOPED` — a channel thread, an email thread. Never TTL-reset.

The reset check reads `conversation_kind`, not the surface's platform family.
That single change fixes the channel-thread amnesia and stops the next person
hitting the same conflation. The agent-change reset moves *outside* the shape
branch, because "this thread now routes to a different agent" is a reason to
start fresh on every shape, email included.

Cold email keeps its asymmetry — it is the one case where Lemma opens a thread —
but stops leaving a half-built conversation behind. Today
[notification_egress.py:151](../../lemma-backend/app/modules/agent_surfaces/services/notification_egress.py:151)
writes the link only and never sets the conversation's `surface_platform`
metadata, and since [tool_assembler.py:150](../../lemma-backend/app/modules/agent/tools/tool_assembler.py:150)
builds the surface toolset off exactly that key, an agent running in a
cold-opened conversation has no reply tool and no platform guidance until the
person writes back. Opening a thread writes both halves or neither.

---

## Identity

The first product rule is "a surface is a door, not a hole". Email is currently
a hole.

Inbound email identity is unauthenticated. Grepping the module for
`dmarc|dkim|spf|Authentication-Results` returns nothing. The Svix signature check
at [webhook_security_service.py:295](../../lemma-backend/app/modules/agent_surfaces/services/webhook_security_service.py:295)
proves the payload came from Resend; it says nothing about whether the `From:`
inside it is real. That header is taken at face value, matched to a Lemma user
by address, and the run executes under `build_user_context(user_id=...)` — that
member's full RLS scope, tables and connectors.

Chat platforms do not have this hole: the sender id is asserted inside a signed
platform payload. Email's is a string a stranger typed.

Three outcomes, not two, because "the header says the sender failed" and "there
is no header" are different facts and only one of them is an attack:

- **FAIL** — the receiving service evaluated the message and it did not pass.
  Never resolved to a user, under any configuration. The sender is anonymous
  and gets what an unknown Slack user gets: the signup path in
  `fallback_reply_service`, not a run.
- **PASS** — DMARC passed, or SPF/DKIM passed *aligned to the `From:` domain*.
  An unaligned pass is worth nothing; a bulk mailer's valid signature says
  nothing about the line a person reads.
- **UNKNOWN** — no usable header. This is a deployment fact rather than a
  security one, so `SURFACE_EMAIL_ALLOW_UNAUTHENTICATED_IDENTITY` decides. It
  defaults to permitting resolution and logging
  `email_sender_unauthenticated.degraded`, because whether a given provider
  supplies the header could not be confirmed from the repository, and defaulting
  the other way would stop every inbound sender resolving on a deployment where
  it is absent. Operators watch that line, then set it `False`.

The check reads every `Authentication-Results` occurrence itself rather than
going through `header_map`, whose dict is last-wins. Anyone can put that header
in a message they send; the receiver strips untrusted copies and prepends its
own, so the *first* is the real one and a last-wins map would hand a forged copy
the final say. `SURFACE_EMAIL_TRUSTED_AUTHSERV_IDS` names the receivers whose
word is taken, which is the guarantee RFC 8601 actually intends — without it
only the first header is read, which is weaker but not nothing.

The gate sits **above** the resolution cache, and that placement is the whole
control: on an email surface the `external_user_id` *is* the `From:` address, so
a spoofed message from an address that resolved once before takes the cache-hit
return and never reaches a check placed alongside the other matches.

One more, unrelated to spoofing:
[user_repositories.py:79](../../lemma-backend/app/modules/identity/infrastructure/user_repositories.py:79)
gains the `is_active` / `is_deleted` filters that the phone lookup ten lines
below it already has. A departed member's address resolved to their user, and
that match is an authority grant.

---

## What the person should see

The point of the envelope model is that this table is derivable rather than
hand-maintained. Each row is the same envelope; the differences are all
capability lookups.

| | Slack | Teams | Telegram | WhatsApp | Email |
| --- | --- | --- | --- | --- | --- |
| While working | streamed answer | live edited message | one-line chip | rationed post | nothing |
| The answer | closes the stream | replaces progress | replaces progress | new message | the one reply |
| A question | block buttons | adaptive card | inline keyboard | buttons / list | numbered list in the reply |
| An approval | 3 buttons | 3 buttons | 3 buttons | 3 buttons | two links into Lemma |
| After tapping | control clears, "Approved" | same | same | same | n/a |
| A file under cap | attachment | link (no upload API) | attachment | attachment | attachment |
| A file over cap | Lemma link | Lemma link | Lemma link | Lemma link | Lemma link |
| A chart | PNG attachment | link | PNG attachment | PNG attachment | inline image |
| Typed "sure" mid-approval | approved | approved | approved | approved | n/a |
| Typed a question mid-approval | delivered as a message | same | same | same | n/a |

The last two rows are the behaviour change that people will actually feel. A
typed reply is classified into `approve` / `deny` / `neither`, and `neither`
falls through to the normal message path instead of being a silent denial. The
run is superseded by the new message exactly as
`supersede_stale_pending_interactions` already handles, and the agent gets the
person's actual words.

---

## What has to stay true

- **One delivery seam.** Nothing outside a `SurfacePlatformAdapter` calls a
  platform API. The two Telegram manager call sites are the current violations.
- **No silent decline.** `deliver` either delivers or raises. A capability the
  platform lacks is degraded inside the render, never returned as `False` for a
  caller to interpret.
- **Every envelope part has a text degradation.** This is what makes "every
  platform gets the full product" enforceable rather than aspirational: the
  fallback is a property of the *part*, defined once, not of the platform.
- **A failed delivery to a waiting run is an incident, not a debug line.** A run
  in `WAITING` whose prompt reached nobody is the one state a person cannot act
  on and cannot see.
- **Identity is asserted by something signed.** A platform user id inside a
  verified webhook, or a DMARC-aligned `From:`. Never a bare header.

An architecture check should hold the first two: the adapter port's outbound
verb count is the metric that regressed here, and it can be ratcheted the same
way `architecture-baseline.json` already ratchets file size and broad catches.

---

## Migration

The order matters, because each step removes a reason the next one is hard.

1. **Fix the denial default.** Classify typed replies three ways and let
   `neither` fall through. One file, no interface change, and it removes the
   worst live behaviour immediately.
2. **Make `acknowledge` mandatory** and implement it for Slack, Teams and
   WhatsApp. Also one file each; closes the silent-failure class.
3. **Introduce the envelope** alongside the existing verbs. `deliver` starts as
   a composition over what is already there, so no platform changes on day one.
4. **Move the five content verbs inside it**, deleting them from the port as
   each platform's render learns the part.
5. **Add `delivery_cardinality`** and rewrite the email reply tool to fill the
   pending envelope. This is the step that deletes paths 4 and 5 above and the
   second metadata store with them.
6. **Rename `SurfaceMode` to `thread_shape`** and move the reset check onto
   `conversation_kind`.
7. **Split the chrome port** and move the Telegram manager behind it.
8. **Gate email identity on DMARC.** Independent of the rest; can land any time,
   and should land before the rest if the deployment already accepts public
   inbound mail.

Steps 1, 2 and 8 are bug fixes and should not wait for the redesign.

---

## Naming

One noun per concept, and two are currently doubled:

- **Envelope** — one thing a person receives. New, and the only new noun here.
- **Plan** is used for two unrelated things: `SurfacePlan` is the agent's
  `write_todos` checklist ([progress_plan.py](../../lemma-backend/app/modules/agent_surfaces/services/progress_plan.py)),
  while `SurfaceQuestionRenderPlan` and `SurfaceApprovalRenderPlan` are render
  instructions ([domain/models.py](../../lemma-backend/app/modules/agent_surfaces/domain/models.py)). The
  render plans become envelope parts and the name goes with them; `plan` is left
  meaning the checklist, which is what a person would guess.
- **Mode** is used for four things already (`SurfaceMode`, `SurfaceEventMode`,
  `SurfaceCredentialMode`, `markdown_mode`). `thread_shape` deliberately avoids
  a fifth.

---

## Known debt this does not address

- **`snooze` is invisible on every surface.** It is a pausing tool like the other
  two, but [progress_waiting.py:43](../../lemma-backend/app/modules/agent_surfaces/services/progress_waiting.py:43)
  handles only `ask_user` and `request_approval`, so a sleeping agent looks
  identical to a dead one. Whether it *should* be visible is a product question,
  not a bug, which is why it is here rather than above.
- **Channel approvals may be open to anyone.**
  [interaction_helpers.py:38](../../lemma-backend/app/modules/agent_surfaces/services/interaction_helpers.py:38)
  returns true when either external user id is empty, so in a channel whose link
  carries no user id, any member can approve a privileged action. Needs a product
  decision about who may approve in a channel before it can be fixed.
- **Permission denials remain model-discretionary.** A 403 comes back as a
  `needs_approval` tool error ([tool_errors.py:38](../../lemma-backend/app/modules/agent/tools/tool_errors.py:38))
  and the model must *choose* to call `request_approval`. Nothing forces the
  prompt. The envelope model makes the ask deliverable everywhere, including
  email — it does not make it mandatory.
- **~110 hardcoded `SurfacePlatform.X` branches** remain across 25 files outside
  the capability registry, 14 in `event_receiver_service.py` alone. The registry
  was the right idea and roughly half the module never migrated to it. Each is
  cheap to move; there are just a lot of them.
