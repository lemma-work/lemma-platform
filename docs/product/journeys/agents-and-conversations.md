# Agents and conversations

**Journey:** A person puts an agent on the work in a pod, talks to it, and stays
in control of what it does.

An agent is a named thing in a pod with instructions, a set of tools, and a
model behind it. A conversation is a thread with one. Everything an agent can
reach — tables, files, functions, workflows, connectors — is something a person
granted it, and everything it does is attributable to the person it acted for.

The promise here is control without supervision: a person should be able to hand
an agent real work, walk away, and be confident it cannot do anything they did
not permit — and that anything genuinely consequential comes back to them before
it happens.

---

## Capability: Define an agent

### PS-AGENT-001 — A person creates an agent and gives it a job
**Status:** covered

- When a person creates an agent with instructions, the system shall make it
  available in that pod.
- When an agent is created, the system shall record `agent.created`.
- The system shall let a person choose which sets of tools an agent may use, and
  shall give it no tools beyond those.
- Where an agent declares the shape of its output, the system shall hold its
  answers to that shape.
- If a person creates an agent whose name collides with an existing agent in the
  pod, then the system shall refuse.

**Contracts:** `agent.create`, `agent.get`, `agent.list`, `agent.created`

### PS-AGENT-002 — An agent gets only the access it was granted
**Status:** covered

- When a person grants an agent access to a table, a file, a function, or a
  workflow, the system shall allow exactly that and refuse everything else.
- When a person asks what an agent may reach, the system shall answer completely
  — every resource and every permission on it.
- While an agent acts on a person's behalf, the system shall give it no more
  access than that person has, even where the agent's own grants allow more.
- When a person changes an agent's grants, the system shall apply the change to
  the next run and not to one already in flight.

**Contracts:** `agent.permissions.get`, `agent.permissions.replace`

### PS-AGENT-003 — A pod has an agent without anyone creating one
**Status:** covered

- The system shall give every pod a default agent, so a person can ask a
  question before building anything.
- The system shall give the default agent exactly the access of whoever is
  talking to it, and no standing access of its own.
- When a person deletes an agent they created, the system shall keep the default
  agent available.

**Contracts:** `agent.conversation.create`, `agent.list`

### PS-AGENT-005 — A person gives an agent a memory
**Status:** covered

- When a person gives an agent memory, the system shall let it keep durable
  facts between conversations — shared with the pod, or private to the person it
  learned them from.
- When a person gives an agent memory, the system shall also give it the access
  it needs to write there, so the capability works without a second step.
- When a person takes memory away, the system shall take that access back.
- The system shall not give an agent memory it was never granted, and shall not
  carry one person's private facts into another person's conversation.
- The system shall bound how much remembered text is loaded into a run, so one
  overlong note cannot crowd out the rest.

**Contracts:** `agent.create`, `agent.update`, `agent.permissions.get`

### PS-AGENT-004 — A person chooses which model an agent uses
**Status:** covered

- Where an organization configures its own model provider, the system shall let
  its agents use it.
- Where an agent pins no model, the system shall fall back to the pod's default,
  and then to the deployment's.
- The system shall keep provider credentials secret — never returning them to
  any client, in any response, at any privilege level.
- If a configured provider is unreachable, then the system shall fail the run
  with a message saying the provider failed, rather than silently using a
  different one.

**Contracts:** `agent.runtime.profiles.create`, `agent.runtime.profiles.list`, `agent.runtime.profiles.update`, `agent.update`

---

## Capability: Talk to an agent

### PS-AGENT-010 — A person starts a conversation and gets an answer
**Status:** covered

- When a person starts a conversation with an agent, the system shall create the
  thread and record `conversation.started`.
- When a person sends a message, the system shall run the agent and persist both
  the question and the answer to the thread.
- When a run reaches a conclusion, the system shall record
  `agent_run.completed` with how it ended and what it cost.
- The system shall keep the full history of a conversation readable afterwards,
  including what tools were called and what they returned.

**Contracts:** `agent.conversation.create`, `agent.conversation.message.send`, `agent.conversation.message.list`, `conversation.started`, `agent_run.completed`

### PS-AGENT-011 — A person watches the answer arrive
**Status:** covered

- While an agent is working, the system shall stream its output to a person
  watching, as it is produced.
- The system shall stream what the agent is doing, not only what it is saying,
  so a person can see a long tool call is in progress rather than assuming it
  has stalled.
- When a watcher connects to a run already in progress, the system shall give
  them what has happened so far and then continue live.
- When a run ends, the system shall close the stream.

**Contracts:** `agent.conversation.stream`, `agent.conversation.get`

### PS-AGENT-012 — A person can stop an agent
**Status:** covered

- When a person stops a run, the system shall stop it and shall keep whatever
  the agent had produced up to that point.
- When a person stops a run, the system shall stop the work it had started where
  it can, and shall leave the conversation usable for the next message.
- The system shall never leave a stopped run appearing to still be working.
- If a person stops a run that has already finished, then the system shall leave
  its result unchanged.

**Contracts:** `agent.conversation.stop`, `agent.conversation.get`

### PS-AGENT-013 — A failed run can be tried again
**Status:** covered

- When a person retries a failed run, the system shall run it again from the
  last message rather than from the beginning of the conversation.
- The system shall not duplicate the work a failed run already completed where
  that work had lasting effects.
- If a person retries a run that did not fail, then the system shall refuse.

**Contracts:** `agent.conversation.retry`, `agent.conversation.get`

### PS-AGENT-014 — A conversation is private to the pod
**Status:** covered

- The system shall show a conversation only to people entitled to it in its pod.
- If a person from another pod or another organization attempts to read a
  conversation, then the system shall refuse and shall not reveal that it exists.

**Contracts:** `agent.conversation.get`, `agent.conversation.list`

---

## Capability: Stay in control of what the agent does

### PS-AGENT-020 — Consequential actions come back to a person first
**Status:** covered

- If an agent attempts something destructive — dropping a table, deleting
  records in bulk, sending a message outside the pod — then the system shall
  pause the run and ask a person before doing it.
- While a run is paused for approval, the system shall keep it paused
  indefinitely rather than timing out and proceeding.
- When a person approves, the system shall resume the run and perform exactly
  the action that was described to them.
- When a person denies, the system shall resume the run without performing the
  action and shall let the agent report that it was refused.
- Where a person approves an action for the rest of the session, the system
  shall stop asking for that action in that session only, and shall ask again in
  the next one.

**Contracts:** `agent.conversation.approval.list`, `agent.conversation.approval.resolve`, `agent.conversation.get`

### PS-AGENT-021 — An agent can ask a person a question mid-run
**Status:** covered

- When an agent needs something only a person can supply, the system shall pause
  the run and present the question.
- When the person answers, the system shall resume the run with their answer.
- The system shall present the question wherever the conversation is happening,
  including on an external surface, rather than only in the workspace.

**Contracts:** `agent.conversation.message.send`, `agent.conversation.get`

### PS-AGENT-022 — Every action is attributable
**Status:** covered

- The system shall record, for every action an agent takes, which agent took it
  and on whose behalf.
- The system shall keep approvals and denials as a durable record, readable
  afterwards.
- The system shall never let an agent's action appear to have been taken
  directly by a person.

**Contracts:** `agent.conversation.approval.list`, `agent.conversation.message.list`

---

## Capability: Give an agent more than one thing to do

### PS-AGENT-030 — An agent can delegate to a subagent
**Status:** covered

- Where an agent starts a subagent, the system shall run it as a child of the
  original conversation and shall return its result to the parent.
- The system shall give a subagent no more access than its parent has.
- The system shall show a person the subagent's work as part of the parent
  conversation, rather than hiding it.

**Contracts:** `agent.conversation.create`, `agent.conversation.get`

### PS-AGENT-031 — An agent can show a person something interactive
**Status:** covered

- Where an agent produces an interactive result, the system shall render it in
  the conversation and shall let a person interact with it.
- The system shall scope access to such a result to the conversation it belongs
  to, and shall expire it.
- If someone without access to the conversation opens the result, then the
  system shall refuse.

**Contracts:** `widget.embed_token`, `agent.conversation.get`

---

## Capability: Run an agent on your own machine

### PS-AGENT-040 — A person pairs a local agent host with their account
**Status:** covered

- When a person pairs an agent host, the system shall bind it to their account
  and shall let them see it in their list of hosts.
- The system shall let a person revoke a host at any time, from either end.
- When a host is revoked, the system shall stop dispatching work to it
  immediately.

**Contracts:** `agent.host.pairing.create`, `agent.host.pairing.complete`, `agent.host.list`, `agent.host.revoke`, `agent.host.self_revoke`

### PS-AGENT-041 — Work dispatched to a host runs exactly once
**Status:** covered

- The system shall dispatch a given run to exactly one host, even when a host
  reconnects while that run is in flight.
- If a host disappears mid-run, then the system shall mark the run failed rather
  than leaving it appearing to run.
- The system shall show a person what their host is running and what it has run.

**Contracts:** `agent.host.poll`, `agent.host.events.append`, `agent.host.harnesses.list`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Reaching an agent from Slack, Teams, email | [Surfaces and notifications](surfaces-and-notifications.md) |
| What a function or workflow the agent calls does | [Automating work](automating-work.md) |
| Granting an agent access to one table | [Sharing and permissions](sharing-and-permissions.md) |
| Model spend and limits | [Operating a deployment](operating-a-deployment.md) |
