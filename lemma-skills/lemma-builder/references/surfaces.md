# Surfaces

A surface meets users where they already chat — it exposes **one pod agent** on
**Slack, Teams, Telegram, WhatsApp, or email**. A teammate DMs the bot or emails the
agent's address, the agent answers (as a delegated pod user, with that user's grants
and inside that user's ceiling), and the exchange becomes a pod conversation. The
surface owns routing and behavior; platform credentials come from a **SHARED pod
connector** (an account its owner shared with the pod) or from Lemma's own system
credentials, never from the surface itself.

> Grounds in `pod-model.md` (surfaces are a human interface over the same data +
> identity). The surface is often *the product* — design the agent and its grants
> as carefully as any app.

**Email is Resend, and only Resend.** Gmail and Outlook used to be surface platforms;
they are **connectors** now (`connectors.md`). An agent still reaches a Gmail *account*
through the connector — that is how it sends mail as you. But a pod is *reached* on
email at its own Lemma-provisioned address. Anything you read that says
`platform: "GMAIL"`, `mode: EMAIL` on Gmail, or `event_mode: COMPOSIO_TRIGGER` is out
of date and will be rejected. Ground truth:
`agent_surfaces/domain/entities.py` → `SurfacePlatform`.

**Surface vs. event-workflow** (pod-model heuristic #3). A surface is for a **human
conversing** — a *person* initiates each exchange. If instead a *system event* (a
connector trigger, a row change) should drive *unattended* work, that's an
**event-based schedule → workflow/agent** (`schedules-and-triggers.md`), not a surface.
The two can coexist: the surface answers people while a `WEBHOOK` schedule runs a
pipeline. And a surface's agent is a full pod agent — it can start functions,
workflows, or other agents via its tools mid-conversation, so "do real work from chat"
never requires leaving the surface.

## The model, for surfaces

**One agent, one row, one surface.** A surface row belongs to exactly one agent
(`default_agent_name` on the wire) — there is no "which agent answers here?" routing
inside a surface. Give an agent that needs its own bot its own app and its own surface.
Naming no agent binds the surface to the **pod's own assistant**, which has a real
agent row like every other agent.

- **`name`** — the surface's **pod-unique identifier**, and how every command and route
  addresses it. It **defaults to the lowercased platform** (`slack`, `telegram`,
  `resend`), so the ordinary one-bot-per-platform pod never names anything. A pod may
  hold **several surfaces of the same platform** — two Slack bots for two agents, an
  auto-provisioned mailbox per agent — each with its own name. Every `lemma surfaces`
  command takes that name where its help says "platform", so `lemma surfaces get
  resend-inbox-agent` works; what the CLI cannot do is *create* a second one, since
  `upsert` derives the platform from the name it is given. Use
  `lemma surfaces telegram-setup --name`, or `POST /pods/{pod_id}/surfaces`, for that.
- **`platform`** — `SLACK`, `TEAMS`, `TELEGRAM`, `WHATSAPP`, `RESEND`. Immutable;
  delete and recreate to change it.
- **`mode`** — `DM` (a private one-to-one thread) or `EMAIL`. Derived from the platform
  and worth leaving alone: `EMAIL` is **RESEND-only** and is its default; everything
  else is `DM`. (Slack/Teams *channels* are an allow-list in `config.channels`, not a
  mode.)
- **`event_mode`** — `WEBHOOK`, and only `WEBHOOK`. Every surface now receives over a
  native webhook; the polled-mailbox mode went with Gmail/Outlook. Never set it.
- **`pod_connector`** — the slug of the **SHARED** pod connector the surface runs as.
  A surface answers with nobody at the keyboard, so a per-member connector is refused
  (422), and the connector must be the platform's own (a Slack surface needs a `slack`
  connector). The account behind it is re-read on every use: when its owner withdraws
  it, or the connector is removed, the surface stops at once and says why
  (`CONNECTOR_NEEDS_ACCOUNT`, `POD_CONNECTOR_NOT_FOUND`, `REAUTH_REQUIRED`). Binding
  needs the same `connector.use` and account access as running an operation through it.
- **`credential_mode`** — `SYSTEM` (Lemma's own credentials, no connector) or `CUSTOM`
  (your own bot). **Passing a `pod_connector` makes it `CUSTOM`.**
  Three shapes in practice:
  - **Provisioned, nothing to connect (`SYSTEM`)** — **email**. Every agent is minted a
    Resend mailbox *at creation*, `{agent}.{pod}@<RESEND_INBOUND_DOMAIN>` (pod fallback
    `pod-<pod-id-hex>@…`), read back as `surface_identity_email`. No account, no OAuth,
    no webhook to paste. **Don't create one — find it**: the surface already exists,
    named `resend-<agent>` (and `resend-assistant` for the pod's assistant), so
    `lemma surfaces list` is the first move and you address it by that name.
    The deployment-level setup (a verified catch-all domain, the Resend inbound
    webhook) is an operator job done once, not per pod.
  - **System bot (`SYSTEM`)** — **Telegram and WhatsApp**: create the surface with no
    account and it works once `ACTIVE`. For Telegram, `lemma surfaces telegram-setup`
    walks a *managed* bot creation and prints the link that creates it.
  - **Custom app (`CUSTOM`, `pod_connector` required)** — **Slack and Teams** reject
    creation without a `pod_connector`; Telegram and WhatsApp accept one if you want
    to bring your own bot/number. Register the platform app, connect it as your
    account, share it into the pod as a SHARED pod connector, then finish the
    platform-side step: paste the webhook/redirect URL Lemma gives you, and for Teams
    obtain tenant admin consent. A managed Telegram bot and a Slack teammate app Lemma
    makes are shared into the pod for you by the person setting them up.
- **`config`** — user-editable behavior:
  - `identity` — `allowed_domains` / `allowed_email_addresses`; empty means everyone.
  - `channels` — Slack/Teams allow-list of `{channel_id, channel_name}`. A route is a
    **place**, not a choice of agent; it no longer carries `agent_name`, because the
    surface's one agent always answers.
  - `send_policy.allow_send` — exposes this surface's own `surface_send_message` tool,
    which reaches **the person in the current conversation** mid-task.
  - `telegram.app_name` / `slack.app_name` — the pod app pinned as this bot's Telegram
    Mini App / Slack app surface.
- **`send_policy` does not gate reaching other pod members.** That capability is
  granted per *agent*, by giving it the `MESSAGING` toolset — see `agents.md`.
  A surface setting would be a second gate an editor had to find before a grant
  they had already made took effect.

**Status** is one of `ACTIVE`, `PENDING_ADMIN_CONSENT` (Teams starts here, awaiting a
tenant admin), `NEEDS_SETUP`, `INACTIVE`, `ERROR`. Only `ACTIVE` accepts inbound events.

## What the system handles for you

A surface is **fully managed plumbing**. Once it's `ACTIVE`, Lemma owns the whole
transport: it registers and renews the platform webhook, receives every inbound event,
verifies its signature, de-duplicates, maps the sender to a delegated pod user, opens or
reuses the right conversation, runs the agent, and posts the reply back on-platform in
the correct thread/channel. **Your agent never sees a webhook, a signing secret, or a
raw platform payload** — it runs in a conversation exactly as if the user had messaged
it in Lemma's own chat. You configure *who answers* (the surface's agent), *where*
(`config.channels`, an allow-list) and *who's allowed* (`identity`); the system does the
rest.

**Outbound goes through one seam.** The agent produces one envelope — text, resources,
files, voice, choices, a decision — and each platform renders what it can and degrades
the rest, reporting how each part landed. Two consequences worth designing around:

- **Chat platforms send many messages; email sends one.** An email surface folds the
  whole turn into a single reply, so a file or a voice note is an *attachment on that
  reply*, not a second send.
- **Interactive tools are native where they can be.** `ask_user` renders as native
  choices and `request_approval` as native Approve / Deny (and Approve-for-session
  where the paused call carries permissions) on Slack, Teams, Telegram and WhatsApp,
  with a formatted text prompt as the fallback anywhere else — including when a native
  render fails. **Email is interactive too, asynchronously**: the question goes in the
  one reply, the run ends, and the person's emailed answer resolves the pause through
  the same path a tapped Slack button takes. A reply that is *not* a decision is not
  swallowed — it supersedes the pending call and reaches the agent as what it is.

(Don't confuse this with a `WEBHOOK` *schedule*, the explicit path for system-event
automation — `schedules-and-triggers.md`. A surface's inbound webhook is transport you
never wire.)

## Thread shape — one conversation per person and agent

Every thread sits in a **channel**: a member's private chats on every platform are
their `DIRECT` channel, a contact's are their `CONTACT` channel, and a group, a shared
Slack channel or an email thread with others on it is a `GROUP` channel of its own. A
person has **one conversation per agent in a channel**, however many threads, numbers,
platforms or quiet days they write across — a new Slack DM thread, a WhatsApp chat on
another pool number and a Telegram message a week later all continue it. Only a
different agent, or a different person, opens another. There is no inactivity reset;
the agent's own history cap and compaction decide how much of a long conversation a run
carries. A surface config that still sends `dm_conversation_reset_after_hours` is
accepted and ignored. The SDK lists channels and reads their timelines (`pod.channels` in Python, `client.channels` in TypeScript).

## Setup flow

The shape is: **(credentials, if the platform needs them) → surface upsert → finish
platform-side setup**.

```bash
# 1. Credentials: your own account, shared with the pod as a SHARED connector —
#    Slack/Teams only (Telegram/WhatsApp default to the system bot; email is provisioned)
#    Lemma's own Slack app needs no setup: with no install yet, this installs it
#    and returns the sign-in link.
lemma connectors connect-requests create slack        # -> authorization_url
lemma connectors accounts list --app slack            # grab the <account-id>
lemma connectors pod-share <account-id>               # owner only; -> <pod-account-id>
lemma connectors pod-add support-slack slack --mode SHARED \
  --shared-account <pod-account-id>
#    Your own Slack app instead: create the install first (field names from
#    `lemma connectors get slack`, kinds[].install_config_schema; Slack also needs
#    signing_secret), then pass --auth-config-id to connect-requests and the
#    install's name to pod-add. `surfaces setup` then lists the webhook steps.
#    lemma connectors auth-configs create slack --kind http --name workspace-slack \
#      --config-source ORG_CUSTOM -d '{"client_id": "...", "client_secret": "...", "signing_secret": "..."}'

# 2. Create/update the surface (one command per platform — covers create AND edits)
lemma surfaces upsert slack --agent triage-agent --connector support-slack

# 3. Finish + inspect platform-side setup (webhook URL, admin consent, checklist)
lemma surfaces setup slack
```

`upsert` is the single create-or-update command, addressed by platform (= the default
surface name). Only the fields you pass change; the rest are left alone.

`lemma surfaces setup <platform>` reports readiness and any outstanding manual steps:
for **Slack/Teams** the **webhook/redirect URL** to paste into your app config (and, for
**Teams**, the **admin-consent link** while status is `PENDING_ADMIN_CONSENT`); for the
**system-bot** (Telegram/WhatsApp) and **provisioned-email** paths it simply confirms
there's nothing left to do. When a surface is `ACTIVE`, the system is already receiving
and handling inbound messages.

```bash
# DM surface that only answers people from one domain
lemma surfaces upsert slack --agent triage-agent --connector support-slack \
  --data '{"config": {"identity": {"allowed_domains": ["example.com"]}}}'

# Email: the surface already exists, named after its agent. Find it, then configure it.
lemma surfaces list                                   # -> resend-inbox-agent, …
lemma surfaces get resend-inbox-agent                 # prints surface_identity_email
lemma surfaces upsert resend-inbox-agent \
  --allowed-domain example.com --allowed-email vip@partner.com

# Managed Telegram bot, end to end
lemma surfaces telegram-setup --agent triage-agent   # prints the link (and a setup id)
lemma surfaces telegram-setup-status <setup-id>      # poll until bot_username is set

# Allow-list the Slack channels this surface's agent answers in (replaces ALL routes)
lemma surfaces available-channels slack                # list routable channels
lemma surfaces channels slack --channel-id C123 --channel-name support
```

First-class flags on `upsert`: `--agent/--agent-name`, `--connector/--pod-connector`,
`--credential-mode SYSTEM|CUSTOM`, `--enabled/--disabled`, `--allowed-domain`,
`--allowed-email`. Everything else (`config`, identity) goes in `--data`/`--file`.

## Manage

```bash
lemma surfaces list
lemma surfaces get slack
lemma surfaces upsert slack --data '{"config": {"send_policy": {"allow_send": true}}}'
lemma surfaces enable slack / lemma surfaces disable slack    # toggle without deleting
lemma surfaces setup slack                                    # what's still missing?
lemma surfaces delete slack --yes                             # frees the bot for another surface
```

## Patterns

- **DM assistant.** A `DM` surface maps one person to one pod conversation with
  the agent, in their direct channel, for as long as they keep writing.
- **Channel triage (Slack/Teams).** `config.channels` is the allow-list of
  channels this surface's agent answers in; the agent replies in-thread where the
  platform supports it. A specialist agent for `#billing` needs its **own bot** —
  one surface answers as one agent, so give it its own Slack app and its own named
  surface. `GET /surface-setup/slack/manifest?agent_name=billing-agent` serves a
  ready-to-paste manifest already named for that agent, with this deployment's URLs
  and the scopes its own code asks for.
- **Agent inbox (email).** Every agent already has an address on the `resend-<agent>`
  surface; point people at it and set `identity.allowed_domains` /
  `allowed_email_addresses` so only trusted senders are answered. Remember the whole
  turn arrives as one reply — write the agent for that.
- **Pair with an app.** Requesters talk to the surface; operators work the queue in
  an app (`apps.md`). The surface agent writes to the same tables the app reads —
  one data model, two front doors.

**Write the agent for the medium.** A surface agent needs instructions tuned to its
channel (short replies for chat; subject/quote handling for email) and **grants for
everything it must read to answer** (`pod-model.md` → zero access by default). An
agent that can't read the knowledge folder will answer confidently and wrongly. And
because a run is capped by the person who sent the message, a surface answering
`VIEWER`s cannot write on their behalf — see `authorization-model.md` §2.

## Bundles

Configured surfaces **round-trip in pod bundles** as `surfaces/<name>/<name>.json`,
where the folder name is the surface's `name` (the lowercased platform, unless you gave
it one):

```json
{
  "name": "slack",
  "platform": "SLACK",
  "default_agent_name": "triage-agent",
  "credential_mode": "CUSTOM",
  "pod_connector": "support-slack",
  "is_enabled": true,
  "config": {
    "channels": [{ "channel_id": "C123", "channel_name": "support" }],
    "identity": { "allowed_domains": ["example.com"] }
  }
}
```

`platform` must be present and must be one of the five — a bundle without it is
rejected rather than guessed at from the folder name. Import upserts the surface and
resolves the agent by name. What does **not** travel and must be reconnected/re-derived
per environment: the **account** behind the pod connector (the bundle carries only the
`pod_connector` slug; the importing pod declares that connector and an owner shares an
account into it), **webhook secrets**, **platform setup state**, the **provisioned email address**, and
resolved identities. After importing into a fresh environment, run
`lemma surfaces setup <platform>`.

> One bundle caveat: the **CLI importer keys the upsert on the platform**, so a bundle
> carrying two surfaces of the same platform collapses onto one through
> `lemma pods import`. The backend's async import keys on `name` and round-trips both.
> Keep bundles to one surface per platform unless you are importing server-side.

## Limits & gotchas

- **Email is RESEND only**, and `EMAIL` mode is rejected on any other platform.
  `COMPOSIO_TRIGGER` no longer exists as an `event_mode`.
- **Connector required.** SLACK and TEAMS reject creation without a `pod_connector`.
  Connect the account and share it into the pod first (`connectors.md`). TELEGRAM,
  WHATSAPP and RESEND do not.
- **One agent per surface.** Two agents on one platform means two surfaces (and, for
  Slack/Teams, two apps) — not one surface with routing rules.
- **The connector is referenced by slug, the account never.** Reconnecting the same
  account needs nothing; pointing the pod connector at another shared account (an
  editor's `pod.connector.manage`) moves every surface on it, and rebinding one
  surface is `upsert --connector <other-slug>`.
- **The webhook is managed, not yours to wire.** Lemma registers and serves the inbound
  endpoint; for a custom Slack/Teams app you only ever *paste a URL it gives you*.
  Inbound still needs a publicly reachable backend, so local stacks may not receive
  platform POSTs; Teams may sit in `PENDING_ADMIN_CONSENT` until a tenant admin
  consents (`lemma surfaces setup teams` shows the link).
- **Conversations are lazy.** A link is created on first contact per external
  thread/user — an idle surface shows no conversations until someone messages it.
- **A shared system bot serves many pods.** When one Telegram bot or WhatsApp number is
  reachable across pods, the pod that answers is picked deterministically, and a user's
  **saved default surface** (`/surfaces/me`) wins over conversation continuity. If a
  tester's messages land in the wrong pod, that saved default is the thing to look at.

## Verify

```bash
lemma surfaces get slack          # status ACTIVE, right agent, right account
# Send a real message on the platform (DM the bot / mention in a routed channel /
# email the agent's provisioned address, which `surfaces get` prints)
lemma conversations list          # a conversation appeared, linked to the surface
lemma conversations messages <id> # the exchange is recorded
# Confirm the reply landed on-platform in the right thread/channel.
# A second message, even from a new thread, continues the same conversation.
```

## See also

- `people-outside.md` — `config.contacts.answer`: answering people who are not members privately, as contacts.

- The model → `pod-model.md` · credentials/accounts → `connectors.md`
- The agent behind the surface → `agents.md` · the paired operator UI → `apps.md`
- How its interaction tools render per platform → `agent-tools.md`
- Operate a live surface → the `lemma-user` skill
