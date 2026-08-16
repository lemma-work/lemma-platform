# Surfaces and notifications

**Journey:** A person reaches their pod from wherever they already work, and the
pod reaches them back.

A surface connects a pod's agent to an outside platform — Slack, Microsoft
Teams, Telegram, WhatsApp, Gmail, Outlook, or plain email. A person messages the
agent there and gets an answer there, in the same thread, without opening Lemma.

Two rules run through everything here. **A surface is a door, not a hole**: who
someone is on Slack has to resolve to who they are in Lemma before they get
anything, and a person who is not entitled to the pod gets nothing.
**Every platform gets the full product**: asking a question, approving an action,
sending a file — if it works in the workspace it works on the surface, natively
where the platform supports it and as plain text where it does not, but never
dropped.

---

## Capability: Connect a pod to a platform

### PS-SURF-001 — A person connects a pod's agent to a platform
**Status:** planned

- When a person connects a surface for a platform and binds it to an agent, the
  system shall start accepting messages for that pod on that platform.
- When a surface is connected, the system shall record `surface.connected`.
- The system shall tell a person what is still needed to finish setup, at each
  step, rather than failing at the first message.
- The system shall let a person see which platforms are available to connect and
  which are already connected.

**Contracts:** `agent.surface.create`, `agent.surface.get`, `agent.surface.list`, `agent.surface.available`, `agent.surface.setup`, `agent.surface.setup_guide`, `surface.connected`

### PS-SURF-002 — Setting up a platform does not require reading its documentation
**Status:** planned

- Where a platform needs an app definition, the system shall generate it rather
  than asking a person to write one.
- Where a platform needs administrator consent, the system shall carry the
  person through it and shall report when it has been granted.
- The system shall let a person set up a bot for a platform without leaving
  Lemma, where the platform allows it.

**Contracts:** `agent.surface.slack_manifest`, `agent.surface.telegram_managed.start`, `agent.surface.telegram_managed.get`, `agent.surface.teams_admin_consent_callback`

### PS-SURF-003 — A person changes or removes a surface
**Status:** planned

- When a person points a surface at a different agent, the system shall route
  later messages to the new agent and shall leave existing threads readable.
- When a person deletes a surface, the system shall stop accepting messages on
  it.
- When a pod is deleted, the system shall stop every surface belonging to it.

**Contracts:** `agent.surface.update`, `agent.surface.delete`, `pod.delete`

---

## Capability: Receive a message from outside

### PS-SURF-010 — Only genuine messages from the platform are acted on
**Status:** planned

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
**Status:** planned

- If a platform delivers the same message more than once, then the system shall
  act on it once.
- The system shall keep that guarantee across a restart.

**Contracts:** `surface.webhook.handle_platform`, `surface.webhook.handle_surface`

### PS-SURF-012 — A person on a platform is resolved to who they are in Lemma
**Status:** planned

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
**Status:** planned

- When a person replies in a thread, the system shall continue the same
  conversation rather than starting a new one.
- When a person starts a new thread, the system shall start a new conversation.
- The system shall make the conversation readable in the workspace, with the
  surface and thread it came from recorded on it.

**Contracts:** `agent.conversation.get`, `agent.conversation.list`

### PS-SURF-014 — A file sent to a surface reaches the pod
**Status:** planned

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
**Status:** planned

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
**Status:** planned

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
**Status:** planned

- Where a surface is email, the system shall reply to the sender in the same
  email thread, with a subject a person recognises.
- The system shall give each agent its own inbound address, and shall route mail
  to exactly the pod that address belongs to.
- If an inbound email cannot be read completely, then the system shall drop it
  rather than starting an agent on a partial message.

**Contracts:** `agent.surface.create`, `surface.webhook.handle_platform`, `agent.surface.send`

### PS-SURF-023 — A person reached on several platforms gets one predictable answer
**Status:** planned

- Where a person has chosen a default surface, the system shall reach them there
  when it starts the contact, whatever platform any earlier conversation used.
- When a person changes their default surface, the system shall use the new one
  from then on.
- Where a pod's bot serves several organizations, the system shall route each
  person to the right one and shall keep that routing stable.
- The system shall never let one organization's thread appear in another's.

**Contracts:** `agent.surface.list_mine`, `agent.surface.set_my_default`, `agent.surface.channels`

---

## Capability: Be told when something needs you

### PS-SURF-030 — A person has one place to see what needs them
**Status:** planned

- When something in a pod needs a person's attention, the system shall put it in
  their notifications for that pod.
- The system shall show a person how many notifications they have not read,
  without them opening the list.
- The system shall group a thread of related notifications as one item, rather
  than one item per message.

**Contracts:** `notification.list`, `notification.unread_count`, `notification.send`

### PS-SURF-031 — A person clears what they have dealt with
**Status:** planned

- When a person reads a notification, the system shall mark it read and shall
  reflect that in the unread count.
- When a person marks everything read, the system shall clear the unread count
  for that pod.
- The system shall keep read state per person, so one person reading something
  does not clear it for everyone.

**Contracts:** `notification.mark_read`, `notification.mark_all_read`, `notification.unread_count`

### PS-SURF-032 — A person can answer from the notification
**Status:** planned

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
