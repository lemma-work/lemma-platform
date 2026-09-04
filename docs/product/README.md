# Product specification

What Lemma promises a user, written so that each promise is testable and so
that you can find the test that proves it.

This is the document to read when you want to know what the product *does*.
[ARCHITECTURE.md](../../ARCHITECTURE.md) tells you how it is built and the
[module docs](../../lemma-backend/docs/modules/README.md) tell you what each
module owns. This tells you what any of it is for.

## This document is normative

It states the behavior the product is **meant** to have. It is not a
transcription of what the code currently does.

That direction matters, and it is the whole reason the document exists. When
the implementation and this document disagree, the default assumption is that
the implementation is wrong and gets changed. The alternative — writing down
whatever the code happens to do — produces a spec that can never fail, and so
can never be useful.

Two consequences to hold on to while reading or writing:

- **Product language, no hedging.** Scenarios say what a person can do and what
  the system owes them. They do not describe endpoints, tables, queues, or
  modules. If a sentence only makes sense to someone who has read the code, it
  belongs in the module documentation instead.
- **A divergence is a finding, not an edit.** If you discover the system does
  not behave the way a scenario says, do not soften the scenario to match. Mark
  it `gap`, record the specifics in the deviation register, and fix the code.
  See [Status](#status).

## The shape

Three levels, and nothing else:

| Level | What it is | Example |
|---|---|---|
| **Journey** | A stretch of product a person moves through, start to end | Getting started |
| **Capability** | One coherent thing they can do inside it | Sign up and get a working pod |
| **Scenario** | One named, testable path through that capability | `PS-ONB-001` — A new user signs up and lands in their own organization |

A journey is a file. A capability is an `##` heading. A scenario is a `###`
heading with an ID, acceptance criteria, and the contracts it exercises.

There is no fourth level. If a scenario wants sub-scenarios, it is really a
capability and should be promoted.

## Vocabulary

The nouns are not up for negotiation per-document — they are the ones
[`app/core/analytics/event_catalog.py`](../../lemma-backend/app/core/analytics/event_catalog.py)
already enforces, because a product whose spec and whose telemetry disagree
about what a thing is called has two products:

> organization, pod, member, table, record, document, file, function,
> workflow, schedule, agent, conversation, connector, account, surface,
> notification, app, bundle

Use them exactly. A "project" is a pod. A "bot" is a surface. A "script" is a
function. Where the API and the product disagree, the product noun wins in this
document and the operation ID carries the API's spelling.

## Scenario IDs

`PS-<AREA>-<NNN>` — `PS-POD-004`, `PS-DATA-017`.

| Area | Covers |
|---|---|
| `ONB` | Signup, organizations, invitations, membership of an org |
| `POD` | Pod lifecycle, pod membership, pod roles, join requests |
| `DATA` | Tables, records, files, documents, extraction, search, query |
| `FUNC` | Functions and function runs |
| `FLOW` | Workflows, workflow runs, forms |
| `SCHED` | Schedules, timers, triggers, run ledger |
| `AGENT` | Agents, conversations, tools, approvals |
| `SURF` | Surfaces, webhooks, notifications, inboxes |
| `CONN` | Connectors, auth configs, accounts, operations |
| `ACCESS` | Permissions, grants, visibility, delegation |
| `PACK` | Bundles, export, import, publish, share links, apps |
| `OPS` | Usage, limits, deletion, retention, deployment posture |

Three rules, and they exist because traceability is the whole point:

- **Area tracks the noun, not the file.** A capability that moves between
  journey files keeps its IDs. `PS-DATA-*` is about data wherever it is written
  down.
- **IDs are append-only.** Never renumber, never reuse. A retired scenario is
  marked `withdrawn` and stays in the file — a dangling `PS-` reference in a
  test or a PR should resolve to *something*, even if that something says the
  promise was dropped.
- **Numbers are allocated per area, not per file**, and gaps are fine.

## Acceptance criteria

Every scenario states its criteria in **EARS** — the Easy Approach to
Requirements Syntax. Five patterns, fixed clause order, small keyword
vocabulary. It exists to make prose testable: an EARS clause has exactly one
trigger, one condition, and one required behavior, so it maps to an assertion
without interpretation.

| Pattern | Form | Use for |
|---|---|---|
| Ubiquitous | *The system shall …* | An invariant that is always true |
| Event-driven | *When \<trigger>, the system shall …* | A response to something happening |
| State-driven | *While \<state>, the system shall …* | Behavior that holds for a duration |
| Optional | *Where \<feature is present>, the system shall …* | Behavior behind a configuration or plan |
| Unwanted | *If \<trigger>, then the system shall …* | Errors, denials, conflicts, abuse |

Write the unwanted-behavior clauses. They are where the product actually lives:
a spec with only happy paths produces a suite with only happy paths, which is
how a system ends up with 263 operations and no idea what any of them do when
told no.

Two house rules on top of EARS:

- **One clause, one behavior.** If you need an "and", you probably need a
  second clause.
- **Name the observable, not the mechanism.** "shall record `pod.created`" is a
  promise. "shall publish to the `pod_events` stream after commit" is an
  implementation detail, and belongs in the module contract instead.

## Status

Every scenario carries exactly one. The status says where the *implementation*
stands — never where the promise stands, which is always "this is what we want".

| Status | Meaning |
|---|---|
| `covered` | Implemented, and a scenario test proves it. **Gated** — a `covered` scenario with no test fails CI. |
| `planned` | Intended behavior, no test yet. The backlog; nothing is claimed about the code either way. |
| `gap` | Intended behavior the system does **not** deliver today. A known divergence, with an entry in the deviation register. |
| `manual` | True, but proven outside the automated suite — live OAuth consent, a real provider, a human decision. Names how it is verified. |
| `withdrawn` | The promise was dropped. Kept so the ID still resolves; says what replaced it. |

`gap` is the load-bearing one. It is how a spec stays honest without becoming a
description: the promise stands, and the status admits we have not met it yet.
A scenario should sit at `gap` only as long as it takes to fix the code.

### The issues register

Specifics of a divergence — what actually happens, the reproduction, the
affected operation, the severity — go in [`issues.md`](../../issues.md) at the
repository root, one entry per finding with a `DEV-` id.

It is tracked in git rather than kept as scratch, so that a bug found once is
not found again six months later by someone who has no way of knowing it was
already understood. A `gap` in this document is the promise; the entry in
`issues.md` is the evidence and the proposed fix.

## How a scenario connects to its test

A scenario test names the promises it proves and the contracts it exercises:

```python
@scenario("A new user signs up and lands in their own organization")
@proves("PS-ONB-001")
@covers("org.create", "auth.signed_up", "organization.created")
async def test_new_user_lands_in_own_organization(world):
    ...
```

`@proves` takes `PS-` IDs from this document. `@covers` takes OpenAPI
`operationId`s and event names — both already exist and are already CI-gated,
so the tech layer needs no ID space of its own.

The link is checked in both directions by
[`scripts/check_scenario_coverage.py`](../../scripts/check_scenario_coverage.py),
which runs in `make quality`:

1. Every `@proves` ID exists here; every `@covers` name is a live operation or
   event.
2. Every `covered` scenario has at least one test.
3. [`coverage.md`](coverage.md) matches what a collection run produces — the
   same generate-then-gate posture as
   [route-inventory](../../lemma-backend/docs/modules/route-inventory.md).

Without those three, this is documentation. With them, it is a specification.

## Writing a new scenario

1. Find the journey and capability it belongs to. If neither exists, the change
   is bigger than a scenario — add the capability first and say why.
2. Allocate the next `PS-<AREA>-<NNN>` for its noun.
3. Write the criteria in EARS, describing the behavior you **want**. Do not go
   read the handler first and write down what it returns — decide what is
   right, then check. Include at least one unwanted-behavior clause.
4. List the contracts it exercises.
5. Start at `planned`. Move it to `covered` in the pull request that adds the
   test, not before. If writing the test shows the system does something else,
   move it to `gap` and file the divergence — do not edit the criteria to
   match what you found.

Per [CONTRIBUTING](../../CONTRIBUTING.md), documentation is part of the change.
A pull request that alters what a user can do updates this document in the same
diff.

## The journeys

| Journey | What a person is trying to do |
|---|---|
| [Getting started](journeys/getting-started.md) | Sign up, land somewhere, bring a team |
| [Building a pod](journeys/building-a-pod.md) | Make a pod and decide who is in it |
| [Working with data](journeys/working-with-data.md) | Get tables, records, and documents in, and ask questions of them |
| [Automating work](journeys/automating-work.md) | Write functions and workflows that do the work |
| [Scheduling and triggers](journeys/scheduling-and-triggers.md) | Make the work happen without being asked |
| [Agents and conversations](journeys/agents-and-conversations.md) | Put an agent on the work and talk to it |
| [Surfaces and notifications](journeys/surfaces-and-notifications.md) | Reach the pod from Slack, Teams, Telegram, WhatsApp, email |
| [Connectors and accounts](journeys/connectors-and-accounts.md) | Connect the systems the work actually lives in |
| [Sharing and permissions](journeys/sharing-and-permissions.md) | Decide who and what can touch each resource |
| [Packaging and reuse](journeys/packaging-and-reuse.md) | Export a pod, publish it, import someone else's |
| [Operating a deployment](journeys/operating-a-deployment.md) | Watch usage, stay inside limits, delete cleanly |

Coverage across all of them: [coverage.md](coverage.md) — generated, do not edit.
