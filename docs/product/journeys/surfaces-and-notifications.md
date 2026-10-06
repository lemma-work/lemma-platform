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

An agent reaches a platform in exactly one place: one Slack app, one WhatsApp
number, one Telegram bot. Two doors onto one platform for one agent is an
ambiguity rather than a feature, because the person on the other side has no
way to tell which one they are talking to. Several agents in a pod each get
their own, which is how a pod is reachable in more than one place at once.

The pod's own assistant is an agent like any other here. It holds surfaces on
the same terms and under the same limit — it simply starts with a mailbox
nobody had to connect. What is special about it is only how permission to
change its surfaces is checked, pod-scoped rather than agent-scoped, because
its row's id is the pod's own.

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
  not answer elsewhere. A group somebody brought the bot into — a Telegram
  group, a Slack group DM — is named by bringing it in.
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
- Where the deployment cannot deliver email to an inbox, the system shall not
  ask the sender for an address, and shall tell them to add their number to
  their profile or ask whoever runs this Lemma for an invitation.
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

### PS-SURF-007 — The owner of a Desktop install chats with the shared Telegram bot by sharing their contact
**Status:** manual

- When the deployment accepts unverified phone matches and exactly one live
  account has the sender's self-shared number on its profile, unverified, the
  system shall link that Telegram chat to that account without an email, and
  the pod's agent shall answer what they sent.
- A number verified on another account shall win, and a number claimed by more
  than one profile shall match nobody.
- A typed number or somebody else's contact shall prove nothing.

> **Verified by:** module e2e, not the scenario suite, for the reason under
> PS-SURF-004. `test_telegram_contact_claim_e2e.py` shares a contact against an
> unverified profile number and follows the replayed message to the pod's
> agent, and covers the switch, a verified owner, and a server without email.

**Contracts:** `surface.webhook.handle_platform`

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
  rather than failing silently. The one exception is a group the pod has opened
  to people outside it (PS-SURF-015), where they are answered for the pod from
  what it has marked Public and nothing else.

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

### PS-SURF-015 — Somebody outside the pod is answered in a group opened to them
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_telegram_group_outsiders_e2e.py`.
> A scenario needs the forged Telegram chat to speak in a group and to deliver
> `my_chat_member`, which it does not yet.

A group chat holds people who are not in the pod: a vendor, a client, a
colleague from another team. Members of the pod are answered there as
themselves, exactly as in private. Everybody else is answered *for the pod*.

- When a member of the pod who may configure the bot adds it to a group, the
  system shall make them the person who answers for that group's people from
  outside the pod.
- Where a group has somebody answering for it and has not been closed to
  outsiders, when somebody outside the pod addresses the bot there, the system
  shall answer them in the group.
- The system shall answer them from the conversation and from what the pod has
  marked Public, and from nothing else — not the pod's other data, not the
  answering member's own access, files, memory or connected accounts.
- The system shall keep their questions in one conversation per group that
  belongs to the member answering for it, and shall show who asked each one.
- The system shall never take anything somebody outside the pod types as that
  member deciding something: no approval, no answer to a question the bot asked
  the member.
- The system shall let the member pass a question on: the bot may message the
  member answering for the group, and no one else, and relays what they say.
- The system shall limit how often one person outside the pod, and one group in
  a day, can put the bot to work, and shall charge the group only for turns
  that run.
- Where nobody answers for a group, or it has been closed to outsiders, the
  system shall not answer people outside the pod there at all. A member who has
  left the pod answers for nobody.
- The system shall let a member who may configure a bot close all of its groups
  to people outside the pod at once.
- The system shall never answer somebody outside the pod on a coding agent's
  runtime, which holds a member's credentials in a shell of its own; it shall
  use a model that runs in Lemma, or not answer.
- The system shall treat a group on a pod's own bot or number exactly as one on
  a shared bot.

**Contracts:** `surface.webhook.handle_platform`, `agent.group.list`,
`agent.group.update`

### PS-SURF-016 — The bot remembers what was said in a group
**Status:** planned

> Proven at module level by the same test. Same reason for `planned`.

- Where a platform will not let the bot read a group's history, the system shall
  keep its own record of what the bot heard in a group the pod knows, and of
  what the bot said there.
- When the bot answers in such a group, the system shall show it the recent
  record as background, marking what was said by people outside the pod.

**Contracts:** `surface.webhook.handle_platform`

### PS-SURF-017 — A person can say something to the agent without the group reading it
**Status:** planned

> Proven at unit level by `agent/tests/unit/test_private_notes.py` and
> `agent_surfaces/tests/unit/test_private_note_runs.py`.

A conversation that lives on a chat platform can also be read and written in
Lemma, and what the agent says in it goes to the platform.

- Where a person writes in Lemma into a conversation that lives on a chat
  platform, the system shall let them mark it as a note to the agent alone.
- The system shall start the composer on a note in a group or channel, and on
  a reply in the person's own direct chat or email thread, and shall remember
  the last choice made in that conversation on that device.
- When the agent answers a note, the system shall keep that answer in Lemma and
  send nothing of it to the platform — no reply, no progress, no attachment.
- The system shall tell the agent in every later turn that the note was not seen
  in the chat, so it acts on it without repeating it there -- except in the
  person's own direct chat, where it only says the note was not sent.
- The system shall answer a note and a message for the platform in separate
  runs: a note never shapes an answer that goes to the chat, and a run that
  continues a note -- resumed, retried or following on -- stays in Lemma.

**Contracts:** `agent.conversation.message.send`

### PS-SURF-018 — An email thread with other people on it is answered like one
**Status:** planned

> Proven at unit level by `agent_surfaces/tests/unit/test_email_threads.py`.

- When the pod answers an email that other people were on, the system shall
  reply to the sender and copy the others, as a person replying to all would,
  and shall not copy more than a handful.
- Where the pod's address is only copied on an email, the system shall answer
  only if a line of it speaks to the agent by name.
- When an email reaches the pod without naming it in To or Cc -- forwarded, sent
  to an alias, or Bcc'd -- the system shall answer it.
- Where other people are on a thread, the system shall not answer a message
  that says nothing but thanks, and shall send a refusal or a sign-up reply to
  the sender alone.

**Contracts:** `surface.webhook.handle_platform`

### PS-SURF-019 — A member opens a WhatsApp group with the bot in it
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_whatsapp_groups_e2e.py`
> and `agent_surfaces/tests/unit/test_whatsapp_groups.py`. A scenario needs the
> forged WhatsApp chat to create a group and to speak in one, which it does not
> yet.

A WhatsApp bot cannot be added to a group. It can open one, and people join
by its link.

- When a member asks the bot, in their own chat with it, to open a group, the
  system shall create the group and hand back the link people join by.
- When a member who may configure the bot starts a group from the pod's groups
  in Lemma, the system shall do the same, show the group as waiting for
  WhatsApp until WhatsApp confirms it, and then show the link.
- The system shall make that member the person who answers for the group's
  people from outside the pod, as if they had brought the bot in.
- Where the member asks again for a group they already have, the system shall
  answer with that group rather than open a second one.
- The system shall answer in the group as the pod that opened it, whichever
  other pods the person asking also belongs to.
- The system shall answer in the group only when somebody addresses the bot —
  by name, by mentioning it, or by replying to it.
- The system shall send a group only what a group can show: questions,
  approvals and links as words.

**Contracts:** `surface.webhook.handle_platform`, `agent.group.list`,
`agent.group.start`

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

### PS-SURF-033 — What is meant for one person is not said in a group
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_member_reach_private_e2e.py`.

- The system shall deliver a notification meant for one person only somewhere
  private to them.
- Where the only place a person has spoken to the bot is a group, the system
  shall reach them another way -- email, or their Lemma inbox -- rather than in
  front of the group.

**Contracts:** `notification.send`

---

## Capability: Look after the pod's groups

A pod's bots end up in many groups -- WhatsApp groups a member opened, Telegram
groups somebody added the bot to, Slack channels and group DMs -- and each is a
place the pod speaks for itself. They are one list in the pod, so nobody has to
remember where the bot is, who is in there, or what is waiting on them.

### PS-SURF-040 — A pod sees every group its bots are in
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_space_groups_e2e.py`.
> A scenario needs the forged chats to create and speak in groups, which they
> do not yet.

- The system shall list, for every member of the pod, each group its bots are
  in: the platform, the group's name, who answers for its people outside the
  pod, how many people in and outside the pod have spoken there, when it was
  last active, and how many questions are waiting on the member looking.
- Where the pod keeps its own record of a group, the system shall show a member
  what was said there, marking who is in the pod, and for each of the bot's
  answers, whom it answered and whether it answered from what the pod made
  Public. An answer made with one member's own access shall show its words to
  that member alone.
- The system shall keep its record of a group for 90 days, and the bot shall say
  so in the group when it arrives.
- The system shall never show a member's private conversation with the bot, or
  a note written to the agent in Lemma, as part of a group.
- When the member who answers for a group switches its people outside the pod
  off, the system shall stop answering them there; when it is switched on with
  nobody answering for them, the system shall make the member who switched it
  the one who does.
- The system shall let only the member who answers for a group, or an admin of
  the pod, change it or take it over, and shall tell that member when an admin
  does.

**Contracts:** `agent.group.list`, `agent.group.get`, `agent.group.timeline`,
`agent.group.update`

### PS-SURF-041 — The member answering for a group sees what its people are waiting on
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_space_groups_e2e.py`.

- When the bot passes on a question it could not answer somebody outside the
  pod, the system shall show it on that group's page, to the member it was
  passed to, until they answer it.
- When the member answers, the system shall relay the answer in the group.

**Contracts:** `agent.group.get`, `notification.respond`

### PS-SURF-042 — A member adds the pod's Telegram bot to a group from Lemma
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_space_groups_e2e.py`
> and `agent_surfaces/tests/unit/test_telegram_group_links.py`.

- When a member who may configure the bot asks for it, the system shall give
  them a link that opens Telegram's own choice of group and adds the bot there.
- When the bot is added through the link, the system shall make the group the
  pod's, with that member answering for its people outside the pod, and the bot
  shall say in the group that it is there and how to ask it.
- The system shall let a link be used once, and for an hour.
- The system shall not take the Telegram account that used a link for the
  member it was made for: a link is easily passed on.

**Contracts:** `agent.group.link`, `surface.webhook.handle_platform`

### PS-SURF-043 — A Slack channel shared with another company answers that company for the pod
**Status:** planned

> Proven at unit level by `agent_surfaces/tests/unit/test_slack_connect_groups.py`.

- The system shall list the Slack channels and group DMs a pod's bot answers in
  among the pod's groups, and mark the ones shared with another company.
- Where a channel is shared with another company, when somebody from that
  company addresses the bot, the system shall answer them for the pod from what
  it made Public, as in any group opened to people outside the pod.
- The system shall never answer somebody from the pod's own Slack workspace as
  an outsider; one who is not in the pod is told how to join it instead.

**Contracts:** `surface.webhook.handle_platform`, `agent.group.list`

### PS-SURF-044 — People in a group ask the bot by the name they see
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_whatsapp_groups_e2e.py`.

- Where a platform does not mark who a message is to, the system shall treat a
  group message that speaks to the bot by the name the app shows it under -- the
  pod's own name for the pod's assistant, an agent's own name otherwise -- as
  put to the bot, and shall still answer the pod's assistant as "Lem".
- The system shall not take a name merely at the start of a line ("Sales
  numbers are up") as speaking to the bot.

**Contracts:** `surface.webhook.handle_platform`

## Contacts

A pod's own bot can answer somebody who is not a member, privately: a customer
on WhatsApp, a client by email. They are the pod's contacts. They never sign in
and are never billed, and they are answered for the pod from what it made
Public, the way a group's people from outside it are.

### PS-SURF-045 — A stranger writing to the pod's own bot is answered as a contact
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_email_contacts_e2e.py`
> and `agent_surfaces/tests/unit/test_contacts.py`. A scenario needs a forged
> inbound email or chat from an address no Lemma user has.

- Where a bot that is the pod's own -- its own bot token, its own number, its
  own email address -- is set to answer anyone, when somebody who is not a
  member writes to it privately, the system shall make them a contact of the
  pod and answer them for the pod from what it made Public.
- Where the bot is set to answer known contacts only, the system shall answer
  the pod's existing contacts and nobody else.
- Where the bot is set to answer nobody outside the pod, which is how a bot
  starts, the system shall answer only members, as before.
- The system shall never take somebody writing to Lemma's shared bot for a
  contact of any pod.
- The system shall answer a contact in a conversation of their own, which
  belongs to the member who looks after the bot's contacts -- whoever turned
  contacts on, unless somebody else was named -- and shall pass on what it
  cannot answer to that member.
- The system shall answer a contact's email to them alone, copying nobody else
  on the thread.
- The system shall treat a contact who writes in a group as one of the group's
  people from outside the pod, never as a contact: everyone in a group reads the
  answer.

**Contracts:** `surface.webhook.handle_platform`, `agent.surface.update`,
`contact.list`, `contact.get`

### PS-SURF-046 — Email nobody vouched for is never answered
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_email_contacts_e2e.py`.

- When an email to a bot that answers contacts was not authenticated by the
  receiving mail service, the system shall not reply to it, shall not make its
  sender a contact, and shall tell the member who looks after contacts once an
  hour per sender, with what it said, so they can answer by hand if it is
  genuine.

**Contracts:** `surface.webhook.handle_platform`

### PS-SURF-048 — A contact sees what is theirs, and only that
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_email_contacts_e2e.py`
> and at unit level by `agent/tests/unit/test_contact_tools.py`.

- Where a pod marks a table as its contacts' (contact-owned), the system shall
  let every member read every row of it, and let a contact's conversation read
  only the rows naming that contact.
- The system shall never let a contact's conversation read a row naming
  somebody else, however it asks, and shall report a table that is not
  contact-owned as not there.
- Where a pod opens a function to contacts, the system shall let a contact's
  conversation call it, tell the function which contact is asking, and never
  let the conversation tell it somebody else.
- The system shall let a pod's admins export what it holds about a contact --
  their handles, their words and the bot's answers -- and shall never include
  the pod's tool calls or a member's private notes.

**Contracts:** `table.create`, `table.update`, `function.contacts.update`,
`contact.export`

### PS-SURF-049 — A web widget answers visitors on the pod's behalf
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_web_widgets_e2e.py`
> and at unit level by `agent_surfaces/tests/unit/test_web_widgets.py`.

- The system shall let a pod's editors put its chat on a web page with a
  public key, or share it as a page Lemma hosts, and shall show its signing
  secret once, when it is made or rotated.
- The system shall answer an anonymous visitor from what the pod made Public,
  and shall answer a visitor named by a token the page's server signed with the
  widget's secret, or who entered a code sent to their email, as a contact --
  keeping a conversation they had already started.
- The system shall refuse a token signed with anything else, meant for another
  widget, or living longer than ten minutes, and shall stop accepting tokens
  signed with a secret once it is rotated.
- The system shall answer only pages on the widget's allowed origins, and shall
  refuse strangers at a widget that answers known contacts only.
- The system shall stream the answer to the visitor as it is written, and
  shall never send them the model's thinking, tool traffic, or anything from a
  member's private note.

**Contracts:** `agent.web_widget.create`, `agent.web_widget.update`,
`agent.web_widget.rotate_secret`, `public.web.session.start`,
`public.web.message.send`, `public.web.stream.read`, `public.web.code.verify`

### PS-SURF-050 — A member writes first to a contact only where the contact wants it
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_email_contacts_e2e.py`
> and `test_web_widgets_e2e.py`, and at unit level by
> `agent_surfaces/tests/unit/test_contact_follow_ups.py`.

- The system shall let a member write to a contact in the contact's most recent
  conversation, on the channel it lives on.
- The system shall write on WhatsApp only within 24 hours of the contact's last
  message there, and shall leave a web visitor's message for their next visit.
- The system shall end every email follow-up with a way to stop, and once the
  contact uses it, shall write to that address no more until they write again.
- The system shall not unsubscribe anybody for opening the link; only the
  button on the page it opens does.

**Contracts:** `contact.follow_up`

### PS-SURF-051 — People outside the pod add rows to a table it opened to them
**Status:** planned

> Proven at module level by `agent_surfaces/tests/e2e/test_public_rows_e2e.py`,
> and at unit level by `datastore/tests/unit/test_public_rows.py` and
> `agent/tests/unit/test_form_tools.py`.

- The system shall let a member who can change a table open it to rows from
  people outside the pod -- anyone, or confirmed contacts only -- for chosen
  columns, and shall refuse to open a per-member table or to leave closed a
  column the table cannot do without.
- The system shall let any page holding a widget's public key learn the open
  columns and add one row, as the member who opened the table, and shall
  ignore every column that is not open and read nothing back.
- The system shall ask a stranger to confirm their email before they add a row
  to a table open to contacts only, and shall name a confirmed sender on a
  contact-owned table itself, whatever the page sent.
- The system shall let the pod's chat on that page fill the form's open
  columns from the conversation, and shall never add the row for the visitor.
- The system shall stop taking rows the moment the table is closed.

**Contracts:** `table.public_rows.open`, `table.public_rows.get`,
`table.public_rows.close`, `table.public_rows.list`, `public.web.table.read`,
`public.web.row.add`

### PS-SURF-047 — Contacts cost the organization, never a member
**Status:** planned

> Proven at unit level by `usage/tests/unit/test_outside_audience_windows.py`
> and at module level by `agent_surfaces/tests/e2e/test_email_contacts_e2e.py`.

- The system shall charge what answering contacts and a group's people from
  outside the pod costs to the organization's budget, and never to the
  allowance of the member who looks after the conversation.
- The system shall let an organization's owners and editors set, read and clear
  a monthly cap on that cost, and shall stop answering contacts for the rest of
  the month once it is reached.
- The system shall let a pod's admins forget a contact, with every handle they
  are known by.

**Contracts:** `usage.organization.contacts_cap.get`,
`usage.organization.contacts_cap.update`, `contact.update`, `contact.delete`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| What the agent does with the message | [Agents and conversations](agents-and-conversations.md) |
| Firing work from an inbound webhook | [Scheduling and triggers](scheduling-and-triggers.md) |
| Connecting to a system to read or write data | [Connectors and accounts](connectors-and-accounts.md) |
| Email deliverability and verification | [Authentication hardening](../../authentication-hardening.md) |
