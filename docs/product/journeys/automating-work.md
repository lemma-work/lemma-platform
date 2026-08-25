# Automating work

**Journey:** A person turns a thing they do by hand into a thing the pod does —
first as a single step, then as a sequence with decisions and people in it.

Two building blocks, and the difference between them is the point. A **function**
is one piece of code that takes an input and produces an output. A **workflow**
is a graph that strings functions, agents, decisions, and human input together
and survives waiting.

The promise underneath both: code a person writes runs in isolation, with
exactly the access that person granted it and no more, and a run that starts
always reaches a conclusion a person can see.

---

## Capability: Write a function

### PS-FUNC-001 — A person creates a function and runs it
**Status:** covered

- When a person creates a function with code and a declared input, the system
  shall make it runnable in that pod.
- When a function is created, the system shall record `function.created`.
- When a person runs a function with a matching input, the system shall execute
  it and return its output.
- If a person runs a function with an input that does not match what it
  declared, then the system shall refuse before executing it and shall say which
  part of the input was wrong.

**Contracts:** `function.create`, `function.run`, `function.get`, `function.created`

### PS-FUNC-002 — A function runs isolated from everything else
**Status:** covered

- The system shall run every function in an isolated sandbox, so that code in
  one pod cannot read another pod's data, reach another pod's sandbox, or affect
  the platform running it.
- The system shall give a function no credentials beyond the ones granted to it.
- If a function attempts to reach something it was not granted, then the system
  shall refuse the attempt and shall let the function see the refusal, so it can
  report a useful error rather than hanging.

**Contracts:** `function.run`, `function.permissions.get`

### PS-FUNC-003 — A function gets only the access it was granted
**Status:** covered

- When a person grants a function access to a table, a file, or another
  function, the system shall allow exactly that and refuse everything else.
- While a function runs on a person's behalf, the system shall give it no more
  access than that person has, even where its own grants would allow more.
- If a function performs a destructive act — dropping a table, deleting records
  in bulk — then the system shall require either a standing grant for that act
  or a person's approval at the time.
- When a person changes a function's grants, the system shall apply the change
  to the next run and not to one already in flight.

**Contracts:** `function.permissions.replace`, `function.permissions.get`, `function.run`

### PS-FUNC-004 — A person can change a function without breaking what is running
**Status:** covered

- When a person updates a function's code, the system shall keep every earlier
  version, so a run can be traced to the exact code that produced it.
- While a run is in flight, the system shall keep it on the version it started
  with.
- When a person deletes a function, the system shall stop it being runnable and
  shall keep the history of what it did.

**Contracts:** `function.update`, `function.delete`, `function.run.get`

---

## Capability: Run a function and see what happened

### PS-FUNC-010 — A quick function answers immediately
**Status:** covered

- Where a function is meant to answer in the moment, the system shall run it and
  return its result in the same request.
- The system shall bound how long such a function may run, and shall report a
  timeout as a failed run rather than leaving the caller waiting.

**Contracts:** `function.run`, `function.run.get`

### PS-FUNC-011 — A long function is queued and reports progress
**Status:** covered

- Where a function is meant to take a while, the system shall accept the request,
  return a run to follow, and execute it in the background.
- The system shall report every run as waiting, running, completed, failed, or
  cancelled, and shall move it to a terminal state exactly once.
- The system shall record a run's output on success and its error on failure,
  and shall keep both readable afterwards.

**Contracts:** `function.run`, `function.run.get`, `function.run.list`

### PS-FUNC-012 — A run that cannot finish does not hang forever
**Status:** covered

- If the sandbox running a function disappears, then the system shall mark the
  run failed and shall say the run was lost rather than leaving it running.
- If a function is still running past its limit, then the system shall stop it
  and mark the run failed.
- The system shall never leave a run in a non-terminal state after the work
  behind it has stopped.

**Contracts:** `function.run.get`, `function.run.list`

---

## Capability: Build a workflow

### PS-FLOW-001 — A person composes steps into a workflow
**Status:** covered

- When a person creates a workflow and gives it a graph, the system shall make
  it runnable in that pod.
- When a workflow is created, the system shall record `workflow.created`.
- The system shall support steps that run a function, talk to an agent, choose
  between branches, repeat over a list, ask a person for input, wait for a time,
  and finish with a result.
- If a person saves a graph that cannot run — a step with no way in, a branch
  with no destination, a reference to something that does not exist — then the
  system shall refuse and shall say which step is at fault.

**Contracts:** `workflow.create`, `workflow.graph.update`, `workflow.get`, `workflow.created`

### PS-FLOW-002 — A person can see the shape of a workflow before running it
**Status:** covered

- When a person asks to visualise a workflow, the system shall return its shape
  in a form that can be drawn, without running it.
- When a person visualises a run, the system shall show which path it actually
  took and where it currently is.
- The system shall answer for a workflow whose graph is still empty, because a
  workflow is created before it is drawn.

**Contracts:** `workflow.visualize`, `workflow.run.visualize`

---

## Capability: Run a workflow

### PS-FLOW-010 — A person starts a workflow and follows it
**Status:** covered

- When a person starts a workflow, the system shall create a run and begin it.
- The system shall report a run as running, waiting on a person, completed,
  failed, or cancelled.
- When a run completes or fails, the system shall record
  `workflow_run.completed` with which of the two it was.
- The system shall keep the record of each step a run took, in order, with what
  went in and what came out.

**Contracts:** `workflow.run.create`, `workflow.run.get`, `workflow.run.list`, `workflow_run.completed`

### PS-FLOW-011 — A run that waits survives the wait
**Status:** covered

- While a run waits on a function, an agent, a timer, or a person, the system
  shall hold its state durably, so that a restart of the platform does not lose
  it.
- When the thing a run waits on completes, the system shall resume the run from
  where it stopped.
- The system shall resume a run exactly once for a given completion, even if the
  completion is reported more than once.
- If a completion is never reported, then the system shall notice the run is
  stuck and shall resolve it rather than leaving it waiting indefinitely.

**Contracts:** `workflow.run.get`, `workflow.run.stream`

### PS-FLOW-012 — A workflow can ask a person and wait for the answer
**Status:** covered

- When a run reaches a step that needs a person, the system shall record who is
  being asked and what is being asked of them.
- When a person asks what is waiting on them, the system shall list exactly the
  waits assigned to them across the pod.
- When the assigned person submits their answer, the system shall validate it
  against what the step asked for and resume the run.
- If a person who was not asked submits an answer, then the system shall refuse.
- If an answer does not match what the step asked for, then the system shall
  refuse and shall keep the run waiting rather than failing it.

**Contracts:** `workflow.run.form.submit`, `workflow.run.waiting_assigned_to_me`

### PS-FLOW-013 — A person can stop a run
**Status:** covered

- When a person cancels a run, the system shall stop it and shall mark it
  cancelled.
- When a run is cancelled, the system shall stop the work it was waiting on
  where it can, and shall not resume the run when that work later reports back.
- If a person cancels a run that has already finished, then the system shall
  refuse rather than changing a terminal result.

**Contracts:** `workflow.run.cancel`, `workflow.run.get`

### PS-FLOW-014 — A workflow run carries the authority of whoever started it
**Status:** planned

- While a run executes, the system shall give each step no more access than the
  person who started the run has.
- Where a workflow is meant to run for the pod rather than for one person, the
  system shall use the authority granted to the workflow itself and not any
  individual's.
- If a step attempts something the run's authority does not permit, then the
  system shall fail that step with a refusal a person can read, and shall not
  silently skip it.


> **No test yet, and nothing is claimed about the code either way.** This read
> `covered` on the strength of `test_an_outsider_cannot_create_a_workflow`,
> which proves an outsider cannot *create* a workflow — a pod-role rule
> (`PS-POD-011`, where that test now lives) and not a statement about what a
> *run* may do. Proving this one means executing a graph as a restricted actor
> and watching a step be refused, so it belongs in the sandbox lane.

**Contracts:** `workflow.run.create`, `workflow.run.get`

---

## Capability: Watch a run happen

### PS-FLOW-020 — A person follows a run as it goes
**Status:** covered

- While a run is in progress, the system shall stream its steps to a person
  watching, as they happen.
- When a watcher connects to a run already in progress, the system shall give
  them enough to understand where it is, not only what happens next.
- When a run reaches a terminal state, the system shall close the stream rather
  than leaving it open.

**Contracts:** `workflow.run.stream`, `workflow.run.get`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Making work happen on a timer or a data change | [Scheduling and triggers](scheduling-and-triggers.md) |
| What an agent does inside a workflow step | [Agents and conversations](agents-and-conversations.md) |
| Granting a function access to one table | [Sharing and permissions](sharing-and-permissions.md) |
| Sandbox providers, images, and limits | [Sandbox fabric](../../architecture/sandbox/README.md) |
