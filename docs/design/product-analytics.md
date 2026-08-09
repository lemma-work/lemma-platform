# Product analytics

Status: Phases 1–4 implemented; Phase 5 (ClickStack) not started.

* **Phase 1** — `app/core/origin.py`, `app/core/analytics/` (catalog, sink,
  emitter, PostHog transport, bootstrap), origin resolved in
  `RequestObserverMiddleware`, `DomainEvent` carries origin, and
  `app/composition/analytics_consumer.py` raises 8 catalog events off the bus
  as a new `analytics` module.
* **Phase 2** — `lib/analytics/client.ts`, `AnalyticsProvider`, the `/ingest`
  rewrite, and the two loop events (`share_link.viewed`, `import.started`).
* **Phase 3** — `lemma_cli/cli_core/telemetry.py` and `lemma telemetry`.
* **Phase 4** — `locald/src/telemetry.rs`.

Nothing reports anywhere until an ingestion key is set, and no key is set
anywhere in this repo. Remaining gaps are listed in §11.
Companion: [observability](../observability.md) — the operational plane, already built

Lemma is not a web app with some integrations. It is a pod that people and
agents both work through, reached a dozen different ways, and the analytics
plane has to be shaped like that or it will answer the wrong questions
confidently.

This describes one product-analytics plane across the whole platform, sinking
into PostHog Cloud (EU) today and self-hosted ClickStack later, without
rewriting a single call site at the swap.

## 1. Why this is not the observability stack

Lemma already exports traces, metrics, and logs over OTLP. That plane cannot
answer product questions, and the reason is deliberate:
`app/core/observability/span_sanitizer.py` is default-deny. Only a fixed
allowlist of attribute keys crosses the export boundary, strings truncate at
256 chars, and span names collapse to a small safe set. The observability doc
calls this "a production-safety invariant, not a dev convenience."

So there is no version of "just query the traces" that yields activation rate
or import conversion. The business context is stripped on purpose, and it
should stay stripped.

Three planes, kept apart:

| Plane | Sink | Answers | Source of truth for |
|---|---|---|---|
| Operational | OTLP → ClickStack / Phoenix | Is it broken? Is it slow? What did the model see? | Nothing — diagnostics only |
| **Product** | **PostHog Cloud (EU)** | **Who did what, through what, did the pod deliver?** | **Nothing — decisions only** |
| Metering | Postgres | What do we bill, what quota is left? | Billing |

Never bill off analytics. PostHog drops events, dedupes unpredictably, and is
sampled at the edges by ad blockers. Usage reservation already lives in
Postgres and stays there.

## 2. The shape of the platform, and what that forces

The first draft of this plan had one field called `client_surface` carrying
`web | cli | desktop`. That is wrong twice over, and both errors are worth
naming because they are the errors any analytics plan on this codebase will
make.

**A surface is not the platform.** In Lemma, *surface* is a specific product
noun: a chat platform where an agent answers pod members — Slack, Teams,
WhatsApp, Telegram, Gmail, Outlook. Using it to mean "the client that made the
request" collides with the product's own vocabulary and breaks one noun per
concept. Surfaces are one entry among many.

**How work enters does not tell you who is working.** Humans chat through the
web UI *and* through surfaces. Agents usually work through the SDK — but
humans use the SDK too, and agents post to surfaces. Collapsing the two into
one field makes it permanently impossible to ask "how much of this pod's work
is human?", which is the single most interesting question Lemma can answer
about itself.

So every event carries four independent facts, and the platform already
computes three of them at every authorization check:

| Fact | Enum | Where it lives today |
|---|---|---|
| **Actor** — who acted | `ActorType` | `app/core/authorization/context.py` |
| **Origin** — how the work entered | `OriginKind` (new) | scattered; see §3 |
| **Resource** — what was acted on | `ResourceType`, `ResourceVisibility` | `app/core/authorization/context.py` |
| **Outcome** — what happened | per event | — |

`ActorType` already distinguishes `USER`, `AGENT`, `FUNCTION`,
`DELEGATED_USER_WORKLOAD`, `SYSTEM`, `ANONYMOUS`. That fourth value carries
`delegated_by_user_id` — an agent acting *as* a person. Analytics must record
both: the actor is the agent, and `on_behalf_of` is the human. Counting that
run as pure agent activity understates human engagement; counting it as human
activity overstates it. It is genuinely both, and only recording both lets a
dashboard choose later.

### REACH_RULE, and why engagement is not one number

Stated once in `lemma-frontend/lib/recipes/recipes.ts` and threaded through
every starter prompt:

> Only members of this pod can message its surfaces or open its apps — anyone
> else gets a signup or request-access link instead of an answer. Reach anyone
> outside it through a connector instead.

This is an architectural boundary, not a policy note, and it splits pod
engagement into two things that must never be summed:

- **Inside reach** — surfaces and apps. Bounded by pod membership. Growth here
  means the team adopted the pod.
- **Outside reach** — connectors. The only path by which the world reaches a
  pod. Growth here means the pod is doing real external work.

A support desk pod scores near zero on inside reach and is thriving. An
internal dashboard pod scores zero on outside reach and is thriving. One
blended "engagement" metric calls both of them dying.

## 3. Origin: the missing enum

The platform has no single name for how work entered. `Conversation` carries a
loose `origin_type: str | None`; `NotificationOriginKind` is a proper enum but
covers only notifications. Analytics needs one canonical spine, and *origin*
is already the platform's word for "what produced this" — so canonicalize it
rather than inventing `client_surface`. This fixes an existing inconsistency
as a side effect.

```python
class OriginKind(str, Enum):
    # People, directly
    WEB = "WEB"                    # the Next app in a browser
    DESKTOP = "DESKTOP"            # the Next app in the Tauri webview
    CLI = "CLI"                    # lemma commands
    APP = "APP"                    # a published pod app, members only
    SURFACE = "SURFACE"            # Slack/Teams/WhatsApp/Telegram/Gmail/Outlook

    # Programmatic
    SDK = "SDK"                    # lemma-python / lemma-typescript
    MCP_POD = "MCP_POD"            # /agent-runtime/pods
    MCP_CONVERSATION = "MCP_CONVERSATION"   # /agent-runtime/conversations
    AGENT_HOST = "AGENT_HOST"      # user-owned coding agent over ACP
    WORKSPACE = "WORKSPACE"        # code running in a sandbox

    # Nobody present
    SCHEDULE = "SCHEDULE"          # cron trigger
    DATA_TRIGGER = "DATA_TRIGGER"  # table-write trigger
    WORKFLOW = "WORKFLOW"          # a workflow node
    CONNECTOR = "CONNECTOR"        # inbound work from outside the pod

    SYSTEM = "SYSTEM"              # platform-internal
```

`SURFACE` and `CONNECTOR` carry a `platform` property (`slack`, `gmail`, …) so
one dimension does not explode into fourteen. `AGENT_HOST` carries the agent
family (`claude-code`, `codex`, `cursor`, `opencode`) — which is worth knowing
precisely because Lemma's pitch is that you bring your own.

Origin is resolved once, at the edge, and rides the request context alongside
the actor. The `X-Lemma-Client` header already exists on every SDK request
(`lemma-python/lemma_sdk/transport.py`, currently `lemma-sdk-py/<version>`);
it gains an origin token. Server-initiated work — schedules, triggers, outbox
consumers — sets origin at the point it is enqueued, not guessed downstream.

The **origin × actor matrix is the point.** Not every cell is populated, and
the empty ones are as informative as the full ones:

| | USER | AGENT | DELEGATED | SYSTEM |
|---|---|---|---|---|
| WEB / DESKTOP | chat, build, admin | — | assistant acting for you | — |
| CLI | operator, builder | coding agent running `lemma` | — | — |
| SDK | scripts, app code | agent tool calls | agent as user | — |
| SURFACE / APP | members working | agent replies | — | — |
| MCP / AGENT_HOST | — | external agents | — | — |
| SCHEDULE / TRIGGER / WORKFLOW | — | — | — | autonomous work |
| CONNECTOR | member approves a send | agent reads outside work | — | — |

## 4. The event contract

The decision that makes the ClickStack migration cheap is that call sites never
touch a vendor SDK. They emit against a typed catalog; an adapter maps that
catalog to a sink.

This repo already proved the pattern — `app/core/log/event_catalog.py` holds an
exact, machine-checked event contract for logs. Product analytics gets the
sibling at `app/core/analytics/event_catalog.py`, hand-written rather than
generated, because these events are a product decision and not a by-product of
the code.

```python
@dataclass(frozen=True, slots=True)
class AnalyticEvent:
    name: str
    properties: frozenset[str]   # exact allowlist; nothing else crosses
    groups: frozenset[str]       # "organization", "pod"
    origins: frozenset[OriginKind] | None = None   # None = any
```

Every event automatically carries the spine — `actor_type`, `on_behalf_of`,
`origin`, `origin_platform`, `organization_id`, `pod_id`, `deployment` — so
per-event `properties` lists only what is specific to that event.

Naming is `noun.verb_past`, and the noun is the product's noun: pod, agent,
table, file, function, workflow, schedule, connector, surface, app, bundle,
conversation. One noun per concept, matching the story canon. `pod.created`,
never `workspace_created`.

Two rules that keep it honest:

- **Adding an event is a PR that edits the catalog.** An emit for a name not in
  the catalog raises in dev and CI, no-ops in production — the same posture as
  the logging contract violation event.
- **Events are append-only.** Never redefine `pod.created`; add a new name if
  the meaning changes. Renaming silently splits every historical funnel.

The sink is an interface:

```python
class AnalyticsSink(Protocol):
    def capture(self, event: CapturedEvent) -> None: ...

class PostHogSink:      # today
class ClickStackSink:   # later — one class, catalog unchanged
class NullSink:         # every deployment that is not Lemma Cloud
```

## 5. Where events are emitted

**The backend is the primary emitter**, and events project off the **domain
event bus**, not controllers. `app/core/domain/events.py` plus the
transactional outbox already give durability, lineage (`correlation_id`,
`causation_id`), and replay (`replay_outbox_event`). A subscriber there means:

- no controller edits, so instrumentation cannot drift from behaviour;
- events survive a PostHog outage and replay afterwards;
- an event that fires is an event that committed, because the outbox writes in
  the same transaction as the state change. Controller-level emits routinely
  report actions that later rolled back.

Critically, it also means **every origin is covered by one implementation.** A
pod created from the CLI, from an agent over MCP, from a coding agent over ACP,
or from a workflow node all land on the same domain event. Client-side
instrumentation would have caught the first and missed the rest — and the rest
is where Lemma is differentiated.

Clients emit only what the backend cannot see:

| Emitter | Emits | Never emits |
|---|---|---|
| Backend (Python) | Everything with a state change, across every origin | UI interaction |
| Web (posthog-js) | Pageviews, funnel steps abandoned before any API call, client errors, landing→signup | Anything the API already sees |
| CLI (Python) | `cli.command_invoked` — name, version, exit status, install id | Any domain event; the API already logged it |
| Desktop (Rust) | Install/launch/runtime lifecycle, which precedes any account | Anything about pod content |

Web-specific settings, all deliberate:

- **Autocapture off.** Lemma renders customer business data — table records,
  agent transcripts, file contents. Autocapture harvests DOM text into event
  properties. It is the fastest possible way to put a customer's records in a
  third-party analytics database.
- **Session replay off.** Same reason, more so.
- **Manual pageviews** — App Router does not fire what posthog-js expects.
- **Reverse-proxied through `/ingest`.** Ad blockers eat a meaningful share of
  direct calls, and the loss skews toward exactly the technical users Lemma
  sells to.

## 6. Deployment posture

The sink is a property of the deployment, not of the code path. A local-first
AGPL product that quietly phones home loses something it cannot buy back.

| Deployment | Sink | Identity | Content events | Default |
|---|---|---|---|---|
| Lemma Cloud | PostHog EU | User + org + pod | Full catalog | On |
| Self-hosted server | Anonymous heartbeat only | Random instance id | **None** | On, opt-out |
| Desktop, Local mode | Anonymous install health only | Random install id | **None** | On, opt-out |
| Telemetry off | `NullSink` | — | — | — |

Enforced structurally, not by a runtime `if`:

- The backend reads `ANALYTICS_WRITE_KEY` alongside the existing
  `observability_enabled` family in `app/core/config.py`. **Absent key
  constructs `NullSink`** — a null object, not a disabled PostHogSink, so no
  code path can be induced into sending pod content by flipping one boolean.
- The frontend calls the existing `isLocalDeployment()`
  (`lemma-frontend/lib/config.ts`) and never initializes posthog-js locally.
- Desktop and self-hosted use a **separate write key and a separate, smaller
  catalog** — not the product catalog with fields omitted, but a contract that
  structurally cannot express a pod id.

### Desktop sends install health, not product analytics

Desktop is the primary distribution channel; without this you learn about a
broken runtime install from a GitHub issue three weeks late. The whole list:

```text
desktop.launched         os, arch, app_version, install_id, cold|warm
desktop.runtime_install  started | completed | failed(step, error_class)
desktop.runtime_ready    duration_ms, cached|fresh
desktop.mode_selected    local | hosted
desktop.quit             session_duration_bucket
```

`error_class` is a bounded enum from the installer's own failure taxonomy —
never an error string, which carries paths and hostnames. No pod id, no org id,
no user id, no names of anything the user made. Stage timings already land in
`runtime/launch.log`; these events are the aggregate of what that file records
locally.

Disclosure is a plain-language line at first run with a visible toggle, plus a
permanent switch in Local settings. `LEMMA_TELEMETRY=0` disables it everywhere,
and the CLI gets `lemma system telemetry off` alongside `lemma system config`.

### Self-hosted servers send one event

```text
instance.heartbeat   instance_id, version, deployment_kind, pod_count_bucket
```

Bucketed counts (`1-5`, `6-20`, `21-100`, `100+`), never exact, never names.
Enough to know version spread and whether self-hosted adoption is real; not
enough to profile anyone's business.

## 7. The privacy boundary

Mirror what the observability plane already does:

- **Default-deny property allowlist.** The emitter drops any property not named
  in that event's catalog entry. Not a denylist — a denylist fails the first
  time someone adds a field.
- **Never in properties:** email, name, pod/table/agent/file *names*, record
  contents, prompt or completion text, file paths, URLs with query strings,
  precise location. IDs and bounded enums only.
- **A test that enforces it.** `app/core/tests/unit/test_otel_safety.py` is the
  precedent: adversarial content fed through the pipeline, asserted not to
  survive export. Analytics gets the same — a canary event with an email, a
  prompt, and a pod named `<script>` in every field.
- **PostHog EU Cloud**, person profiles `identified_only` so anonymous traffic
  does not mint a person record for every bot hitting the landing page.

Two things must close before the first production event, both outside the code:

1. **A DPA with PostHog**, and PostHog on the subprocessor list.
2. **The privacy page is a 12-line stub** (`lemma-frontend/app/privacy/page.tsx`).
   It has to name what is collected, by whom, and how to opt out — and be
   consistent with the README's "run it on your laptop" promise, which under
   the posture above it is.

## 8. Identity and groups

- **`distinct_id`** is the SuperTokens user id. Never the email — an email in
  `distinct_id` puts PII in every event, every export, and every URL someone
  pastes into Slack.
- **Group `organization`** — the account. The unit of retention and expansion.
- **Group `pod`** — the product unit. The thing built, shared, imported,
  remixed. Pod-level retention matters more than user retention: a pod running
  on a schedule with nobody watching is delivering value, and a user-centric
  DAU chart scores it as churn.

Anonymous-to-identified stitching is free on the web because marketing and app
are one Next application (`app/landing`, `app/home`, and the dashboard share a
tree), so the same posthog-js instance sets the anonymous id and calls
`identify` at signup.

CLI and Desktop need an id before there is an account: a random UUID written
once to `~/.lemma/config.json` (CLI) and the locald state directory (Desktop),
joined to the person by `alias(install_id, user_id)` at login. Random — never
derived from hostname, MAC, or machine UUID, which are fingerprints.

## 9. What the events are for

**Activation — and it is not a surface message.** The first draft made
`surface.message_answered` the activation event. That is wrong for the same
reason `client_surface` was: it scores only pods whose value arrives over chat,
and calls a dashboard pod, a scheduled report, and a connector-driven support
desk failures.

Activation is `pod.delivered`: **the first time a pod produced an outcome for
someone other than the person building it**, through any origin — an agent run
answering a member on a surface, an app session by a second member, a completed
scheduled run, a connector send a member approved. Derived from the raw events,
computed once, origin recorded so the mix stays visible.

```text
landing.viewed → auth.signed_up → organization.created → pod.created
  → [ pod built: table/agent/workflow/app/surface/connector configured ]
  → pod.delivered   ← activation, any origin
```

**The loop.** Share→import→remix is the growth engine, and the routes are built
(`app/s`, `app/import`, `app/remix`), so this is instrumentable today.

```text
bundle.exported → share_link.viewed → import.started → import.completed
  → pod.created(origin=IMPORT) → pod.delivered
```

`origin=IMPORT` separates imported pods from scratch-built ones and yields a
real k-factor: imports completed per pod shared.

**Retention.** Weekly active *pods*, split by inside reach and outside reach
per §2 — never summed. Plus work per pod per week broken down by actor, which
is the number that says whether a pod is a tool people use or a system that
runs itself. Both are good outcomes; they are different products.

**Build velocity.** Time from `pod.created` to `pod.delivered`, and how many
resources a pod has when it first delivers. This is the number that tells you
whether the harness is doing its job.

**Desktop install.** `desktop.launched → runtime_install → runtime_ready`,
failure step broken out. Entirely pre-account, which is why it needs the
install-id channel.

## 10. Rollout

**Phase 1 — spine.** `OriginKind`, origin resolution at every edge, the
catalog, the sink interface, the PostHog adapter, the null default, allowlist
enforcement, the adversarial test. Analytics subscriber on the domain event bus
covering pod, agent run, workflow run, table, app, surface, connector, bundle.
At the end of this phase the activation funnel and the loop are readable for
*every* origin, with zero client work.

**Phase 2 — web.** posthog-js in `app/providers.tsx`, gated on
`isLocalDeployment()`. Autocapture and replay off, manual pageviews, `/ingest`
rewrite, landing→signup stitching. Only pre-API steps and client errors.

**Phase 3 — CLI.** Install id, `cli.command_invoked`,
`lemma system telemetry`, `LEMMA_TELEMETRY=0`. Fire-and-forget with a hard
timeout — a CLI that hangs on an analytics endpoint is a bug report, and a
deserved one.

**Phase 4 — Desktop.** Install-health catalog in Rust, first-run disclosure,
Local settings toggle, separate write key.

**Phase 5 — ClickStack.** Add `ClickStackSink`, dual-write for one retention
window, reconcile a known funnel across both, cut over, drop PostHog. The
catalog does not change, which is the point of §4.

## 11. Open items

- **Backfilling origin onto `Conversation.origin_type`** — the loose string
  should become `OriginKind`. Worth doing in Phase 1 as the migration that
  proves the enum, or deferring so analytics does not block on a data
  migration.
- **The TypeScript SDK does not send `X-Lemma-Client`.** The Python SDK does,
  and honours `LEMMA_CLIENT`, which is how the CLI names itself. Until the TS
  transport does the same, browser and Desktop traffic resolves to `SDK` rather
  than `WEB`/`DESKTOP` — deliberately safe (an unknown caller is never counted
  as a person in a browser) but it means the origin split is incomplete for the
  two biggest human surfaces.
- **Server-initiated origins** (schedule, data trigger, workflow, connector)
  are not set where the work is enqueued, so those events carry no origin.
  Guessing them downstream from the consumer is what makes an origin dimension
  quietly wrong, so the consumer leaves them empty instead.
- **Desktop events are defined but not raised.** `locald/src/telemetry.rs` has
  the contract, the install id, the opt-out and the transport; the launch and
  runtime-install call sites do not call `record()` yet, and there is no Local
  settings toggle in the UI. Until both land, Desktop reports nothing.
- **Eighteen catalog events have no emitter**, listed in
  `test_analytics_wiring.py::KNOWN_GAPS`. Most need a domain event the platform
  does not publish today — `pod_bundle` and `connectors` raise none at all,
  which is why the share→import→remix loop is only half measurable from the
  server.
- Whether `agent_run.completed` carries token counts. Makes cost-per-activated-
  pod answerable in one place, but duplicates metering Postgres owns. Leaning
  yes, bucketed, explicitly labelled non-authoritative.
- Event volume estimate — agent runs counted per run or per conversation. Per
  run is more useful and materially more expensive; start per run.
