# Operating a deployment

**Journey:** Someone responsible for a Lemma deployment knows what it is doing,
what it is costing, and that it will behave when something goes wrong.

This journey is for the person who owns the deployment rather than the person
doing the work in it — though in a small organization they are the same person.
It covers what the platform owes them: honest numbers, limits that hold, and
failure that is visible rather than silent.

The promise: nothing important fails quietly. A deployment can be under-resourced,
mis-configured, or running out of budget, and in every case the platform says so
rather than degrading in a way that only shows up as confused users.

---

## Capability: Know what is being used

### PS-OPS-001 — An organization can see what its model work cost
**Status:** covered

- When someone entitled to it asks for an organization's usage, the system shall
  report what was spent over a period, broken down by what spent it.
- The system shall report what a cost is made of, distinguishing input from
  output and cached input from uncached, because cached input is billed at a
  fraction of the rate and a total alone cannot explain the figure.
- The system shall say where a price came from, so a best-effort estimate is not
  mistaken for a rate the deployment configured.
- The system shall record every model run, including runs it could not price.
- Where a model's price is unknown, the system shall still record the run and
  shall mark the cost as unknown rather than recording it as zero.
- If a person who is not entitled to an organization's usage asks for it, then
  the system shall refuse.

**Contracts:** `usage.organization.summary.get`, `usage.organization.events.list`, `usage.organization.stats.get`

### PS-OPS-002 — A person can see their own usage
**Status:** covered

- When a person asks for their own usage in an organization, the system shall
  report it without requiring administrative access.
- The system shall keep one person's usage from revealing another's.

**Contracts:** `usage.organization.me.summary.get`

### PS-OPS-003 — Usage records are a ledger, not a cache
**Status:** covered

- The system shall keep a usage record unchanged once written.
- The system shall attribute every record to the run, the model, and the person
  or workload behind it.
- The system shall record a run's usage whether it succeeded or failed, because
  a failed run still costs. This holds however the run ended: an error, a
  cancelled worker, or a run already marked finished by something else.
- The system shall record the model work it does on someone's behalf outside a
  run of their own — compacting a conversation's history, reading an image for a
  model that cannot see — because that is spent on the same credentials.

**Contracts:** `usage.organization.events.list`, `agent_run.completed`

---

## Capability: Stay inside limits

### PS-OPS-010 — Limits are visible before they are hit
**Status:** covered

- When someone asks what limits apply to an organization, the system shall
  report them and how much is used.
- Where a deployment sets no limit, the system shall say so plainly rather than
  reporting a limit of zero or an absent one.

**Contracts:** `usage.organization.limits.get`

### PS-OPS-011 — A missing price never blocks work
**Status:** covered

- Where the system cannot price a model, it shall allow the run and record it
  unpriced.
- The system shall never refuse work because its own pricing table is
  incomplete.

**Contracts:** `usage.organization.limits.get`, `agent_run.completed`

### PS-OPS-012 — Exceeding a limit is refused clearly, never silently
**Status:** covered

- If work would exceed a configured limit, then the system shall refuse it and
  shall say which limit was reached.
- If work exhausts the allowance partway through, then the system shall stop it
  and shall say that is why it stopped, keeping whatever was produced first.
- The system shall not downgrade a model, shorten a run, or drop work without
  saying so.
- When a limit resets, the system shall allow work again without intervention.

> Amended: this previously forbade shortening a run at all, which left admission
> as the only control — and admission reserves a fixed few cents, so someone one
> cent inside their limit could start a run costing hundreds and the promise of a
> limit that holds was not kept. A run may now be stopped by its allowance. What
> it may not do is stop *quietly*: the person is told the allowance ran out, and
> the partial answer is theirs.

**Contracts:** `usage.organization.limits.get`

---

## Capability: Delete cleanly

### PS-OPS-020 — Deleting a pod actually stops everything it was doing
**Status:** covered

- When a pod is deleted, the system shall stop its schedules, its surfaces, and
  its standing work, and shall keep them stopped.
- The system shall not leave a timer, a webhook registration, or a sandbox
  running for a pod that no longer exists.
- The system shall stop answering for anything inside a deleted pod, on every
  route, whoever is asking.
- The system shall leave every other pod's data and running work untouched.

**Contracts:** `pod.delete`, `pod.deleted`

### PS-OPS-021 — A person can take their data out
**Status:** covered

- When someone entitled to it exports a pod, the system shall include the pod's
  data as well as its definitions.
- The system shall make the export readable without Lemma, so that leaving the
  platform does not mean losing the work.

**Contracts:** `pod.bundle.export.start`, `pod.bundle.download`

---

## Capability: Know when the platform itself is unwell

### PS-OPS-030 — The platform reports its own health honestly
**Status:** covered

- The system shall expose whether it is alive and whether it is ready to serve,
  as separate answers, because a process that is running and a process that can
  work are different things.
- If a component the platform depends on is unavailable, then the system shall
  report itself unready rather than accepting work it cannot complete.
- The system shall not report itself healthy while its background workers are
  wedged.

**Contracts:** *(health endpoints are outside the documented API surface)*

### PS-OPS-031 — Work that cannot be completed is not lost silently
**Status:** manual

- If a background job fails repeatedly, then the system shall stop retrying it
  and shall keep it somewhere an operator can find it.
- The system shall let an operator see what has been given up on, rather than
  discovering it through a user report.
- The system shall not drop an event because nothing was listening at the time
  it was published.

> **Verified by:** `test_dead_letter_e2e.py` in `app/core/tests/e2e/`, not by
> a scenario. Every clause needs a dependency to fail repeatedly on demand, and
> the scenario suite forbids mocking, so it has no way to induce one — see
> [testing.md](../../testing.md) on which suite owns injected failure. The two
> tests there drive a real dispatcher against a real outbox with a broker that
> always raises, and hold the retry budget stopping, the row being kept with
> its error type, a dead-lettered event not being claimed again, and an
> operator finding it and replaying it.
>
> This previously read `covered` on the strength of
> `test_feedback_can_be_reported`, which reports a broken *tool* and has
> nothing to do with dead-lettering.

**Contracts:** *(operational; see [Reliability](../../../lemma-backend/docs/operators/reliability.md))*

### PS-OPS-032 — A deployment can be configured for its own region and rules
**Status:** manual

- Where a deployment is subject to particular data-residency rules, the system
  shall let its operator point outbound telemetry and analytics at their own
  region.
- The system shall let an operator turn off product analytics entirely, and
  shall keep working when they do.
- The system shall never send a person's content, prompts, or model
  input and output to analytics.

> **Verified by:** an operator, at deployment time. Both halves are
> configuration rather than API — where telemetry is sent, and whether
> product analytics run at all — so what a scenario could check is that the
> platform works with analytics off, which is how this suite already runs.
> See [Configuration](../../configuration.md).

**Contracts:** *(configuration; see [Configuration](../../configuration.md) and [Product analytics](../../design/product-analytics.md))*

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Installing and running the stack | [Installation](../../installation.md) |
| Every operator-facing setting | [Configuration](../../configuration.md) |
| Traces, metrics, and log export | [Observability](../../observability.md) |
| Replay, dead-letter, and SLO guidance | [Reliability](../../../lemma-backend/docs/operators/reliability.md) |
| What the platform defends against | [Threat model](../../security/threat-model.md) |
