# Surfaces

A surface meets users where they already chat — it exposes a pod agent on
**Slack, Teams, Telegram, WhatsApp, Gmail, or Outlook**. A teammate DMs the bot or
emails the mailbox, the agent answers (as a delegated pod user, with that user's
grants), and the exchange becomes a pod conversation. The surface owns routing and
behavior; credentials live in a **connector account**, never on the surface itself.

> Grounds in `pod-model.md` (surfaces are a human interface over the same data +
> identity). The surface is often *the product* — design the agent and its grants
> as carefully as any app.

**Surface vs. event-workflow** (pod-model heuristic #3). A surface is for a **human
conversing** — a *person* initiates each exchange. If instead a *system event* (a
connector trigger, a row change) should drive *unattended* work, that's an
**event-based schedule → workflow/agent** (`schedules-and-triggers.md`), not a surface.
The two can share one connector account: the surface answers people while a `WEBHOOK`
schedule runs a pipeline. And a surface's agent is a full pod agent — it can start
functions, workflows, or other agents via its tools mid-conversation, so "do real work
from chat" never requires leaving the surface.

## The model, for surfaces

A surface is **one row per platform per pod**, so you address it by **platform
name** everywhere (`slack`, `gmail`, …) — there is no separate surface id to track.

- **`mode`** — `DM` (one external user ↔ the agent, private) or `EMAIL`
  (mailbox-style, one message per thread). EMAIL is **Gmail/Outlook only**; chat
  platforms are DM. (Slack/Teams *channels* are routed via `config.channels`, not a
  separate mode — see below.)
- **`event_mode`** — purely the *delivery mechanism* for an inbound **human message**
  (not a system-event trigger — don't confuse it with a `WEBHOOK` *schedule*):
  `WEBHOOK` (platform POSTs to the backend; chat platforms) or `COMPOSIO_TRIGGER`
  (poll/trigger via Composio; **the required mode for EMAIL surfaces**). The default is
  correct per platform — don't override unless you have a reason. (EMAIL ⇒
  `COMPOSIO_TRIGGER`; everything else ⇒ `WEBHOOK`. Mismatching them is rejected.)
- **`credential_mode`** — how the surface authenticates to the platform. Three paths:
  - **System bot — no `account_id`** (`SYSTEM`). Lemma's own prebuilt app, available
    for **Telegram and WhatsApp**: create the surface with no account and it just works
    once `ACTIVE` — no app to register, no OAuth, no webhook to paste.
  - **Lemma-managed OAuth — `account_id`, no platform-side setup** (`CUSTOM`). For
    **Gmail and Outlook**, connect an account through the normal connector OAuth flow
    (`connectors.md`); Lemma manages the token and the inbound trigger. You supply the
    `account_id`; there is nothing to configure on the provider beyond consenting.
  - **Custom app — `account_id` + a manual step** (`CUSTOM`). For **Slack and Teams**
    (or a BYO app on Gmail/Outlook): register your own platform app/bot, connect it as
    an account, then finish the step the platform requires — paste the webhook/redirect
    URL Lemma gives you, and for Teams obtain tenant admin consent.

  Passing an `account_id` always makes the surface `CUSTOM`. **Slack, Teams, Gmail, and
  Outlook require an `account_id`; Telegram and WhatsApp do not** (they default to the
  system bot).
- **`config`** — user-editable behavior: `dm_conversation_reset_after_hours`
  (default 24 — don't let one DM thread accumulate forever), `identity`
  (allow-list of senders), `channels` (Slack/Teams channel→agent routes), and
  `send_policy`.
- **`config.send_policy.audience`** — who this surface's agent may reach *first*,
  rather than in reply: `NOBODY` (the default, and what every surface did before
  this existed), `SELF` (only the person a run belongs to), or `POD_MEMBERS`.
  Only `POD_MEMBERS` lets an agent with the `MESSAGING` toolset message a
  colleague. Set it deliberately: the recipient sees the pod's bot, not "the
  agent someone else's schedule is running", and extends it the trust they
  extend to Lemma — which is why every such message names both the agent and the
  human whose authority the run carries. The older boolean `allow_send` still
  reads and writes, and maps to `SELF`.
- **`default_agent_name`** — the pod agent that answers. Per-channel routing can
  send specific channels to other agents.

**Status** is one of `ACTIVE`, `PENDING_ADMIN_CONSENT` (Teams, awaiting tenant
admin), `NEEDS_SETUP`, `INACTIVE`, `ERROR`. Only `ACTIVE` accepts inbound events.

## What the system handles for you

A surface is **fully managed plumbing**. Once it's `ACTIVE`, Lemma owns the whole
transport: it registers and renews the platform webhook (or the Composio trigger for
email), receives every inbound event, verifies its signature, de-duplicates, maps the
sender to a delegated pod user, opens or reuses the right conversation, runs the agent,
and posts the reply back on-platform in the correct thread/channel. **Your agent never
sees a webhook, a signing secret, or a raw platform payload** — it runs in a conversation
exactly as if the user had messaged it in Lemma's own chat. You configure *who answers*
(`default_agent_name`, channel routes) and *who's allowed* (`identity`); the system does
the rest.

(Don't confuse this with a `WEBHOOK` *schedule*, the explicit path for system-event
automation — `schedules-and-triggers.md`. A surface's `event_mode=WEBHOOK` is just the
inbound-message transport; you never wire it.)

## Setup flow

The shape is always: **connector account → surface upsert → finish platform-side
setup**.

```bash
# 1. Credentials live in a connector account (skip for a SYSTEM-credential platform)
lemma connectors auth-configs create slack --name workspace-slack
lemma connectors connect-requests create slack --auth-config-id <auth-config-id>
lemma connectors accounts list --app slack            # grab the <account-id>

# 2. Create/update the surface (one command per platform — covers create AND edits)
lemma surfaces upsert slack --agent triage-agent --account <account-id>

# 3. Finish + inspect platform-side setup (webhook URL, admin consent, checklist)
lemma surfaces setup slack
```

`upsert` is the single create-or-update command (a surface is unique per
pod+platform). Only the fields you pass change; the rest are left alone.

`lemma surfaces setup <platform>` reports readiness and any outstanding manual steps:
for **Slack/Teams** the **webhook/redirect URL** to paste into your app config (and, for
**Teams**, the **admin-consent link** while status is `PENDING_ADMIN_CONSENT`); for the
**system-bot** (Telegram/WhatsApp) and **Lemma-managed-OAuth** (Gmail/Outlook) paths it
simply confirms there's nothing left to do. When a surface is `ACTIVE`, the system is
already receiving and handling inbound messages.

```bash
# DM surface with a reset policy
lemma surfaces upsert slack --agent triage-agent --account <account-id> \
  --data '{"config": {"dm_conversation_reset_after_hours": 24}}'

# Email surface (Gmail) with a sender allow-list — event_mode defaults to COMPOSIO_TRIGGER
lemma surfaces upsert gmail --agent inbox-agent --account <account-id> \
  --allowed-domain example.com --allowed-email vip@partner.com

# Route Slack channels to agents (replaces ALL routes; Slack/Teams only)
lemma surfaces available-channels slack                # list routable channels
lemma surfaces channels slack --channel-id C123 --channel-name support --agent support-agent
```

First-class flags: `--agent/--agent-name`, `--account/--account-id`,
`--credential-mode SYSTEM|CUSTOM`, `--enabled/--disabled`, `--allowed-domain`,
`--allowed-email`. Everything else (`config`, identity) goes in `--data`/`--file`.

## Manage

```bash
lemma surfaces list
lemma surfaces get slack
lemma surfaces upsert slack --data '{"config": {"dm_conversation_reset_after_hours": 48}}'
lemma surfaces enable slack / lemma surfaces disable slack    # toggle without deleting
lemma surfaces setup slack                                    # what's still missing?
lemma surfaces delete slack --yes                             # frees the account for another pod
```

## Patterns

- **DM assistant.** A `DM` surface maps one external identity to one pod
  conversation until the reset window — always set `dm_conversation_reset_after_hours`
  so threads don't grow forever.
- **Channel triage (Slack/Teams).** Default agent for general channels, with
  `config.channels` routing `#billing` or `#security` to specialist agents. The
  agent replies in-thread where the platform supports it.
- **Shared mailbox (Gmail/Outlook).** An `EMAIL` surface turns a mailbox into an
  agent inbox; use `identity.allowed_domains` / `allowed_email_addresses` so only
  trusted senders are answered.
- **Pair with an app.** Requesters talk to the surface; operators work the queue in
  an app (`apps.md`). The surface agent writes to the same tables the app reads —
  one data model, two front doors.

**Write the agent for the medium.** A surface agent needs instructions tuned to its
channel (short replies for chat; subject/quote handling for email) and **grants for
everything it must read to answer** (`pod-model.md` → zero access by default). An
agent that can't read the knowledge folder will answer confidently and wrongly.

## Bundles

Configured surfaces **round-trip in pod bundles** as
`surfaces/<platform>/<platform>.json` (folder name = lowercased platform):

```json
{
  "name": "slack",
  "platform": "SLACK",
  "default_agent_name": "triage-agent",
  "credential_mode": "CUSTOM",
  "account_id": "${slack_account}",
  "is_enabled": true,
  "config": {
    "dm_conversation_reset_after_hours": 24,
    "channels": [{ "channel_id": "C123", "channel_name": "support", "agent_name": "support-agent" }],
    "identity": { "allowed_domains": ["example.com"] }
  }
}
```

Import **upserts by platform** (one surface per platform per pod) and resolves the
agent by name. What does **not** travel and must be reconnected/re-derived per
environment: the **connector account** itself (`account_id` exports as a
`${variable}` you resolve at import with `--var`, or leave unresolved so the
surface uses the invoking user's account — see `cli-and-bundles.md` →
*Portability variables*), **webhook secrets**, **platform setup state**, and
resolved identities. After importing into a fresh environment, run `lemma surfaces setup
<platform>` to finish.

## Limits & gotchas

- **EMAIL is Gmail/Outlook only**, and an EMAIL surface **must** use
  `COMPOSIO_TRIGGER` event_mode (the default) — a non-email platform may not.
- **Account required.** SLACK, TEAMS, GMAIL, and OUTLOOK reject creation without an
  `account_id`. Connect the account first (`connectors.md`).
- **One surface per platform.** A second same-platform surface isn't a bundle thing
  — the upsert replaces; use distinct platforms.
- **Account is referenced by id.** Reconnecting or rotating the account means
  updating the surface's `account_id`.
- **The webhook is managed, not yours to wire.** Lemma registers and serves the inbound
  endpoint; for a custom Slack/Teams app you only ever *paste a URL it gives you*.
  `WEBHOOK` inbound still needs a publicly reachable backend, so local stacks may not
  receive platform POSTs; Teams may sit in `PENDING_ADMIN_CONSENT` until a tenant admin
  consents (`lemma surfaces setup teams` shows the link).
- **Conversations are lazy.** A link is created on first contact per external
  thread/user — an idle surface shows no conversations until someone messages it.

## Verify

```bash
lemma surfaces get slack          # status ACTIVE, right agent, right account
# Send a real message on the platform (DM the bot / mention in a routed channel / email the mailbox)
lemma conversations list          # a conversation appeared, linked to the surface
lemma conversations messages <id> # the exchange is recorded
# Confirm the reply landed on-platform in the right thread/channel.
# DM reset: confirm a new conversation starts after the reset window.
```

## See also

- The model → `pod-model.md` · credentials/accounts → `connectors.md`
- The agent behind the surface → `agents.md` · the paired operator UI → `apps.md`
- Operate a live surface → the `lemma-user` skill
