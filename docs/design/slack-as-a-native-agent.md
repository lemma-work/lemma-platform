# Slack, natively

**Status:** Proposal · **Surface area:** `lemma-backend` (Slack platform, manifest, DM routing schema), `lemma-frontend`

## The change in one sentence

Lemma talks to Slack through the 2019 API — one `chat.postMessage(text=…)` per answer, a `⏳` message it edits and deletes, legacy mrkdwn — while Slack spent 2025–2026 building a first-class agent surface (streaming, thinking steps, markdown/table/card blocks, an agent messaging experience, a real search API) that we use none of; and every act of configuring Lemma still happens in a browser tab, when Slack now has the surfaces to do it where the user already is.

[Slack reach is a channel, not a bot](slack-channels-as-reach.md) made the shipped Slack capability *findable*. This doc is the other half it deferred: changing what Slack can do.

---

## Part 1 — What we ship today

The whole Slack surface is ~1,700 lines across six files. Read end to end, here is every Slack primitive we touch.

| Concern | What we do | Where |
| --- | --- | --- |
| **Ingress** | Accept `event_callback` of type `message` or `app_mention`. Everything else returns `None`. | [parser.py:36](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/parser.py:36) |
| **Egress** | `chat.postMessage(channel, text, thread_ts)`. No `blocks`. No chunking. | [service.py:135](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:135) |
| **Interactivity** | `block_actions` only, matched against two action ids (form submit, approval decision). | [parser.py:140](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/parser.py:140) |
| **Progress** | Post `⏳ <120 chars>`, `chat.update` it, `chat.delete` it at the end. | [service.py:316](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:316) |
| **Working indicator** | `assistant.threads.setStatus` in a DM if `assistant:write`, else an `:eyes:` reaction. | [service.py:252](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:252) |
| **Questions** | `input` blocks + a Submit button, posted **into the channel**. | [service.py:772](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:772) |
| **Approvals** | `section` + Approve/Deny buttons. | [service.py:836](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:836) |
| **Display resource** | `section` with a bolded title, up to four `>` lines, and a link button. | [service.py:877](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:877) |
| **Files** | `files.getUploadURLExternal` → PUT → `files.completeUploadExternal`. Correct and current. ✅ | [service.py:458](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:458) |
| **Agent tools** | Two: recent channel messages, and a substring scan over history. | [tools.py:21](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/tools.py:21) |
| **Channel routing** | `#channel → agent`, resolved per inbound event. Correct. ✅ | [ingress_service.py:1891](../../lemma-backend/app/modules/agent_surfaces/services/ingress_service.py:1891) |

The file-upload path and the channel-routing model are genuinely good. Everything else is the minimum that makes a bot reply.

---

## Part 2 — Defects found while reading

Bugs, not missing features. Several are cheap.

**1. The DM working-indicator can vanish entirely.**
`add_processing_indicator` tries `assistant.threads.setStatus` first in a DM. On `missing_scope`, `invalid_arguments`, or `method_not_supported_for_channel_type` it logs and **`return`s** — never falling through to the `reactions_add` fallback four lines below ([service.py:284](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:284)). Since the app declares no assistant/agent view at all (see #2), that error branch is the *likely* path, so DMs plausibly show no indicator whatsoever while the agent thinks. One-line fix: break out to the reaction instead of returning.

**2. The manifest subscribes to an event nothing handles, for a feature never enabled.**
`bot_events` includes `assistant_thread_started`, but the parser only accepts `message` and `app_mention`, so it is dropped. And `features` contains only `bot_user` — no `assistant_view`, no `agent_view` — so Slack does not consider Lemma an assistant and the event probably never fires. A dead subscription, and `assistant:write` paid for nothing.

**3. Private channels are half-wired, in three directions.**
`list_channels` asks for `types="public_channel,private_channel"` ([service.py:386](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:386)), but the bot has no `groups:read`, no `groups:history`, and `message.groups` is not subscribed. So a private channel can never deliver a message even if someone routed one. The bot does hold `groups:write` — write access to something it cannot see. Either support private channels properly (three scope additions, one event subscription) or stop asking for them in the picker. *Empirically the picker is not broken by this* — `conversations.list` tolerates the unauthorized type rather than rejecting the call, confirmed against a live workspace.

**4. `is_member` is fetched, typed, and never read.**
`list_channels` returns the flag; the frontend declares it in an interface and uses it nowhere ([surface-configure-step.tsx:25](../../lemma-frontend/components/surfaces/surface-configure-step.tsx:25)). A user can route a channel the bot isn't in and get silence. This is real but low-priority — Part 4.1 removes the picker from the primary path entirely, which is the better fix.

**5. No markdown → mrkdwn conversion on the Slack path.**
`rendering.py` is Telegram-only ([rendering.py:5](../../lemma-backend/app/modules/agent_surfaces/platforms/rendering.py:5)). Slack gets whatever the model emits, and we rely on a prompt fragment to keep it in mrkdwn. Every time the model slips into standard markdown, `**bold**`, `# Heading` and `| a | b |` render literally. Now entirely avoidable — see the `markdown` block below.

**6. The system prompt teaches the model a Slack that no longer exists.**
`_SLACK_FORMATTING` states: *"There are no headings or tables — use bold lead-in lines and bullet lists instead."* ([platform_capabilities.py:71](../../lemma-backend/app/modules/agent_surfaces/platforms/platform_capabilities.py:71)) Slack shipped the `markdown` block in February 2025 and the `table` block in August 2025. We actively instruct the model to produce worse output than the platform accepts.

**7. Channel search is a substring scan.**
`search_current_channel` pages `conversations.history` and runs `query in item.text.lower()`, bounded by `scan_limit` ([service.py:607](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:607)). No ranking, no semantics, no cross-channel reach, and it burns rate limit.

**8. We capture the `action_token` and never use it.**
`assistant_thread_action_token` is parsed out of every inbound event ([parser.py:85](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/parser.py:85)) and read nowhere in the repo. That ephemeral token is precisely what a bot token needs to call `assistant.search.context` — the fix for #7 is already in our hands.

**9. Slack is marked as unable to cold-open, but it can.**
`can_cold_open=False` for Slack, commented *"a Slack/Telegram/WhatsApp bot needs a prior interaction before it may DM"* ([platform_capabilities.py:49](../../lemma-backend/app/modules/agent_surfaces/platforms/platform_capabilities.py:49)). True for Telegram and WhatsApp; not for Slack, where `users.lookupByEmail` → `conversations.open` → `chat.postMessage` reaches any workspace member unprompted — and the manifest already requests `im:write` and `users:read.email`. So "an agent can reach a person" (#296) falls through to email on Slack-only workspaces. The blocker is structural, not just the flag: `notification_service`'s cold-open branch is hardcoded to `_email_channel` ([notification_service.py:343](../../lemma-backend/app/modules/agent_surfaces/services/notification_service.py:343)) and needs a real second branch. *Confidence high, but verify on a live workspace first — Slack has tightened DM rules before.*

**10. No message chunking on Slack egress.** A 12,000-character answer posts as one wall of text.

**11. Still true from the prior doc:** `SLACK_BOT_TOKEN` declared and read nowhere ([config.py:72](../../lemma-backend/app/modules/agent_surfaces/config.py:72)); Socket Mode has no reachable `app_token` source.

### Not a defect

The 44 user scopes in `oauth_config.scopes.user` are **intentional and load-bearing**. One Slack app serves both the surface and the Slack *connector* — the manifest's redirect URL is the connectors OAuth callback, and the connector derives account identity from `authed_user.id` / `authed_user.email` ([connector_service.py:1392](../../lemma-backend/app/modules/connectors/services/connector_service.py:1392), [account_identity.py:190](../../lemma-backend/app/modules/connectors/services/account_identity.py:190)). The user grant is what makes connector operations work. Worth revisiting only if Slack Marketplace listing is ever pursued, where consent-screen breadth is a review criterion.

---

## Part 3 — What Slack now offers that we do not touch

The pinned SDK is `slack-sdk==3.42.0`. Every method below **already exists in the client we ship** — verified by introspecting the installed package, not read off a changelog. Almost none of this needs a dependency bump.

### The one that changes the feel of everything: streaming

```
chat.startStream(channel, thread_ts, markdown_text=…, chunks=…, task_display_mode=…)
chat.appendStream(channel, ts, markdown_text=…, chunks=…)
chat.stopStream(channel, ts, markdown_text=…, blocks=…, metadata=…, chunks=…)
```

Today Lemma fakes this: post a placeholder, `chat.update` it under a 2-second rate limiter, then `chat.delete` it and post the real answer separately. Slack now has the real thing, with a proper streaming affordance in the client, and `slack_sdk.web.chat_stream` ships a lifecycle helper. This deletes `stream_progress` / `end_progress` and the delete-then-post race with them.

### Thinking steps — the feature Lemma already has the data for

Streaming accepts a `chunks` array. The SDK ships `MarkdownTextChunk`, `PlanUpdateChunk`, `TaskUpdateChunk`, `BlocksChunk`, plus `UrlSourceElement` and the matching `PlanBlock` / `TaskCardBlock`. `task_display_mode` picks **plan** (checklist declared upfront) or **timeline** (steps as they happen). Both collapse by default.

This is a native, collapsible rendering of exactly what `progress_observer` already computes and currently throws away — it truncates the whole thing to a 120-character `⏳` line ([progress_observer.py:38](../../lemma-backend/app/modules/agent_surfaces/services/progress_observer.py:38)). The agent's tool calls, sources, and plan become first-class Slack UI for free.

### Blocks we should be using

| Block | Shipped | What it replaces |
| --- | --- | --- |
| `MarkdownBlock` | Feb 2025 | The entire mrkdwn-conversion problem, and defects #5 and #6 |
| `TableBlock` | Aug 2025 | "There are no tables" — pod tables render as tables |
| `AlertBlock` | Apr 2026 | Errors currently posted as plain text |
| `CardBlock` | Apr 2026 | The section+link-button `display_resource` rendering |
| `CarouselBlock` | Apr 2026 | Multiple results |
| `FeedbackButtonsElement` / `ContextActionsBlock` / `IconButtonElement` | Oct 2025 | Nothing — 👍/👎 on every answer is a quality signal Lemma has no other source for |

`MarkdownBlock` alone is the highest value per line changed in this document: a one-line swap in `send_message` that deletes a prompt instruction and fixes a whole class of "why is the bot printing asterisks."

### The agent messaging experience

Adding `features.agent_view` (with `agent_description`) puts Lemma in Slack's agent surface: conversations render like real DMs, suggested prompts sit at the top of the Messages tab, and the app appears in the Agents & AI Apps rail. Events shift from `assistant_thread_started` to `app_home_opened` + `app_context_changed`.

Two SDK methods we ship and never call become meaningful: `assistant.threads.setSuggestedPrompts` (a new user is told what the pod can do instead of facing an empty box) and `assistant.threads.setTitle` (threads get named, so Slack's own history is navigable).

Caveats: `agent_view` is one-way (no revert), and it wants **slack-sdk ≥ 3.43.0** — we pin 3.42.0, so this is the one item needing a bump.

### Real-Time Search

`assistant.search.context` replaces defect #7 with keyword + semantic retrieval across messages, files, channels and users, with surrounding-context expansion. Bot tokens authorize it with the `action_token` we already parse and discard (#8). Granular `search:read.public` / `search:read.private` scopes let us search without asking for DM access.

### Surfaces we occupy zero of

- **App Home** (`views.publish`) — every Slack user opening Lemma sees an empty tab. See Part 4.2.
- **Modals** (`views.open` / `views.push`) — `ask_user` posts input blocks *into the channel*, so every half-answered form is a permanent artifact in the transcript. A modal is the correct container, and `view_submission` is unhandled today.
- **Slash commands and shortcuts** — no `/lemma`, no "Summarize this thread with Lemma" message action. The message shortcut is the cheapest possible first touch for a new user.
- **Link unfurling** (`link_shared` + `chat.unfurl`) — every Lemma URL pasted into Slack renders as a bare link. It should render as a card. This is the [share → import → remix loop](pod-bundle-share-import.md) running inside the tool where links actually get pasted.
- **`chat.postEphemeral`** — errors and authorization failures currently pollute the channel for everyone.
- **Message metadata** — a typed `metadata` payload correlates a message to a run, instead of smuggling a callback id inside a button's `value` string ([service.py:828](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/service.py:828)).
- **Canvases** (`canvases.create`, `conversations.canvases.create`) — long output belongs in a canvas, not a 12,000-character message (#10) or a file the user must download.
- **Slack lists** (`slackLists.*`) — a natural two-way mapping to pod tables.

---

## Part 4 — Three things Slack should own that a browser tab owns today

Part 3 is about making Lemma's *output* native. This part is about making its *configuration* native. Each of these replaces a trip to the web app with an exchange in the place the user already is.

### 4.1 — Setting up a channel starts with the invite

**Trigger.** `member_joined_channel` carries an **`inviter`** field. Slack tells us exactly who to ask, and they just took an intentional action, so their attention is already on this channel. Not currently subscribed. Note the parser already sees the weaker version of this — `subtype: channel_join` sits in the ignore set at [parser.py:44](../../lemma-backend/app/modules/agent_surfaces/platforms/slack/parser.py:44) — but that fires for every human join and does not cleanly give the inviter, so `member_joined_channel` is the right subscription.

**The exchange.** `chat.postEphemeral` to the inviter only, so the channel never sees setup chatter. One line, one button: *"I'm in. Who should answer here?"* Button → `views.open` a modal → pod select → on select, `views.update` fills the agent list → `view_submission` writes `config.channels[channel_id]`. Two dependent selects have to be a modal; Slack messages cannot cascade one select off another.

**Trap: `trigger_id` expires in 3 seconds.** The button handler must call `views.open` before anything else — no ingress, no dedup, no agent resolution. That is a fast lane the current webhook path does not have.

**Trap: identity is the hard part.** Slack `inviter` → Lemma user. `users:read.email` is granted, so `users.info` → email → the existing `identity_resolution_service`. Three outcomes, none of them silent:

| Resolution | Response |
| --- | --- |
| Lemma user with rights on a pod carrying this Slack surface | Open the modal |
| Lemma user, no rights | *"You're signed in as X but can't route agents here — ask \<owner\>."* |
| No match | *"Connect your Lemma account to set this up"* + link |

Authorization matters more here than in the web app. There, being on the page implies rights. Here, anyone in the workspace can drag the bot into a channel — so the route write must be authorized **as the resolved Lemma user**, not as the workspace.

**A free second entry point.** An @-mention in an unrouted channel today falls through to `surface.agent_id`, the DM agent, silently ([ingress_service.py:1912](../../lemma-backend/app/modules/agent_surfaces/services/ingress_service.py:1912)). Same ephemeral, same modal, aimed at the mentioner. One implementation, two doors.

**Why this beats fixing the picker.** The picker requires knowing Lemma has a Slack surface, opening the web app, finding the agent page, and having already invited the bot. The invite flow reverses the order — the invite *is* the trigger, asked in the place it happened, of the person who did it. It also makes defect #4 moot on the primary path, since an invite guarantees membership.

### 4.2 — In a DM, you pick who you're talking to

Today one agent answers DMs for an entire workspace ([ingress_service.py:1912](../../lemma-backend/app/modules/agent_surfaces/services/ingress_service.py:1912)). The prior doc listed this under *Out of scope*: *"Multiple agents on Slack DMs. Needs an addressing mechanism that doesn't exist for any 1:1 platform."*

**That is no longer true.** The modern API has three addressing mechanisms, and they compose:

1. **App Home** (`views.publish`) is a per-user, persistent, app-owned surface. *"You're talking to `sales-agent`. [Change]"* — a per-user default, replacing an org-wide one.
2. **Suggested prompts** (`assistant.threads.setSuggestedPrompts`) at thread start can literally be *"Ask sales-agent…"* / *"Ask support-agent…"*. Addressing as onboarding, for the user who never opens App Home.
3. **Per-thread binding.** Under `agent_view`, DMs are threaded. Each thread binds to its own agent, and `assistant.threads.setTitle` names it — so Slack's own DM history becomes a browsable list of agent conversations. This is strictly better than a per-user default, because a person talks to different agents about different things on the same day.

Add a `context_actions` overflow on the agent's own messages for the mid-conversation switch, and `/lemma agent <name>` for people who type.

**Schema.** The DM route is currently a single `surface.agent_id`. It becomes a three-tier resolution:

```
thread binding  →  user default  →  surface.agent_id
```

`surface.agent_id` stays as the floor, so nothing breaks and there is no migration for existing workspaces. `external_user_repository` is the natural home for the per-user default; the per-thread override keys on `thread_ts`.

**This obsoletes shipped UI.** `DirectMessagesHeldChip` ([agent-surfaces-row.tsx:238](../../lemma-frontend/components/surfaces/agent-surfaces-row.tsx:238)) exists to name the one agent holding a workspace's DMs and grey out every other agent. That constraint dissolves; the chip should be removed with this change, not left to contradict it.

### 4.3 — The pod app, pinned where people are

Telegram binds one pod app to the chat via `setChatMenuButton` with `type: web_app`, giving a persistent *"Open \<App\>"* button ([telegram_mini_app_service.py:88](../../lemma-backend/app/modules/agent_surfaces/services/telegram_mini_app_service.py:88)), configured by `surface.config.telegram.app_name`.

**Slack has no webview.** Nothing renders a Lemma app *inside* Slack the way a Mini App renders inside Telegram. That is the honest ceiling, and the design should not pretend otherwise. What Slack does have are two persistent, app-owned places to put the door:

- **Bookmarks** (`bookmarks.add` — both bot scopes already granted). Pins a labeled URL to a channel's bookmark bar: always visible at the top, per channel. The closest structural analogue to Telegram's menu button, and it fits 4.1 neatly — the setup modal that routes a channel can also offer *"pin this pod's app here."*
- **App Home** (`views.publish`). The per-user analogue, and the same surface 4.2 needs. The Telegram menu button lives in the DM with the bot; App Home *is* the DM-with-the-bot's own tab.

The reusable piece is `surface.config.<platform>.app_name`, which already exists for Telegram. Slack needs the same binding, resolved through the same `get_ready_pod_app_by_name`, rendered into a bookmark and an App Home button instead of a menu button. Links open a browser tab rather than an in-client view — worth saying plainly in the UI so nobody expects Telegram's behavior.

---

## What shipped

**A, B, C, D and most of E.** All of it verified against a real Slack workspace, which changed several designs — the notes below say where.

**A — bug sweep.** The DM indicator now falls through to the `:eyes:` reaction instead of returning, so a DM can no longer show nothing at all. The dead `assistant_thread_started` subscription is gone. Private channels are wired (`groups:read`, `groups:history`, `message.groups`) with a `missing_scope` fallback in `list_channels`. `SLACK_BOT_TOKEN` deleted.

**B + D — one change, not two.** Progress steps are *replacements* while `chat.appendStream` **appends**, so streaming raw progress text would have produced `SearchingReading`. Modeled as `task_update` chunks they become additive. Then the live workspace overturned two more assumptions:

- `chat.stopStream` **rejects** `markdown_text`. The answer rides `appendStream`; stop only finalises.
- A stream is chunk-based or plain-text **for its whole life**. Mixing them fails every append with `streaming_mode_mismatch` — silently, because the fallback looked almost right.

**Real token streaming.** `progress_observer` never looked at `AgentEventType.TOKEN`; it now streams text deltas (first delta immediate, then batched at 280 chars / 0.8s). A stateful `ThinkingStreamFilter` strips `<think>…</think>` across delta boundaries — per-delta stripping let `<thi` + `nk>` through and users watched the model reason. Slack no longer gets a step timeline at all: a step chunk appended into a live text stream lands *inside* the sentence being written.

**C — the agent messaging experience.** `slack-sdk` 3.43, `features.agent_view`, `assistant.threads.setTitle` / `setSuggestedPrompts`. Thread titles fire once, at conversation creation — which needed `_get_or_create_conversation_link` to return `(link, created_title)`, the only reliable signal that a turn started a fresh conversation.

**E — native configuration.** `ParsedSurfaceLifecycleEvent` as a third inbound contract. `member_joined_channel` → ephemeral to the inviter → modal → route saved → confirmation. A per-person DM agent picker (`dm_agent_by_user`), which removes the "one DM agent per workspace" limit the earlier doc called structurally impossible. An App Home tab with starter prompts, agent cards and per-viewer app cards. `list_ready_pod_apps` added alongside the single-app contract.

**The trigger_id trap, gotten wrong once.** Slack kills a `trigger_id` in ~3s. Ordering the handler first did nothing, because that handler runs in a *worker* behind a Redis queue. Modal opening now happens inside the HTTP request.

**Three states, three times.** "Pod assistant" was stored as an empty agent name, which is indistinguishable from *unconfigured* — so it silently resolved to the surface default. The same conflation recurred in egress display names and again in the DM choice. All three now distinguish explicitly.

**Verification.** ~73 new tests; **3559 passing**. Ten log events registered; a static contract test caught every one.

### Still to do

**F entirely** — link unfurling, `assistant.search.context` (channel search is still a substring scan; the `action_token` it needs is now carried), Slack DM cold-open.

No handler records feedback-button clicks. Nothing prevents **two surfaces claiming one Slack workspace** — found live, where routing was decided by row order across two orgs.

## Part 5 — Workstreams

This is not one project. Grouping it honestly:

**A. Bug sweep — days.** Defects #1, #2, #3, #10, #11. Independent of everything else.

**B. Make the output native — one change each, no schema, no new events.** `MarkdownBlock` in `send_message` and delete the stale prompt line (#5, #6). `chat.startStream`/`appendStream`/`stopStream` replacing the `⏳` hack. `TableBlock`. `FeedbackButtonsElement`. `chat.postEphemeral` for errors. **Start here** — highest ratio of user-visible change to risk in the document.

**C. Become an agent app — needs the SDK bump and a one-way manifest change.** `features.agent_view` + `agent_description`, `setSuggestedPrompts`, `setTitle`, App Home shell. Prerequisite for 4.2's better half.

**D. Thinking steps.** Wire `progress_observer`'s existing step data into `PlanUpdateChunk` / `TaskUpdateChunk`. Depends on B's streaming.

**E. Native configuration — the largest, and the one with real design and schema work.** 4.1 (invite flow) and 4.2 (DM agent choice). Both need: a third inbound-event category, `view_submission` handling, a `trigger_id` fast lane, Slack-user→Lemma-user authorization, and for 4.2 a DM-routing schema change plus removal of contradicting frontend. Multi-week. 4.3 rides along on both.

**F. Reach and distribution.** `link_shared` unfurling. `assistant.search.context` (#7, #8). Slack DM cold-open (#9). Each independently shippable.

### One architectural note that cuts across E

The Slack parser returns either a `ParsedInboundSurfaceEvent` (a message) or a `ParsedSurfaceInteraction` (a button tap). `member_joined_channel`, `app_home_opened`, `view_submission`, and `link_shared` are none of these — they are lifecycle and configuration events that never reach an agent. They want their own branch and their own contract, not special cases wedged into the message parser. Getting that boundary right early is what keeps E from turning into a pile of conditionals in `parse()`.

## Open questions

1. **Does `agent_view` fit multi-agent pods?** Slack's agent experience assumes one agent per app; Lemma routes N agents behind one `@Lemma`. Part 4.2 argues per-thread binding is the answer, but "which agent am I talking to right now" still has no native affordance beyond a thread title. `chat:write.customize` (already granted, already used for `username`) may cover the rest.
2. **Streaming versus our one-complete-reply prompt discipline.** `platform_agent_guidance` instructs the model to *"Send a single, complete reply when your work is done."* Streaming inverts that. The prompt fragment and the delivery mechanism have to change together or the model fights the surface.
3. **Should the DM agent picker generalize?** Telegram and WhatsApp have the same one-agent-per-1:1-surface limit. If the three-tier resolution in 4.2 lands in `ingress_service` rather than the Slack platform, they inherit it — but neither has App Home or threads to pick with.

## Deferred — researched, deliberately held

Both of these answer "you can add apps" literally, and both are held for now. The research is kept here so it does not have to be redone.

**Workflow Builder custom steps.** Declare `functions` in the manifest, handle `function_executed`, complete with `functions.completeSuccess` / `functions.completeError` (both already in our SDK). A Lemma agent or workflow becomes a **step any Slack admin can drag into a Slack workflow** — no code, no Lemma UI — triggered by a form submit or an emoji reaction. The largest distribution upside on this list, and the least clarity about where it belongs: it is inbound-triggered like a surface but has a typed input/output contract like a connector, and may be neither.

**MCP, in both directions.** Our manifest has `settings.is_mcp_enabled: false`. Slack's MCP server (GA February 2026, `mcp.slack.com`) exposes the workspace as tools — read channels, post, search, list users, react, create channels, read files. Flipping the flag would let Lemma-side agents use Slack's hosted tools instead of our hand-rolled two. In the other direction, the Slackbot MCP client can call an external MCP server, auto-discover its tools, and invoke them from a user prompt — making a Lemma pod callable from Slack without a Lemma bot in the conversation at all.

*Note:* deferring the Slack MCP server does not weaken workstream F's `assistant.search.context` work — that is a plain Web API method and needs no MCP.

## Out of scope, still

**Correction: bring-your-own Slack app was never blocked.** This doc inherited that claim from the earlier one and repeated it without checking. The per-surface webhook URL (`POST /surfaces/{id}/webhook`), the encrypted per-surface `webhook_secret` column, and the per-platform dispatch in `verify_surface_request` all already existed — Telegram and WhatsApp use them. Slack simply had no branch, so it fell through to the deployment-wide secret. That branch now exists, and a workspace can run its own Slack app.

A *second* bot identity in one workspace is still out of scope, and needs an addressing mechanism that does not exist for any 1:1 platform.

## Verification status

Every SDK method, chunk type, and block class named here was confirmed present in the installed `slack-sdk==3.42.0` by introspection. Platform behavior (dates, manifest properties, deprecations) comes from Slack's developer changelog and docs. The channel picker was confirmed working against a live workspace. **Nothing else here was exercised against a live Slack workspace** — in particular defect #1's error path and #9's cold-open want a real workspace before anyone builds on them.
