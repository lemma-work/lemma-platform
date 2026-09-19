# Surfaces and notifications

**Journey:** A person reaches their pod from wherever they already work, and the
pod reaches them back.

A surface belongs to an agent. It connects that agent to an outside platform —
Slack, Microsoft Teams, Telegram, WhatsApp, or email — and a person messages the
agent there and gets an answer there, in the same thread, without opening Lemma.

The agent is the owner, not the pod. Surfaces have their own APIs and their own
screen, which can make them look like a pod-level resource; they are not. Every
surface names exactly one agent, that agent answers on it, and a pod reaches
someone only through the agents inside it. Where this document says a pod is
reachable somewhere, that is shorthand for one of its agents being reachable
there.

Two rules run through everything here. **A surface is a door, not a hole**: who
someone is on Slack has to resolve to who they are in Lemma before they get
anything, and a person who is not entitled to the pod gets nothing.
**Every platform gets the full product**: asking a question, approving an action,
sending a file — if it works in the workspace it works on the surface, natively
where the platform supports it and as plain text where it does not, but never
dropped. Email included: it receives one message per turn rather than several,
so a question travels inside the reply and the person answers by replying — but
it is asked, not skipped.

---

## Capability: Connect an agent to a platform

### PS-SURF-001 — A person connects an agent to a further platform
**Status:** covered

> "A person connects a surface" describes the second and subsequent ones, not
> the first. That was an open spec question; this is the answer to it, and the
> promise below is written to match. A pod is never connected
> to nothing: creating one mints its assistant's mailbox, so `agent.surface.list`
> answers with a `resend` surface before anyone opens the surfaces screen. That
> is the behaviour we want — an agent with no other way to reach anyone should
> still have an address — so the promise below is about the second and
> subsequent platforms, and the scenarios say "nothing a person connected"
> rather than "no surfaces at all".

- Every pod shall start with its assistant reachable at an address nobody had to
  connect.
- When a person connects a surface for a further platform and binds it to an
  agent, the system shall start accepting messages for that pod on that
  platform.
- A surface shall answer as exactly one agent. Where a person wants a second
  agent reachable on a platform, the system shall let them make that agent its
  own bot rather than sharing one.
- Where a platform has channels or groups, the system shall treat the ones a
  person names as the places that surface's agent may be spoken to, and shall
  not answer elsewhere.
- When a surface is connected, the system shall record `surface.connected`.
- The system shall tell a person what is still needed to finish setup, at each
  step, rather than failing at the first message.
- The system shall let a person see which platforms are available to connect and
  which are already connected.

**Contracts:** `agent.surface.create`, `agent.surface.get`, `agent.surface.list`, `agent.surface.available`, `agent.surface.setup`, `agent.surface.setup_guide`, `surface.connected`

### PS-SURF-002 — Setting up a platform does not require reading its documentation
**Status:** covered

- Where a platform needs an app definition, the system shall generate it rather
  than asking a person to write one.
- Where a platform gives one app one identity, the system shall name the
  generated definition after the agent it is being made for, so a bot made for
  one agent arrives under that agent's name.
- Where a platform needs administrator consent, the system shall carry the
  person through it and shall report when it has been granted.
- The system shall let a person set up a bot for a platform without leaving
  Lemma, where the platform allows it.

**Contracts:** `agent.surface.slack_manifest`, `agent.surface.telegram_managed.start`, `agent.surface.telegram_managed.get`, `agent.surface.teams_admin_consent_callback`

### PS-SURF-003 — A person changes or removes a surface
**Status:** covered

- When a person points a surface at a different agent, the system shall route
  later messages to the new agent and shall leave existing threads readable,
  under the name that answered them at the time.
- When a person deletes a surface, the system shall stop accepting messages on
  it.
- When a pod is deleted, the system shall stop every surface belonging to the
  agents inside it.

**Contracts:** `agent.surface.update`, `agent.surface.delete`, `pod.delete`

---

## Capability: Start privately through chat

### PS-SURF-004 — A stranger on a shared bot proves who they are before getting a workspace
**Status:** manual

- Where a person messages a shared Lemma WhatsApp or Telegram bot and resolves
  to no Lemma user, the system shall verify their identity privately and then
  give them a personal assistant to talk to.
- The system shall take WhatsApp's signed sender phone as proof of that number.
- On Telegram the system shall require a contact shared by the sender
  themselves, and shall treat neither a username nor a typed number as proof.
- Where the sender is still unknown after that, the system shall verify their
  mailbox with an email code before provisioning anything.
- A bot connected with a customer's own credentials shall keep its existing pod
  access boundaries, and inbound email shall not create an account.

> **Verified by:** module e2e, not the scenario suite. Signup here is a
> conversation with a platform, and the suite has no shared WhatsApp number or
> Telegram bot to hold one with — the Telegram scenarios drive a local server
> standing in for a *connected* bot, which is the case this promise excludes.
> `app/modules/agent_surfaces/tests/e2e/test_central_chat_onboarding_e2e.py`
> runs the WhatsApp provision-and-replay path, the Telegram contact
> requirement, and phone-binding revocation against real Postgres and Redis.

**Contracts:** `surface.webhook.handle_platform`, `users.ensure_first_workspace`

### PS-SURF-005 — Signup inside a company installation stays inside that company
**Status:** manual

- Where signup begins from a Slack or Teams installation, the system shall
  conduct it in a private conversation belonging to that installation.
- The system shall select only that installation's organization, and shall let
  that organization's existing membership policy decide whether the verified
  person may join.
- If membership is refused, then the system shall tell the person their account
  is ready and to ask their team administrator for access.
- When the person returns after access is granted, the system shall resume setup
  without asking for another email code, while their verified identity holds.
- The installation's credentials and its existing channel routes shall stay in
  the organization that owns them.

> **Verified by:** module e2e, not the scenario suite, for the reason under
> PS-SURF-004 — and here the private conversation is one the platform opens on
> request, which the suite has no installation to ask.
> `test_slack_onboarding_e2e.py` covers the refused-then-granted resume, and
> `test_teams_onboarding_e2e.py` the private-card path.

**Contracts:** `surface.webhook.handle_platform`, `users.ensure_first_workspace`

### PS-SURF-006 — Nothing about signup appears in a channel
**Status:** manual

- A channel mention may begin private setup, but no email address, code,
  account status or onboarding reply shall appear in the channel.
- After setup, the system shall replay only the message that began it and that
  message's attachments, and shall copy neither channel history nor the channel
  pod's private resources.
- If a request expires before setup finishes, then the system shall discard it
  and ask the person for a new one.
- If the private handoff fails, then the system shall not fall back to answering
  in the channel.

> **Verified by:** module e2e, not the scenario suite, for the reason under
> PS-SURF-004. `test_slack_onboarding_e2e.py` asserts what the channel does and
> does not receive, and `test_central_chat_onboarding_e2e.py` asserts what the
> replayed request carries.

**Contracts:** `surface.webhook.handle_platform`, `agent.conversation.get`

---

## Capability: Receive a message from outside

### PS-SURF-010 — Only genuine messages from the platform are acted on
**Status:** covered

- The system shall verify every inbound message is genuinely from the platform
  it claims to be from, before acting on it.
- If a message fails verification, then the system shall reject it and shall not
  start any work.
- The system shall answer a platform's verification challenge without a signed-in
  person, because the platform cannot sign in.
- The system shall accept an inbound message quickly and do the work afterwards,
  so that a slow agent does not cause the platform to retry.

**Contracts:** `surface.webhook.handle_platform`, `surface.webhook.handle_surface`, `surface.webhook.verify`, `surface.webhook.verify_surface`

### PS-SURF-011 — The same message delivered twice is answered once
**Status:** covered

- If a platform delivers the same message more than once, then the system shall
  act on it once.
- The system shall keep that guarantee across a restart.

**Contracts:** `surface.webhook.handle_platform`, `surface.webhook.handle_surface`

### PS-SURF-012 — A person on a platform is resolved to who they are in Lemma
**Status:** covered

- When a message arrives from an external identity, the system shall resolve it
  to a Lemma user where one exists, and shall keep that resolution stable across
  later messages.
- The system shall give a resolved person exactly the access their Lemma
  identity has, and no more — being present in the Slack channel shall not by
  itself grant access to the pod.
- If a message arrives from someone with no access to the pod, then the system
  shall not answer with pod content, and shall tell them how to get access
  rather than failing silently.

**Contracts:** `surface.webhook.handle_platform`, `agent.surface.list_mine`

### PS-SURF-013 — A thread on the platform is a conversation in the pod
**Status:** covered

- When a person replies in a thread, the system shall continue the same
  conversation rather than starting a new one.
- When a person starts a new thread, the system shall start a new conversation.
- The system shall make the conversation readable in the workspace, with the
  surface and thread it came from recorded on it.

**Contracts:** `agent.conversation.get`, `agent.conversation.list`

### PS-SURF-014 — A file sent to a surface reaches the pod
**Status:** covered

- When a person attaches a file to a message, the system shall make it available
  to the agent handling that message.
- Where an attachment is a voice message, the system shall transcribe it so the
  agent receives what was said.
- The system shall bound the size of an attachment it will take, and shall say
  so rather than failing the whole message.

**Contracts:** `surface.webhook.handle_platform`, `file.upload`

---

## Capability: Answer on the platform

### PS-SURF-020 — The answer comes back where the question was asked
**Status:** covered

- When an agent answers a message from a surface, the system shall deliver the
  answer in the same channel and thread.
- When an answer is delivered, the system shall record
  `surface.message_answered`.
- While an agent is working, the system shall show the person that something is
  happening, in whatever way the platform supports.
- If delivery to the platform fails, then the system shall record the failure
  rather than dropping it, and shall leave the conversation readable in the
  workspace.

**Contracts:** `agent.surface.send`, `surface.message_answered`

### PS-SURF-021 — Questions and approvals work on every platform
**Status:** covered

- When an agent asks a person to choose, the system shall present the choices
  natively where the platform supports buttons, and as readable text where it
  does not.
- When an agent asks for approval, the system shall present approve and deny
  natively where the platform supports it, and as readable text where it does
  not.
- The system shall accept the person's response either way — by pressing the
  native control or by typing the answer.
- The system shall never drop a question or an approval because the platform
  lacks native support for it.

**Contracts:** `agent.surface.send`, `agent.conversation.approval.resolve`

### PS-SURF-022 — Email surfaces behave like email
**Status:** manual

- Where a surface is email, the system shall reply to the sender in the same
  email thread, with a subject a person recognises.
- The system shall give each agent its own inbound address, and shall route mail
  to exactly the pod that address belongs to.
- If an inbound email cannot be read completely, then the system shall drop it
  rather than starting an agent on a partial message.
- Where a surface is email, the system shall compose everything one turn produces
  into a single reply — the answer, any file shown, anything said aloud, and any
  question asked.
- Where an agent asks a question or requests approval on an email surface, the
  system shall put it in that reply and shall accept the person's emailed answer
  as the response.
- Where an inbound email's sender cannot be authenticated by the receiving mail
  service, the system shall not resolve it to a member's identity.

> **Verified by:** scenarios for everything except the reply. Addressing,
> routing to the pod that owns the address, refusing mail no surface owns,
> and refusing an unsigned delivery all run in the suite. What a reply looks
> like — same thread, recognisable subject — cannot be: a Resend surface
> authenticates with the deployment's own API key and has no `api_base_url`
> override, so outbound mail can only go to Resend itself. There is nothing
> to point at a local server the way the Telegram scenarios do. That half
> belongs to the live lane, against a real key.

**Contracts:** `agent.surface.create`, `surface.webhook.handle_platform`, `agent.surface.send`

### PS-SURF-023 — A person reachable from several pods is reached by the right one
**Status:** covered

> This promise used to conflate two things. An agent reaches out through **its
> own** surfaces — that is by design, not a shortfall: an agent can only speak
> where it has been given a voice, and a person's preference has no bearing on
> which platforms an agent was connected to. The preference answers a different
> question, and only that one: where the same person is reachable from two
> organizations that both use a Lemma-native account on one platform, which of
> them reaches them there. It selects between pods on a platform; it does not
> select the platform.

- An agent shall reach a person through a surface that agent has, and shall not
  borrow another agent's.
- Where a person is reachable from several pods on one platform and has chosen
  which should reach them, the system shall use that choice.
- When a person changes that choice, the system shall use the new one from then
  on.
- Where a pod's bot serves several organizations, the system shall route each
  person to the right one and shall keep that routing stable.
- The system shall never let one organization's thread appear in another's.

**Contracts:** `agent.surface.list_mine`, `agent.surface.set_my_default`, `agent.surface.channels`

---

## Capability: Be told when something needs you

### PS-SURF-030 — A person has one place to see what needs them
**Status:** covered

- When something in a pod needs a person's attention, the system shall put it in
  their notifications for that pod.
- The system shall show a person how many notifications they have not read,
  without them opening the list.
- The system shall group a thread of related notifications as one item, rather
  than one item per message.
- If someone who does not belong to the pod asks for its notifications, then the
  system shall refuse, as it refuses every other read in that pod.

**Contracts:** `notification.list`, `notification.unread_count`, `notification.send`

### PS-SURF-031 — A person clears what they have dealt with
**Status:** covered

- When a person reads a notification, the system shall mark it read and shall
  reflect that in the unread count.
- When a person marks everything read, the system shall clear the unread count
  for that pod.
- The system shall keep read state per person, so one person reading something
  does not clear it for everyone.

**Contracts:** `notification.mark_read`, `notification.mark_all_read`, `notification.unread_count`

### PS-SURF-032 — A person can answer from the notification
**Status:** covered

- Where a notification asks something, the system shall let a person answer it
  directly and shall carry the answer back to whatever is waiting.
- When a person answers or acknowledges a notification, the system shall stop
  asking.
- If a person answers something that has already been answered or has expired,
  then the system shall say so rather than accepting an answer that goes
  nowhere.

**Contracts:** `notification.respond`, `notification.acknowledge`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| What the agent does with the message | [Agents and conversations](agents-and-conversations.md) |
| Firing work from an inbound webhook | [Scheduling and triggers](scheduling-and-triggers.md) |
| Connecting to a system to read or write data | [Connectors and accounts](connectors-and-accounts.md) |
| Email deliverability and verification | [Authentication hardening](../../authentication-hardening.md) |
