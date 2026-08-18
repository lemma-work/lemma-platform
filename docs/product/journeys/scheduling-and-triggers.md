# Scheduling and triggers

**Journey:** A person stops having to be present for the work to happen.

A schedule connects something that happens — a time arriving, a webhook landing,
a record changing — to something the pod does: an agent, a workflow, or a
message on a surface. It is the difference between a pod that answers when asked
and a pod that gets on with it.

The promise here is about a specific kind of trust. A person who sets a schedule
and stops watching is relying on the system to fire when it said it would, to do
the work exactly once even when things go wrong underneath, and to be honest
about it when it eventually cannot.

---

## Capability: Make work happen on a timer

### PS-SCHED-001 — A person schedules work for a time or a repeat
**Status:** covered

- When a person creates a schedule for a repeating time, the system shall fire
  it at each occurrence.
- When a person creates a schedule for a single moment, the system shall fire it
  once and then stop.
- When a schedule is created, the system shall record `schedule.created` with
  what kind of trigger it uses.
- If a person creates a schedule whose timing cannot be interpreted, then the
  system shall refuse at creation rather than accepting it and never firing.
- The system shall interpret a schedule's timing in a time zone the person
  chose, and shall keep firing correctly across daylight-saving changes.

**Contracts:** `schedule.create`, `schedule.get`, `schedule.created`

### PS-SCHED-002 — A person can pause a schedule without losing it
**Status:** covered

- When a person deactivates a schedule, the system shall stop firing it and
  shall keep its definition and history.
- When a person reactivates a schedule, the system shall resume firing from that
  point and shall not replay the occurrences it missed while paused.
- When a person changes a schedule's timing, the system shall apply the new
  timing to the next occurrence.

**Contracts:** `schedule.update`, `schedule.get`, `schedule.list`

### PS-SCHED-003 — Deleting a schedule stops it everywhere
**Status:** covered

- When a person deletes a schedule, the system shall stop firing it, including
  in whatever component actually holds the timer.
- When a pod is deleted, the system shall stop every schedule in it.
- The system shall leave no timer running for a schedule that no longer exists.

**Contracts:** `schedule.delete`, `pod.delete`

---

## Capability: React to something happening

### PS-SCHED-010 — A pod reacts to a webhook from outside
**Status:** covered

- When a webhook arrives for a source a pod is listening to, the system shall
  match it to the schedules waiting for it and fire them.
- The system shall verify a webhook is genuinely from the source it claims,
  before acting on it.
- If a webhook arrives that matches no schedule, then the system shall accept
  and discard it, rather than erroring at the sender.
- The system shall answer a provider's verification challenge without a person
  having to be signed in, because the provider cannot sign in.

**Contracts:** `schedule.create`, `surface.webhook.verify`

### PS-SCHED-011 — A pod reacts to its own data changing
**Status:** covered

- When a record is added, changed, or removed in a table a schedule is watching,
  the system shall fire that schedule.
- The system shall carry the changed record to whatever the schedule triggers,
  so the work does not have to go looking for it.
- The system shall run the triggered work with the authority of the person who
  owns the changed row, so that a data-triggered action sees exactly what that
  person would see.

**Contracts:** `schedule.create`, `record.create`, `record.update`, `record.delete`

### PS-SCHED-012 — A person can narrow what actually triggers
**Status:** covered

- Where a schedule carries a condition, the system shall evaluate it before
  triggering the work and shall skip the trigger when it does not hold.
- The system shall record a skipped trigger as skipped, distinctly from one that
  never arrived and one that failed, so a person can tell the three apart.
- If evaluating the condition fails, then the system shall treat the trigger as
  failed rather than silently skipping it.

**Contracts:** `schedule.create`, `schedule.run.list`

---

## Capability: Trust that it ran

### PS-SCHED-020 — Work fires once, however many times the trigger arrives
**Status:** covered

- The system shall do the work once for a given trigger, even when the trigger
  is delivered more than once.
- The system shall keep that guarantee across a restart of any component
  involved.
- If two deliveries of the same trigger race each other, then the system shall
  let exactly one proceed and shall record the other as a duplicate.

**Contracts:** `schedule.run.list`, `schedule_run.completed`

### PS-SCHED-021 — A person can see every firing and how it went
**Status:** covered

- When a person asks for a schedule's history, the system shall list each
  firing, when it happened, and how it ended.
- The system shall distinguish "the work was started" from "the work finished",
  because a schedule's job ends at handing over and a person still needs to know
  what happened after.
- When a firing reaches a conclusion, the system shall record
  `schedule_run.completed` with the outcome.

**Contracts:** `schedule.run.list`, `schedule.get`, `schedule_run.completed`

### PS-SCHED-022 — A firing that fails is retried, and then given up on visibly
**Status:** covered

- If a firing fails for a reason that might pass, then the system shall retry it.
- If a firing keeps failing, then the system shall stop retrying and shall mark
  it as given up rather than retrying forever.
- When a person retries a failed firing by hand, the system shall run it again
  and shall record it as a fresh attempt.

**Contracts:** `schedule.run.retry`, `schedule.run.list`

### PS-SCHED-023 — A schedule that keeps failing is turned off and reported
**Status:** covered

- If a schedule fails repeatedly in a row, then the system shall deactivate it
  rather than continuing to fire work that cannot succeed.
- When the system deactivates a schedule, it shall tell the people responsible
  for the pod, and shall say why.
- The system shall never deactivate a schedule silently.

**Contracts:** `schedule.get`, `schedule.list`, `notification.send`

---

## Capability: Choose what the trigger does

### PS-SCHED-030 — A schedule can drive an agent, a workflow, or a message
**Status:** covered

- When a schedule fires at an agent, the system shall start a conversation with
  it carrying whatever triggered the schedule.
- When a schedule fires at a workflow, the system shall start a run of it
  carrying the same.
- When a schedule fires at a surface, the system shall deliver the message to
  that surface.
- If a schedule's target no longer exists, then the system shall record the
  firing as failed and shall say the target is missing, rather than failing
  silently.

**Contracts:** `schedule.create`, `workflow.run.create`, `agent.conversation.create`, `agent.surface.send`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| What the workflow does once started | [Automating work](automating-work.md) |
| What the agent does once started | [Agents and conversations](agents-and-conversations.md) |
| Where a surface message ends up | [Surfaces and notifications](surfaces-and-notifications.md) |
| Which records a schedule may read | [Sharing and permissions](sharing-and-permissions.md) |
