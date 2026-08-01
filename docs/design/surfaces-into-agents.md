# Surfaces move into Agents

**Status:** Implemented · **Surface area:** `lemma-frontend` (bulk) + three backend gaps in `app/modules/agent_surfaces`

## The change in one sentence

A surface stops being a pod-level *place you visit* and becomes a *property of an agent* — "where this agent is reachable" — configured through a modal that knows the platform it is talking about.

## Why

Three problems, one root cause.

1. **Two mental models for one idea.** "Surfaces" is currently a nav rail item, a route, a resource ledger, a chip row on every agent, and a side sheet. The agent page already renders the answer a user wants (`Surfaces · [Telegram] [WhatsApp] [+]`) but every action on it links away to `/pod/{id}/surfaces`. The link-away is the bug: the user was already looking at the thing they wanted to change.
2. **One generic form for seven platforms.** [`pod-channels-panel.tsx`](lemma-frontend/components/pod/pod-channels-panel.tsx) renders a single dialog whose fields are toggled by four `platformSupports*` predicates. It shows a "Surface name" input and a "Default responder" select before it shows anything platform-specific, and it never shows the *journey* — the Telegram bot hand-off, the Meta webhook config, the fact that Lemma's shared number can only be claimed once. Those journeys are where users drop.
3. **The setup story is told after the fact.** `SurfaceSetupSection` only renders for an *existing* surface (`{editingSurface ? <SurfaceSetupSection/> : null}`), so the steps that make the surface actually work appear only after the user has already saved something that doesn't work yet.

## Current state

| Piece | What it does | Disposition |
| --- | --- | --- |
| [workspace-sidebar.tsx:392](lemma-frontend/components/pod/workspace-sidebar.tsx:392) | "Surfaces" nav rail item | **Delete** |
| [app/pod/[id]/surfaces/page.tsx](lemma-frontend/app/pod/[id]/surfaces/page.tsx), [surfaces/new/page.tsx](lemma-frontend/app/pod/[id]/surfaces/new/page.tsx) | Route shells around the panel | **Delete**, replace with redirect to `/pod/{id}/ai` |
| [app/pod/[id]/channels/page.tsx](lemma-frontend/app/pod/[id]/channels/page.tsx) | Legacy redirect → `/surfaces` | **Retarget** to `/ai` |
| [pod-channels-panel.tsx](lemma-frontend/components/pod/pod-channels-panel.tsx) (1364 lines) | Ledger + generic config dialog + setup + send dialog | **Split** — see *Modal design* |
| [inline-channel-form.tsx](lemma-frontend/components/pod/inline-channel-form.tsx) | "Connect new / Route existing" sheet; *Connect new* is a grid of links to the surfaces page | **Replace** — "Connect new" becomes the real modal |
| [resource-automation.tsx:80,130](lemma-frontend/components/pod/resource-automation.tsx:80) | `SurfaceIdentityChip` / `SurfaceConnectChip` on agent pages | **Keep**, rewire `onRoute`/`manageHref` to open modals |
| [lib/utils/surfaces.ts](lemma-frontend/lib/utils/surfaces.ts) | Platform meta, status, reach, deep links | **Keep and extend** — becomes the shared registry |
| [pod/[id]/layout.tsx:106,156,226](lemma-frontend/app/pod/[id]/layout.tsx:106) | Section/tab resolution for `surfaces` \| `channels` | **Prune** |
| [lib/pods/workspace-tabs.ts:334](lemma-frontend/lib/pods/workspace-tabs.ts:334) | `surfaces` workspace tab kind | **Prune** (+ its test) |
| [pod-permissions.ts:92](lemma-frontend/lib/authz/pod-permissions.ts:92) `surfaces: ['agent.update','connector_account.manage']` | Route gate | **Keep as a capability gate**, no longer a route gate |

Three surfaces of the app consume this today and all three keep working, better: the agent detail page ([agents/[agentId]/page.tsx:329](lemma-frontend/app/pod/[id]/agents/[agentId]/page.tsx:329)), the pod default assistant page ([ai/assistant/page.tsx:99](lemma-frontend/app/pod/[id]/ai/assistant/page.tsx:99)), and the agents index roll-up ([ai/page.tsx:73](lemma-frontend/app/pod/[id]/ai/page.tsx:73)).

## Target information architecture

**Agent detail — the Surfaces row becomes the whole manager.**

```
Surfaces   [@acme_ops_bot ·Live]  [+1 555… ·Needs setup]  [Slack ○]  [Teams ○]  [Gmail ○]  [+]
           ▲ live chip → modal, Configure state            ▲ faded chip → modal, Connect state
```

The row is labelled **Surfaces**, not "Channels". The product already has one
noun for this, and a second one for the same idea is what made the old IA read
as two features. "Channel" keeps a narrower job: an actual Slack or Teams
channel, which a surface can route to a different agent.

A live chip opens its platform's modal already configured. A faded chip opens the same modal on its connect journey. No route change, no side sheet, no ledger page.

**Pod default assistant** (`/ai/assistant`) gets the identical component with `reachFor = null` — surfaces with no explicit responder.

**Agents index** (`/ai`) keeps the roll-up line it already has (`defaultSurfaceCount`) and gains a per-agent reach column, so "who is reachable where" is answerable without opening an agent.

---

# Modal design

The generic dialog isn't rearranged — it's replaced. Today's dialog is a settings page in a box: `max-w-2xl`, eight stacked sections, everything visible at once, ordered by implementation convenience (name → responder → identity → account → filters → routes → proactive → setup). The user's actual question is *"how do I make this agent reachable on Telegram"*, and the first thing the dialog asks is what to call it.

## Principles

1. **A journey, not a form.** The modal is a short sequence of states. Each state asks one thing and has one primary verb.
2. **Identity first, always.** Every platform's first question is which bot / number / workspace this runs on, because that answer determines every subsequent field. Today this fork is a two-button grid buried in the middle, and for Slack/Teams it's invisible until setup actions appear post-save.
3. **Don't ask what you can derive.** The surface name is derived from the platform. The responder is the agent whose page you opened the modal from — that's the entire point of moving surfaces into agents, so the "Default responder" select **leaves the connect path**. Both remain editable in Configure.
4. **End on proof.** Every connect journey ends on a state that shows a human how to reach the agent — handle, deep link, QR, test message. Today no such state exists; the dialog closes on a toast.
5. **Constraints render as state, never as a failed save.** A claimed system credential is a disabled option with a reason and a link, shown before the user commits.
6. **Narrower.** `max-w-md` for journey states, `max-w-lg` for Configure. Width is why the current dialog reads like a settings page. Below `sm`, the same states render as a bottom sheet.

## Anatomy

```
┌────────────────────────────────────────────┐
│ ◐ Telegram                    ·Live·   ✕   │  platform mark · label · status pill
│ Make Ops Assistant reachable on Telegram   │  promise — changes per state, names the agent
├────────────────────────────────────────────┤
│                                            │
│   one state, one question                  │
│                                            │
├────────────────────────────────────────────┤
│ ← Back                  [Cancel] [Continue]│  one primary verb; back only mid-journey
└────────────────────────────────────────────┘
```

`Remove surface` leaves the footer and moves to an overflow menu in the header, so Configure also has exactly one primary verb. A destructive action sitting next to Save in the current footer is a misclick waiting to happen.

## States

| State | When | Primary verb |
| --- | --- | --- |
| `identity` | No surface yet | Continue / Connect / Create my bot |
| `connect` | Bring-your-own path only | Connect |
| `provisioning` | Telegram's manager-bot hand-off | — (none; the work is in Telegram) |
| `live` | Just connected | Done |
| `configure` | Surface exists and is verified | Save |
| `setup` | Surface exists, not verified | Done |
| `message` | Messaging a member from a live surface | Send message |

`provisioning` has no primary verb on purpose. The user's job is in Telegram, and
a button here would only invite a click that does nothing.

## Interaction rules

- Never disable the primary verb without saying why immediately beside it.
- Secrets mask with a reveal toggle; every copyable value is a button with copy feedback (the existing `SetupCopyField` pattern is right, keep it).
- Conflicts and unavailability render inline on the option, disabled, with the reason and a link to the thing holding the claim — never as a toast after Save.
- A step the user must complete outside Lemma ends with an explicit acknowledgment control, because we cannot verify it and an unacknowledged surface is indistinguishable from an abandoned one.

---

## Telegram

Every Telegram bot is the user's own — there is no shared Lemma bot to offer, so
the question isn't *whose* bot it is but whether they need a new one. Creating
one leads, because that's the path almost everyone takes.

**`identity`**

```
◐ Telegram                                    ✕
Give Ops Assistant a Telegram bot people
can message

┌──────────────────────────────────────────┐
│ ⬤  Create a bot                   ~1 min │
│    You name it in Telegram and it’s      │
│    yours. Nothing to copy back here.     │
└──────────────────────────────────────────┘
┌──────────────────────────────────────────┐
│ ○  Use a bot you’ve connected            │
│    Point an existing Telegram bot at     │
│    Ops Assistant instead.                │
└──────────────────────────────────────────┘

                          [Cancel] [Create my bot]
```

"Create a bot" hands off to Lemma's **manager bot** (`agent.surface.telegram_managed.start`),
which walks the user through naming it inside Telegram and registers the result.
The token never reaches the browser, so there is nothing to paste, verify, or
mistype — and no BotFather walk to narrate.

**`provisioning`** — a wait, not a form. There is no primary verb here, because
the work is happening in Telegram and offering a button would only invite a
click that does nothing:

```
◐ Telegram                                    ✕
Finish naming it in Telegram — the bot is yours

⟳ Waiting for you to name it in Telegram…

    ┌─────────┐
    │ ▓▒░ ░▒▓ │  Telegram will ask for a name and
    │ ░▒▓ ▓▒░ │  a username. Scan this on your
    └─────────┘  phone or open it here — this
                 window updates by itself.

                 [Open Telegram ↗]

                                     [Close]
```

The QR matters: the hand-off finishes on a phone, and the person setting this up
is usually at a desk. Polling stops on `COMPLETE`/`FAILED`; an expired or failed
setup gets its own state with Telegram's reason and a **Start again**. The
completed setup names the surface it created, which the modal adopts before
moving to the proof state.

**`live`**

```
✓ Telegram                                    ✕
Ops Assistant answers as @acme_ops_bot

    ┌─────────┐
    │ ▓▒░ ░▒▓ │     t.me/acme_ops_bot
    │ ░▒▓ ▓▒░ │     [Copy link]   [Open ↗]
    └─────────┘

Lemma registered the webhook — nothing else
to configure. Send it a message to check.

                                       [Done]
```

The token becomes a `telegram` connector account created **from inside the modal** (fields rendered from `credential_schema` via the existing [`SchemaFields`](lemma-frontend/components/connectors/schema-fields.tsx)); the user never visits Connectors. Guardrails rendered rather than discovered: one bot = one surface org-wide (Truth 2), and custom bots need a public HTTPS deployment (Truth 3) — on a local runtime the "Your own bot" option is disabled up front with that reason.

## WhatsApp

**`identity`** — the shared number's claim state is the headline:

```
◐ WhatsApp                                    ✕
Make Ops Assistant reachable on WhatsApp

┌──────────────────────────────────────────┐
│ ⬤  Lemma number                Available │
│    +1 555 0134. One pod per org can use  │
│    it — this org hasn’t claimed it yet.  │
└──────────────────────────────────────────┘
┌──────────────────────────────────────────┐
│ ○  Your own number              ~15 min  │
│    Needs a Meta Business account and a   │
│    webhook you configure yourself.       │
└──────────────────────────────────────────┘

                           [Cancel] [Continue]
```

**`live` (Lemma number)** — the per-person half of the story (Truth 1b), told at the moment it becomes true rather than in a settings page nobody visits. This block renders **only** when `GET /surfaces/me` reports `conflict` for WhatsApp:

```
✓ WhatsApp                                    ✕
Ops Assistant answers on +1 555 0134

    ┌─────────┐
    │ ▓▒░ ░▒▓ │     wa.me/15550134
    └─────────┘     [Copy]   [Open ↗]

 ⓘ You’re in two pods reachable on this
   number. Your messages go to:
      ( ) Support          (•) Ops Assistant
   Change this any time.

                                       [Done]
```

The radio writes `PUT /surfaces/me/default`.

**`finish-setup` (own number)** — credentials, then the part Lemma genuinely cannot do:

```
← WhatsApp · Your own number                  ✕
Finish in Meta — Lemma can’t do this part

 Callback URL
 ┌──────────────────────────────────────┐ ⧉
 │ https://api.lemma.work/…/webhook     │
 └──────────────────────────────────────┘
 Verify token
 ┌──────────────────────────────────────┐ ⧉
 │ ••••••••••••••••          reveal     │
 └──────────────────────────────────────┘

 1  developers.facebook.com/apps → your app
 2  WhatsApp → Configuration
 3  Paste both values above
 4  Subscribe to the “messages” field
 5  Verify and save

 ☐ I’ve done this in Meta

                             [Cancel] [Finish]
```

Content comes from the typed setup actions the backend already emits ([setup_guides.py:158](lemma-backend/app/modules/agent_surfaces/domain/setup_guides.py:158)) — the change is rendering them *during* setup instead of after.

## Slack

The identity fork exists here too and is currently invisible: Lemma's Slack app vs. the workspace's own app. Make it the first state, then two explicit steps in one modal:

```
Step 1 · Connect workspace  ────►  Step 2 · Route channels
```

Step 2 becomes reachable the moment step 1 saves, because the surface now exists and channels can be enumerated (Truth 7). That's what turns a limitation into a sequence — and it retires the current copy, *"Turn on Telegram first, then reopen Configure to route channels."* A workspace on its own Slack app gets the Event Subscriptions checklist ([setup_guides.py:108](lemma-backend/app/modules/agent_surfaces/domain/setup_guides.py:108)) as step 1b, same shape as WhatsApp's Meta block.

Channel rows keep today's structure (channel select · agent select · remove), with the mention/thread rule stated once above them.

## Teams

Slack's shape, with admin consent promoted to a blocking state of its own — status `PENDING_ADMIN_CONSENT` and `setup.admin_consent.consent_url` already exist:

```
⚠ Teams · Waiting on your admin
A Microsoft admin approves this once for
your tenant.

   [Open consent page ↗]     [Copy link]

Send the link to whoever administers your
Microsoft tenant. This modal updates when
approval lands.
```

## Gmail / Outlook

Mailbox pick, then filters — with the warning promoted, because for a mailbox surface it's the highest-consequence field on the screen:

```
Which senders become work?

 Allowed domains      [ acme.com, partner.org ]
 Allowed addresses    [ vip@acme.com          ]

 ⚠ No filters set — every email in this
   mailbox becomes pod work.
```

Warning tone, doesn't block save (some mailboxes really are dedicated).

## Resend

The simplest module and a good template for the shell: no identity fork, no account, no journey. Show the provisioned `pod-<id>@<domain>`, copy control, filters, Done.

## What goes away

- The surface name input (derived; rename moves to Configure's overflow)
- The "Default responder" select in the connect path (the agent is the context)
- The everything-at-once scroll
- The post-hoc `SurfaceSetupSection`
- `max-w-2xl`
- `Remove surface` in the footer next to Save
- "Turn on X first, then reopen Configure to route channels"

---

## Truths the UI must convey

Each is enforced in the backend today and invisible in the UI until it fails.

1. **System credentials are claimable once per organization** ([surface_service.py:815](lemma-backend/app/modules/agent_surfaces/services/surface_service.py:815) → `get_system_credential_conflict_in_org`) — today a prose 400 discovered on Save.
   **1b.** And when one person belongs to several orgs that have each claimed it, inbound routing already converges on exactly one surface for that person: membership → their saved default → conversation continuity → deterministic tiebreak ([ingress_service.py:1430](lemma-backend/app/modules/agent_surfaces/services/ingress_service.py:1430)). The model is single-valued at both layers; the UI's job is to say *which pod holds the claim* (org layer, in `identity`) and *which pod answers you* (person layer, in `live`).
2. **A connector account binds to exactly one surface in the org**, and a Telegram account additionally to exactly one surface globally ([`_ensure_unique_telegram_account`](lemma-backend/app/modules/agent_surfaces/services/surface_service.py:847)).
3. **Custom Telegram/WhatsApp/Slack/Teams surfaces need a public HTTPS API URL.** Local and standalone runtimes get Telegram polling and Slack Socket Mode only. On such a deployment the bring-your-own option is disabled in `identity` with that reason, not refused at Save.
4. **Telegram registers its own webhook; WhatsApp does not.** Telegram's manager-bot hand-off also removes the failure mode that used to matter here — the token is never typed, so there is no mistyped-token surface to strand. A hand-off that fails or expires reports Telegram's own reason and offers **Start again**.
5. **`GET /surfaces/me` and `PUT /surfaces/me/default` exist, expose a `conflict` flag, and nothing consumes them.** The `live` state's default picker is the first consumer.
6. **A surface has a reach handle** — `@botname`, phone, mailbox — resolved lazily and cached ([surface_reach_resolver.py](lemma-backend/app/modules/agent_surfaces/services/surface_reach_resolver.py)), with client-side deep links already written ([`getSurfaceDeepLink`](lemma-frontend/lib/utils/surfaces.ts:66)). This is what makes the `live` state possible.
7. **Channel routes are an after-creation step** for Slack/Teams; in a channel the agent answers only on mention or in a thread it's already in.
8. ~~**Proactive messaging never cold-opens a thread** — it reuses an existing one.~~
   *Superseded by [proactive-messaging.md](./proactive-messaging.md).* This was stated
   as universal and was only ever true of chat bots: email surfaces can address someone
   who never wrote first. It now lives as data — `can_cold_open` and
   `reply_window_hours` on `platform_capabilities.py` — rather than as a rule in prose.
   The caveat that *does* still belong next to the toggle is narrower: `surface.send`
   targets one named surface and needs a thread that already exists, whereas `notify`
   picks a channel and always leaves a copy in the recipient's Lemma inbox.

## API: what exists, what's missing

**Sufficient today:** list / create / update / delete / toggle, `GET …/surfaces/{name}/setup` (typed actions + admin consent), `GET …/channels`, `POST …/send`, `GET …/setup-guide`, `GET /surfaces/me`, `PUT /surfaces/me/default`.

**Exists, unused by the frontend:** `GET /pods/{id}/surfaces/available` ([available_surfaces_builder.py](lemma-backend/app/modules/agent_surfaces/services/available_surfaces_builder.py)) returns per platform: connector id, title/description/icon, supported credential modes (`SYSTEM` only when native credentials are actually configured in this environment), and a connect descriptor with `auth_scheme` + `credential_schema`. **This drives the modal catalog and credential forms** instead of the hardcoded `SURFACE_DEFINITIONS` array — the difference between "WhatsApp appears and fails on Save because this deployment has no Meta credentials" and "WhatsApp offers only *your own number* here."

**Gaps (backend, small):**

- **G1 — Typed claim conflict** *(done).* `AgentSurfaceCredentialConflictError` carries `{kind, conflicting_surface: {pod_id, name}}`, and the catalog publishes the same claim up front as `system_claim` so the option disables before the user commits.
- **G2 — Managed-setup availability** *(done).* `managed_setup_available` on the catalog, so the Telegram hand-off is offered only where a manager bot is configured. Read defensively on the client: only an explicit `false` blocks, since a backend predating the field would otherwise hide the primary way to connect Telegram.
- **G3 — Reach on create** *(already true).* The create response resolves `reach`, so `live` renders the handle without a second round trip.

**Frontend plumbing:** `useAvailableSurfaces` (new), `useUserSurfaces` / `useSetDefaultSurface` (exist, unused), and reuse of [`ConnectAccountDialog`](lemma-frontend/components/connectors/connect-account-dialog.tsx) / [`SchemaFields`](lemma-frontend/components/connectors/schema-fields.tsx) for in-modal account creation.

## Work breakdown

**Phase 1 — Shell and registry.** `SurfaceModal` shell, the state machine, the per-platform module contract; extend `lib/utils/surfaces.ts` into the shared registry fed by `agent.surface.available`. Port existing behavior so nothing regresses. *No user-visible change yet.*

**Phase 2 — Telegram and WhatsApp modules.** The manager-bot hand-off, WhatsApp's in-modal account creation and claim state, `live` with reach/QR/default picker. *This is the bulk of the value.*

**Phase 3 — Slack, Teams, Gmail/Outlook, Resend modules.** Re-hosting existing fields plus the identity fork and inline setup actions; Slack/Teams get the two-step shape and the consent state.

**Phase 4 — Agent surfaces.** Rewire the Surfaces row on agent detail, `/ai/assistant`, and the `/ai` roll-up to open modals. Delete `InlineChannelForm`'s "Connect new" grid.

**Phase 5 — Retire the tab.** Remove the nav item, routes, workspace-tab kind, layout branches; redirect `/surfaces` and `/channels` → `/ai`. Keep the permission key as a capability gate. Update onboarding/recipe copy pointing at the surfaces page.

**Phase 6 — Optional standalone "Where you're reachable" view.** The `live` picker covers the moment of truth; a settings view consuming `/surfaces/me` is only needed if users want to change it later without opening a pod.

Phases 1→2 are the critical path; 3 and 4 parallelize once the shell lands; 5 follows 4; 6 is independent.

## Open questions

1. ~~Multiple surfaces of one platform per agent.~~ *Resolved:* surfaces are independent, so each agent gets its own bot on a platform another agent already uses. A faded chip always starts a **new** surface — it never reopens someone else's — and the name is derived from the agent (`telegram-ops-assistant`), since only the first surface of a platform can hold the bare platform name. Still open: whether one agent should be able to hold two bots on the same platform, which the model allows and the chip row doesn't currently offer.
2. **Does anyone lose the pod-wide ledger?** Nothing links to `/surfaces` except the agent pages we're rewiring, but an admin auditing "every place this pod listens" currently has one page and would gain none. Recommendation: accept it (the `/ai` roll-up covers it); revisit if asked.
3. ~~QR generation.~~ *Resolved:* `react-qr-code` is already a dependency, used by WhatsApp mobile verification.

## Out of scope

Changing surface routing semantics, the ingress pipeline, the proactive-send policy, WhatsApp number verification for sign-in, or the bundle/export representation of surfaces. This is an IA and setup-experience change; the surface model itself does not move.
