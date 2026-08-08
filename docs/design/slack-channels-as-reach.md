# Slack reach is a channel, not a bot

**Status:** Implemented · **Surface area:** `lemma-frontend` only — no API, no migration

## The change in one sentence

On Slack, the thing an agent is reachable *at* is a channel, so the chips on an agent page become `#sales` `#support` `+ Add channel` instead of one `Slack` chip hiding a routing table nobody opens.

## Why

[Surfaces move into Agents](surfaces-into-agents.md) gave every platform the same journey: pick an identity, connect it, see proof. That shape is right for Telegram and WhatsApp, where the identity *is* the question — Lemma's number or your own, and once answered there is exactly one place the agent is reachable.

Slack inverts both halves. Its identity question has one answer, forever. Its reach is plural.

**1. Slack has no identity fork, but the UI shows one anyway.**
[`has_native_credentials`](lemma-backend/app/modules/agent_surfaces/services/credential_resolver.py:71) branches on WhatsApp, Telegram and Resend and returns `False` for everything else, so `supported_credential_modes` for Slack is `[CUSTOM]` on every deployment. Meanwhile [registry.ts:159](lemma-frontend/lib/surfaces/registry.ts:159) advertises *"Lemma's Slack app · Nothing to register yourself · **Fastest**"*, and [`blockedReason`](lemma-frontend/lib/surfaces/catalog.ts:71) renders it disabled as *"Not available on this deployment."* The option labelled **Fastest** can never be picked. The other option, *"Your workspace's own app · Your Slack app and branding"*, describes something that doesn't happen either — the flow installs **Lemma's** Slack app via OAuth. Both rows are wrong, and the step they live in has nothing to decide.

**2. The capability users actually want already ships, and is unreachable.**
[`SurfaceChannelRoute`](lemma-backend/app/modules/agent_surfaces/domain/entities.py:97) carries `agent_name`, and [`_resolve_route_agent`](lemma-backend/app/modules/agent_surfaces/services/ingress_service.py:1891) resolves it per inbound event. An org can run `#sales → sales-agent`, `#support → support-agent`, `#eng → eng-agent` behind one bot **today**. It is buried in a post-creation Configure step whose empty state reads *"No channels yet. Invite the Slack bot to one, then reopen this."* ([surface-configure-step.tsx:147](lemma-frontend/components/surfaces/surface-configure-step.tsx:147)) — a dead end offered to someone who has not been told why they'd want to.

**3. The chip disappears exactly when it should multiply.**
[agent-surfaces-row.tsx:53](lemma-frontend/components/surfaces/agent-surfaces-row.tsx:53) — `if (reached.has(platform)) return false` — hides a platform's connect chip once the agent has one. For an identity platform that is right: a second WhatsApp chip would mean a second number. For Slack it is backwards. Channels are additive, and this line is the single reason problem 2 is invisible.

**4. The one hard limit is silent.**
A DM has no channel, so `_resolve_route` finds nothing and falls through to `surface.agent_id` ([ingress_service.py:1912](lemma-backend/app/modules/agent_surfaces/services/ingress_service.py:1912)). One DM agent per workspace, org-wide. Nothing in the UI says so. Setting an agent as the Slack responder silently takes DMs away from whichever agent had them.

## On the word "channel"

The prior doc argues the row is labelled **Surfaces**, not "Channels", and reserves channel for *"a narrower job: an actual Slack or Teams channel, which a surface can route to a different agent."*

This proposal does not reopen that. It is that narrower job, taken literally. Nothing is renamed: `surface` stays the noun for *where an agent is reachable*, `SurfaceChannelRoute` stays the internal type. The claim is only that on Slack the surface has never been the unit a user manipulates — the channel is — and the chips should show the unit.

## Current state

| Piece | What it does today | Disposition |
| --- | --- | --- |
| [registry.ts:159](lemma-frontend/lib/surfaces/registry.ts:159) `identityOptions` for SLACK | Two rows, one permanently disabled, one factually wrong | **Replace** with a single install line |
| [agent-surfaces-row.tsx:53](lemma-frontend/components/surfaces/agent-surfaces-row.tsx:53) `reached.has(platform)` filter | Hides the chip after the first surface | **Branch** on `capabilities.channelRoutes` |
| [surface-configure-step.tsx:131](lemma-frontend/components/surfaces/surface-configure-step.tsx:131) `Channel routing` block | The real feature, post-creation only | **Promote** to the connect journey |
| [surface-connect-step.tsx:103](lemma-frontend/components/surfaces/surface-connect-step.tsx:103) account dropdown | *"Which slack workspace should this run on?"* → link to `/connectors` | **Keep**, but reached once per workspace, not per agent |
| [`SurfaceIdentityStep`](lemma-frontend/components/surfaces/surface-identity-step.tsx) for SLACK | Asks a question with one always-disabled answer | **Skip** when `channelRoutes` |
| `surface.agent_id` on a Slack surface | The DM route, unnamed and invisible | **Render** as a row named *Direct messages* |
| [setup_guides.py:108](lemma-backend/app/modules/agent_surfaces/domain/setup_guides.py:108) Slack Event Subscriptions action | Good copy, never emitted (gated on `ORG_CUSTOM`) | **Leave** — see *Out of scope* |

## Target information architecture

**Agent detail.** The reach row shows channels, and the plus never leaves.

```
Reachable   [#sales]  [#support]  [Direct messages]  [+ Add channel]  [Telegram ○]  [WhatsApp ○]
            ▲ a Slack channel routed to this agent   ▲ always offered once Slack is installed
```

**Another agent's page**, where DMs are already taken — the constraint renders as state, not as a failed save:

```
Reachable   [#eng]  [+ Add channel]  [Direct messages · Answered by sales-agent]
                                     ▲ disabled, names the holder, links to it
```

This is the pattern [catalog.ts:76](lemma-frontend/lib/surfaces/catalog.ts:76) already uses for a claimed system credential, applied to the one Slack constraint that is real.

**Pod level** (`/ai`). Slack rolls up as a routing table — channel → agent — which is the question an admin has and maps 1:1 to `config.channels`. Not a list of surfaces.

## The two moments, separated

Today both are crammed into one per-agent modal. They are not the same moment and do not recur at the same rate.

| | Install | Add a channel |
| --- | --- | --- |
| **Scope** | Once per Slack workspace, org-wide | Every time an agent should answer somewhere new |
| **Who** | Someone who can install a Slack app | Whoever owns the agent |
| **Where** | Connectors, or first run | The agent's reach row |
| **Asks** | nothing — it's an OAuth install | which channel, which agent |

After the install, "connect Slack" should never be asked again. The only Slack question left is *which channel*.

## Generalizing

The discriminator exists: `capabilities.channelRoutes` in [registry.ts](lemma-frontend/lib/surfaces/registry.ts:175).

- **`true`** (Slack, Teams) → identity is a one-time install; reach is N channels; the modal opens on a channel picker.
- **`false`** (WhatsApp, Telegram, Gmail, Outlook, Resend) → today's journey, unchanged. Identity is the choice; reach is one thing.

One branch on a flag already in the registry, not a Slack special case.

Telegram groups are the near-miss that stays on the identity side: they have no allow-list, because being added to the group *is* the authorization ([entities.py:510](lemma-backend/app/modules/agent_surfaces/domain/entities.py:510)). There is no channel to add, so there is nothing to pick.

## Truths the UI must convey

1. **One bot identity per workspace.** Every agent answers as the same `@Lemma`. Different agents, one face. Say it once at install; don't repeat it per channel.
2. **In a channel, an agent speaks only when mentioned or in a thread it joined** ([entities.py:518](lemma-backend/app/modules/agent_surfaces/domain/entities.py:518)). Already stated in the Configure block; it moves with the feature.
3. **One agent answers DMs, org-wide.** The only hard Slack limit, and today the only one that is invisible.
4. **A channel must have the bot in it.** Enumeration comes from `GET .../surfaces/{name}/channels`, which only sees channels the bot has joined — so "invite the bot first" is a real precondition, not a nag. It belongs *before* the empty picker, not as its empty state.
5. **A route naming a deleted agent silently falls back to the surface default** ([ingress_service.py:1906](lemma-backend/app/modules/agent_surfaces/services/ingress_service.py:1906)). Worth surfacing as a warning on the route row rather than leaving it to be discovered by a wrong answer.

## API: what exists, what's missing

**Sufficient today, entirely.** `GET /pods/{id}/surfaces/{name}/channels` ([surface_controller.py:478](lemma-backend/app/modules/agent_surfaces/api/controllers/surface_controller.py:478)) enumerates; the existing surface update writes `config.channels`; `_resolve_route_agent` already routes. Nothing to add, nothing to migrate.

That is the argument for doing this now rather than alongside the harder Slack work: it is a frontend change to make a shipped backend capability findable.

## What shipped

**Phase 1 — Tell the truth.** Slack's `identityOptions` is `null`, so the modal skips the identity step entirely and opens on the workspace picker. *Removes the permanently-greyed "Fastest" option — the single most confusing thing in the flow.*

**Phase 2 — Channels as chips.** `SurfaceChips` renders one chip per reach for a `channelRoutes` surface and an `Add channel` chip that never disappears. The chip carries an `add-channel` intent that opens the modal on routing with a blank row already assigned to the agent whose page you came from. The invite-the-bot precondition moved out of the empty state into the section itself.

**Phase 3 — DMs as a chip.** `DirectMessagesHeldChip` names the agent that holds a workspace's DMs and links to it, on every agent that doesn't. *Makes the one real limit legible.*

**Phase 4 — Pod roll-up, narrowed.** `/ai` counts **places** rather than installs — "3 places", with the tooltip naming them (`Direct messages · #sales · #deals`) — on both the agent cards and the Pod Assistant card. A full channel → agent table on `/ai` was **not** built: it needs the overflow answer in open question 3 first, and the count already fixes the roll-up's specific lie (one Slack workspace reading as "1 surface" when it reaches an agent in six places).

**Not in the plan, found while building.** `usePodAutomation.defaultSurfaces` filtered on `surfaceUsesDefaultAgent`, which asks who answers DMs. A workspace whose DMs belong to another agent but which routes `#general` to nobody in particular *does* reach the pod assistant, and was invisible there. It now filters on `surfaceReachesDefaultAgent`, symmetric with `surfacesForAgent`.

**Verification.** Typecheck, lint, design audit, 517 unit tests, and a production build all pass. New pure-logic tests cover the reach model (`lib/utils/__tests__/surfaces.test.ts`) and pin the registry invariant that a channel platform offers no identity fork. The change was **not** exercised in a browser: `make dev` needs `make init` to provision a database and secrets, and rendering these chips needs a real Slack workspace installed against a running stack.

## Open questions

1. **Where does the install live?** Connectors is correct by scope (org-wide, once) but invisible to someone standing on an agent page. A first-run interstitial inside the channel flow that hands off to connectors and returns may be worth the complexity — or may just be the account dropdown that exists. *Recommendation: ship Phase 2 with the existing dropdown, revisit if it's where people stall.*
2. **Should Teams follow immediately?** Same `channelRoutes: true` shape, but Teams also has admin consent, which is an identity-shaped question Slack doesn't have. The branch may not be as clean there.
3. **Does a channel chip belong on the agent page or the pod page?** Both are argued above. An agent in fifteen channels makes the reach row unreadable, and there is no overflow design.

## Out of scope

Anything that changes what Slack can do, as opposed to what users can find:

- **Genuine bring-your-own Slack apps.** Blocked on [`_verify_slack_signature`](lemma-backend/app/modules/agent_surfaces/services/webhook_security_service.py:279) HMAC-ing against one deployment-wide `SLACK_SIGNING_SECRET`, plus the shared `/surfaces/webhooks/slack` URL. Real backend work; own doc.
- **A second bot identity per workspace.** Requires the above.
- **Multiple agents on Slack DMs.** Needs an addressing mechanism that doesn't exist for any 1:1 platform.
- **Socket Mode.** `app_token` has no reachable source for a CUSTOM Slack surface, so the receiver skips. Separate bug.
- **`SLACK_BOT_TOKEN`** ([config.py:63](lemma-backend/app/modules/agent_surfaces/config.py:63)), declared and read nowhere. Delete it or wire it, in whichever doc takes on the above.
