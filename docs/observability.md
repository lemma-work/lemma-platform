# Observability

Lemma can export traces, metrics, and structured application logs to any OTLP
collector. The implementation has no dependency on a particular cloud or
observability vendor.

For local dev there are two purpose-built backends, started together with one
command:

- **HyperDX (ClickStack)** — general API traces, metrics, and logs. Latency,
  error rates, slow routes, log volume. No LLM/prompt awareness.
- **Arize Phoenix** — LLM/OpenInference traces only: full prompts, responses,
  tool calls, token usage. Independent, isolated pipeline.

No single OSS tool does both well — ClickStack's UI has no prompt-aware trace
viewer (it renders raw span attributes), and Phoenix isn't a general APM tool.
Splitting them is deliberate, not a compromise.

```shell
make observability-up      # start HyperDX + Phoenix, print URLs + keys
make dev OTEL=1 LLM_OTEL=1 # start the app, sending telemetry to both
make observability-open    # open both dashboards in a browser
make observability-down    # stop HyperDX + Phoenix
```

| Service | URL | Purpose |
|---|---|---|
| HyperDX UI | http://localhost:8080 | Traces, metrics, logs, dashboards |
| Phoenix UI | http://localhost:16006 | LLM prompt/response inspection |

`make observability-up` is idempotent — it registers (or logs into, on later
runs) a fixed local-only HyperDX team, persisted in a Docker volume across
restarts, and provisions a "Lemma API Overview" dashboard the first time it
runs. It prints two secrets each time: the OTLP ingestion key (used
automatically by `make dev OTEL=1`) and an `export CLICKSTACK_ACCESS_KEY=...`
line for the MCP server below. Neither is written to a committed file.

## Signal selection

Telemetry is disabled unless `OBSERVABILITY_ENABLED=true`. The standard
`OTEL_SDK_DISABLED=true` switch always disables it. Each signal is selected
independently:

| Variable | Default | Values |
|---|---:|---|
| `OTEL_TRACES_EXPORTER` | `otlp` | `otlp`, `none` |
| `OTEL_METRICS_EXPORTER` | `none` | `otlp`, `none` |
| `OTEL_LOGS_EXPORTER` | `none` | `otlp`, `none` |

Use the standard global `OTEL_EXPORTER_OTLP_ENDPOINT`, protocol, and headers, or
their `TRACES`, `METRICS`, and `LOGS` signal-specific variants. A general
HTTP/protobuf endpoint receives the standard `/v1/<signal>` suffix; a gRPC or
signal-specific endpoint is used as supplied. Selected signals require an
endpoint. `OTEL_SIGNALS` remains a deprecated compatibility selector; an empty
value selects traces only, and explicit standard exporter variables win.

`make dev OTEL=1` sets all three (traces, metrics, **and logs** — `OTEL_LOGS`
defaults to `1`) pointed at HyperDX, with the bootstrapped ingestion key as
the bearer token. Set `OTEL_LOGS=0` to opt back out.

Root traces use `parentbased_traceidratio` sampling at 5% by default. Configure
the standard `OTEL_TRACES_SAMPLER` and `OTEL_TRACES_SAMPLER_ARG` variables to
change it. Metrics export every 60 seconds by default via
`OTEL_METRIC_EXPORT_INTERVAL`. Metric identifiers, URLs, SQL, Redis keys, and
other high-cardinality attributes are removed before export. Histograms carry
trace-only exemplars — see [Exemplars](#exemplars-click-the-spike-see-the-request).

The general pipeline's export boundary (`app/core/observability/span_sanitizer.py`)
is default-deny: only a fixed allowlist of attribute keys crosses it (route
templates, status codes, token counts, bounded Lemma business-context fields),
strings are truncated to 256 chars, and span names are collapsed to a small
safe set (`db.operation`, `http.client`, ...) rather than the raw operation
name -- see [Span names](#span-names) for the ones built to be read. This is why the dashboard's "Endpoints" tile groups by the
`http.route` *attribute* instead of span name, and why general logs never
carry log message bodies beyond a bounded field allowlist
(`SanitizingLoggingHandler`). None of this is configurable — it's a
production-safety invariant, not a dev convenience.

## Metric labels are allowlisted, and that is where labels go to die

One catch-all View is applied to every instrument
(`app/core/observability/telemetry.py`), keeping only the keys in
`METRIC_ATTRIBUTE_KEYS` (`app/core/observability/span_sanitizer.py`) and
silently dropping the rest. Spans have their own allowlist,
`GENERAL_SPAN_ATTRIBUTE_KEYS`, and the resource a third,
`RESOURCE_ATTRIBUTE_KEYS`.

This is a production-safety invariant and it stays default-deny. But it means
**a label you add to an instrument does not appear on a dashboard until its key
is in that set**, and nothing warns you — the metric exports, the label is just
gone, and points that differed only by that label silently aggregate into one
series. If a metric looks like it lost its dimensions, check the allowlist
before checking the instrumentation. `app/core/tests/unit/test_otel_safety.py`
asserts the keys the dashboards depend on.

Tenancy is deliberately split: `lemma.organization_id` is allowed on **spans**
and not on metrics. As a span attribute it costs storage proportional to
sampled traffic and turns "the API is slow" into "it is slow for this
customer"; as a metric label it would multiply every series by the customer
count.

The one exception is spend. `lemma.llm.tokens` and `lemma.llm.cost_usd` carry
`billing.organization_id` — a key distinct from `lemma.organization_id`, so
allowlisting it does not put the tenant on every metric that happens to carry
the span key, and `test_billing_org_label.py` fails if anything but the usage
service writes it. It is one series per organization on two counters, bounded
by the org count and acceptable at current scale. If that stops being true,
delete the key from `METRIC_ATTRIBUTE_KEYS` and spend falls back to per-model
series with no change at the call site. Pod ids are never a metric label.

### Metrics worth knowing about

| Metric | Labels | Answers |
|---|---|---|
| `lemma.http.server.requests` | `http.route`, `http.request.method`, `http.response.status_code` | Per-route error rate. Exact status, so it joins the FastAPI instrumentation's own histogram |
| `http.client.request.duration` | `server.address`, `http.request.method`, `http.response.status_code` | Which third party is slow |
| `db.client.connections.usage` | `pool.name`, `state` | Pool utilisation per pool. `pool.name` is safe only because the engines set an explicit `pool_logging_name`; the instrumentation's fallback is the DSN |
| `lemma.work.items` | `work_class`, `state` | How much work is *waiting*, which throughput cannot tell you |
| `lemma.work.ready_oldest_age` | `work_class` | How long the oldest claimable item has waited: the latency a person sees |
| `lemma.work.dead` | `work_class`, `work_kind`, `error_class` | Work that gave up |
| `lemma.llm.tokens` | `gen_ai.request.model`, `gen_ai.token.type`, `operation`, `billing.organization_id` | Tokens per day by model and by customer |
| `lemma.llm.cost_usd` | `gen_ai.request.model`, `operation`, `billing.organization_id` | Spend by model and by customer |
| `lemma.channels.deliveries`, `lemma.channels.delivery_seconds` | `platform`, `result`, `target` | Channels sends. These labels were emitted but missing from the allowlist until the product counters below added them, so older data is one label-less series |

### Product counters

Defined in `app/core/observability/product_metrics.py`, which folds every
label value onto a fixed set before recording. Prometheus names follow the
usual translation (`.` → `_`, counters gain `_total`).

| Metric | Labels | Recorded at |
|---|---|---|
| `lemma.conversations.started` | `surface` = app, slack, telegram, whatsapp, teams, email, web_widget, api, workflow, other | Root conversation insert (`Conversations.create_conversation`, `infrastructure/db/conversations.py`). The app and the public API write the same rows and both count as `app` |
| `lemma.surface.messages` | `surface`, `direction` = in/out, `outcome` = success/failure/retry | In: `InboundAcceptor.accept`, for events classified as a message (success once stored or handled, failure if that raises). Out: every agent reply send (`reply_work`) and every Channels delivery. `retry` is a transient failure the row will try again, so `success + failure` is messages and `retry` is extra attempts |
| `lemma.surface.delivery_seconds` | `surface` | Same outbound sends, platform call start to answer |
| `lemma.workflow.runs`, `lemma.workflow.run_duration_seconds` | `outcome` = completed/failed/cancelled (counter only) | `RunPublication._record_transition`. A run cancelled before it started is counted and not timed |
| `lemma.function.runs`, `lemma.function.run_duration_seconds` | `kind` = api/job, `outcome` (counter only) | `FunctionExecutionRepository._announce_finished`, every terminal transition |
| `lemma.connector.operations`, `lemma.connector.operation_duration_seconds` | `connector` (catalog slug; the connectors table is global, so bounded by the catalog), `outcome` = success/failure (counter only) | `KindDispatcher.execute`, credential presentation included |
| `lemma.sandbox.sessions_active` (gauge) | — | E2B `sandbox_instances` in state `ready`, sampled on the worker |
| `lemma.schedules.active` (gauge) | `schedule_type` = time/webhook/datastore/other, `target` = agent/workflow/other | Active, non-internal schedules: one grouped count on the worker's gauge loop. Every replica reports the same value, so take the max, not the sum |
| `lemma.schedule.runs` | `schedule_type`, `outcome` = triggered/filtered/failed/skipped/breaker_tripped | Every fire decision. `triggered`: started its target. `filtered`: its filter said no. `failed`: dead-lettered before or at dispatch. `skipped`: the run cap or daily fire cap held it. `breaker_tripped`: this failure deactivated the schedule. A duplicate event or an inactive schedule is not counted |
| `lemma.schedule.run.duration` (s) | `schedule_type`, `outcome` = completed/failed/cancelled | From the run starting to its target's terminal outcome (`ScheduleRunOutcomeService.record_target_outcome`) |
| `lemma.schedule.fire_lag` (s) | `schedule_type` | Timer schedules only: when the fire ran minus when it was due (`next_fire_at`) |
| `lemma.sandbox.session_seconds` | — | An E2B instance leaving `ready` (release or destroy), timed from ready or its latest resume. A destroy through the epoch-fenced `claim_for_destroy` path is not timed |
| `lemma.active_users` (gauge) | `window` = 5m/1h/24h | Distinct `user_id` in `usage_records` over the window — people the product did model work for. One query over a UUIDv7 primary-key range, every 20 gauge ticks (five minutes by default) |

The two gauges are sampled by the work-gauge loop through
`register_gauge_sampler` and, like the work gauges, report nothing until the
first successful sample and nothing again after a failed one. Every worker
replica reports the same reading: aggregate with `max`, not `sum`.

`lemma.event.outbox.*` / `lemma.event.inbox.*` are gone: their definitions
went with the old event transport in #981, so nothing in this tree emits them.
A dashboard or alert still querying them is reading an empty series.

The work gauges are sampled by a loop on the **worker** (every
`WORK_GAUGE_INTERVAL_SECONDS`) and report nothing until the first successful
sample — a gauge that reports a stale level reads as
a healthy steady state, which is the failure these exist to catch.

### Agent runs

Recorded by `AgentRunnerService.execute` and `GracefulToolset`, through
`AgentRunMetrics` (`app/modules/agent/services/run_metrics.py`). In a
Prometheus-backed collector the dots become underscores and counters gain
`_total`, so `lemma.agent.runs` is `lemma_agent_runs_total`.

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `lemma.agent.runs` | counter | `outcome`, `kind`, `model` | How many runs ended, and how |
| `lemma.agent.run.duration` | histogram (s) | `kind`, `outcome` | How long runs take, by what started them |
| `lemma.agent.first_token` | histogram (s) | `model` | Time from the harness starting to the first streamed token -- the same moment the `lemma.agent.first_token` span closes |
| `lemma.agent.tool_calls` | counter | `tool_kind`, `outcome` | Which family of tool fails |
| `lemma.agent.runs.active` | up/down counter | `kind` | Runs executing in this process now |

What each label means, and what bounds it:

- `outcome` (runs) -- `completed`, `failed`, `cancelled` (a person stopped
  it), `waiting` (paused on a person: `ask_user`, an approval, a form), or
  `interrupted`: the worker went away mid-run (a deploy, a lost lease) and
  another worker resumes it, which counts it again when it does end. Read
  `interrupted` as a deploy signal, not a failure.
- `kind` -- what started the run, from `run_origin`: `chat` (a person in the
  app), `surface` (a channel such as Slack or WhatsApp), `schedule`,
  `workflow` (an AGENT step), `trigger`, `subagent` (another agent called it)
  or `pod_ask`. A function that starts an agent does so through one of these,
  so there is no separate `function` kind.
- `model` -- the model name the run's runtime resolved to. Bounded by the
  configured model catalog; cleaned to `[A-Za-z0-9_.:/-]` and 96 characters so
  a hand-typed custom model cannot carry anything odd. `unknown` when the run
  failed before its runtime resolved.
- `tool_kind` -- `function` (`function_*` tools), `agent` (`agent_*`
  tools), `connector` (the connector toolset), `sandbox` (the workspace CLI
  and the VM browser) or `builtin` (everything else Lemma ships).
- `outcome` (tool calls) -- `ok`, `error` (raised, or returned a failure),
  `cancelled`, or `control_flow` (a pause, retry or usage limit -- not a
  failure).

Never on these: a run, conversation, pod, agent or user id, or an agent's
name. Agents are named per pod, so names grow with the customer count. Those
live on the `agent.run` span. Tool calls are counted for in-process (LEMMA
harness) runs only; an Agent Host harness runs its own tools.

### Span names

Server spans used to be exported as `http.server`. A saved query or alert that
groups or filters by that span name goes empty after this change; point it at
`span.kind = SERVER` (or the `{METHOD} {route}` names) instead.


| Span | Name | Notes |
|---|---|---|
| Inbound request | `{METHOD} {route}`, e.g. `GET /pods/{pod_id}/agents` | OTel HTTP semconv. A request refused before routing (a 401 from auth, a 413) is given the route template its path matches; one matching no route is `GET unmatched`, and `http.route` is `unmatched` too. An unknown method is `HTTP` |
| Work item | `work.execute` | A root span per claimed item, carrying `lemma.work_class` and `lemma.work_kind` (which handler ran). A span named `job {kind}` would also survive export, but nothing emits one today |
| Agent run | `agent.run` | Not `agent.run {name}`: names are per pod and unbounded across tenants, so the name is the `lemma.agent_name` attribute. The root carries `lemma.pod_id`, `lemma.agent_id`, `lemma.conversation_id`, `lemma.agent_run_id`, `lemma.agent_name` and `lemma.run_kind` (the `kind` above) |

### HTTP semantic conventions

`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set in-process before the aiohttp
and httpx instrumentations are installed. Those two default to the superseded
conventions, which key outbound calls by `net.peer.name` — and that key is not
on the metric allowlist, so every third party collapsed into one series. pyqwest
(via `e2b` and `connectrpc`) already emits the stable conventions, so the
process was describing the same calls two ways.

**It was `http/dup` first, and the migration is now finished.** The variable is
process-global and the ASGI/FastAPI **server** instrumentation reads it too, so
`http` renames the inbound histogram as well — `http.server.duration` (ms) →
`http.server.request.duration` (s). `dup` emitted both vocabularies at once so
that rename could not break any dashboard on request latency:

| | superseded | stable |
|---|---|---|
| client | `http.client.duration` (ms) | `http.client.request.duration` (s), with `server.address` |
| server | `http.server.duration` (ms) | `http.server.request.duration` (s) |

Every inbound-latency panel reads `http.server.request.duration` now, so the
superseded series — 26 of them, still being paid for — have been switched off.
A deployment that still needs them can set the variable to `http/dup` itself;
the code only supplies a default.

Flipping to plain `http` and dropping the old series is a deliberate follow-up —
do it once the dashboards read the new names, not as a side effect of this. A
deployment can set the variable itself to pin either behaviour.


### Always tracing chosen accounts

Once production samples below 100%, an operator can still keep every request
from particular accounts -- your own, a customer you are debugging with -- by
naming their user ids:

```shell
OTEL_ALWAYS_SAMPLE_USER_IDS=0199a1b2-...,0199c3d4-...   # comma-separated
```

A request whose access token (the `sAccessToken` cookie or a bearer token)
carries one of those ids as `sub` is sampled, with every span created while
the request is served -- its database and HTTP calls, and any model call made
on the API's LLM tracer, which is forced the same way -- and the work it
queues: the work item carries the request's trace context, so an agent turn,
delivery or function run the request started is traced in the worker too
(see the correlation section).
Removing the id (and restarting) stops it. With the setting empty nothing is
installed.

The decision has to be made when the server span starts, before
authentication, so the `sub` is read from the token **without verifying it**
(a base64 decode, no database or network call;
`app/core/observability/forced_sampling.py`). That is acceptable only because
it decides nothing but sampling: a forged token can get a request traced --
as a forged sampled `traceparent` always could -- and is still rejected by
authentication. Find the user id with `SELECT id FROM users WHERE email = ...`
or from any of that user's spans (`lemma.user_id`).

### The edge: load-balancer trace and caller

`app/core/observability/edge.py`, applied by `RequestObserverMiddleware`.

**Trace continuation.** The global propagator is W3C `traceparent`, then
baggage, then Google's `X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1`, which the
external HTTPS load balancer sends. The Cloud Trace header is read only when
no `traceparent` produced a context, so W3C wins. It is extract-only: outbound
calls and outbox events still carry `traceparent` alone. The root server span
also records the header's trace id as `gcp.lb.trace_id`, whatever the
propagator did, so a load-balancer log line can always be matched.

**`lemma.caller.kind`** on the server span. The first rule that applies wins:

| Kind | When |
|---|---|
| `agent-sandbox` | authenticated with a delegated workload or function-run token |
| `webhook` | a surface (`/surfaces/webhooks/...`) or connector (`/webhooks/...`) route |
| `cli` / `sdk` / `browser` | `X-Lemma-Client` names `lemma-cli`; `lemma-sdk-py`/`-ts`; `lemma-web`/`-desktop`/`-app` |
| `browser` | a SuperTokens session cookie, or a `Mozilla/` user agent without a bearer token |
| `sdk` | a bearer token, or a programmatic user agent (`python-httpx`, `python-requests`, `node`, `axios`, `curl`) |
| `unknown` | none of the above |

The user agent is read to decide the kind and is not recorded.

**What the app does not record.** Client IP, user agent, country, region and
ASN, edge latency, and requests the load balancer refused before they reached
the app all come from the load balancer's own request logs
(`resource.type="http_load_balancer"`, 100% sampled) and an infra log-based
metric. They join to app traces through the continued trace id.

**Trust.** Any caller can send these headers, and nothing authorizes on them. A
forged trace header chooses which trace id a request joins and -- because the
sampler is parent-based -- can ask for its own request to be sampled, exactly
as a forged `traceparent` always could.

### Correlation: load balancer → trace → logs → metrics

On GKE, every app log line written inside a span also carries Cloud Logging's
native trace fields (`app/core/log/cloud_trace.py`):

| Key | Value |
|---|---|
| `logging.googleapis.com/trace` | `projects/<PROJECT>/traces/<trace_id>` |
| `logging.googleapis.com/spanId` | the span id |
| `logging.googleapis.com/trace_sampled` | whether the trace is sampled |

`<PROJECT>` is `OBSERVABILITY_TRACE_PROJECT_ID`, falling back to
`GOOGLE_CLOUD_PROJECT`. With neither set, these keys are omitted (a laptop is
unchanged). The payload's own `trace_id`/`span_id` stay for the tools that
read them.

**Async work joins the request's trace.** `WorkWriter.add` stores the current
W3C `traceparent` in the item (under `_traceparent`, removed again at claim, so
handlers never see it), and the dispatcher starts `work.execute` as its child.
The worker's parent-based sampler therefore follows the request's decision --
ratio, upstream or forced -- and one trace shows the request and the agent
turn, delivery or function run it queued. Retries are children of the same
parent. An item queued with no span current (start-up, maintenance) runs as a
root. A periodic's next run is queued by the run before it, so it carries a
*link* to that run (`_trace_link`) instead of a parent, rather than chaining
every run since boot into one trace (`app/core/infrastructure/work/trace_carry.py`).

So the path is: the load balancer's log line (native `trace`) leads to the
app trace (same id, continued), which leads to the app's log lines (same
native `trace`). From a latency metric, the histogram's exemplar leads back
to a trace.

## Exemplars: click the spike, see the request

Every explicit-bucket histogram keeps, per bucket and per export interval, the
last measurement that was recorded inside a **sampled** span, as an exemplar
carrying `trace_id`, `span_id`, the value and the time. A slow bucket therefore
links to one real trace that landed in it.

| Histogram (Prometheus name) | Recorded inside |
|---|---|
| `lemma_http_server_duration_ms` | the FastAPI server span (the OTel ASGI middleware is outermost, so `RequestObserverMiddleware` records inside it) |
| `lemma_work_handler_duration_seconds` (`lemma.work.handler.duration`) | the `work.execute` root span the dispatcher opens per claimed item |
| `http_server_duration_milliseconds` and other instrumentation histograms | their own instrumentation span, when one is current |

`lemma.work.claim_latency` is recorded in the claim loop, outside any span, so
it carries none. Counters and gauges deliberately keep none.

How it is configured (`app/core/observability/exemplars.py`, its `metric_view()` is the
meter provider's only View, set in `telemetry.py`):

- **Filter: `TraceBasedExemplarFilter`.** No current span, or an unsampled one,
  means no exemplar. At the default 5% `parentbased_traceidratio` sampling,
  roughly one request in twenty is eligible; that is enough for every bucket
  that sees real traffic, and every exemplar points at a trace that was exported.
- **No attributes.** The SDK would otherwise copy every measurement attribute
  the allowlist View dropped (identifiers, ids) into the exemplar's
  `filtered_attributes` — the reason exemplars used to be off. The reservoir is
  offered the measurement without attributes, so nothing but the trace link
  leaves the process. A unit test asserts this.

Using one: in Cloud Monitoring / Managed Prometheus, chart a `histogram_quantile`
over `lemma_http_server_duration_ms_bucket`; exemplars render as dots on the
chart. Click one, take its trace id, and open the trace in Cloud Trace (or
search the trace id in ClickStack/HyperDX). In PromQL terms, the exemplar on the
bucket that moved *is* the request that moved it.

To record a new histogram with exemplars, record it while the span that
represents the work is current — inside `with tracer.start_as_current_span(...)`,
before it closes. A measurement recorded after the span ends gets no exemplar.

### What the collector and GMP must do (infra verification)

The OTLP metric exporter sends exemplars on histogram data points as-is; no
SDK setting beyond the filter is needed. Downstream:

- **Collector:** pass metrics through without a processor that rebuilds data
  points. `batch`, `memory_limiter`, `resource`, `resourcedetection` and
  `attributes` keep exemplars. `transform`/`metricstransform` rules that
  aggregate or re-bucket histograms, and any OTLP→Prometheus *scrape* hop
  (`prometheus` exporter + scrape) drop them unless OpenMetrics exemplars are
  enabled end to end — prefer pushing with `googlemanagedprometheus`.
- **`googlemanagedprometheus` exporter:** writes histogram exemplars as Cloud
  Monitoring exemplars, attaching the span as
  `projects/<PROJECT>/traces/<TRACE_ID>/spans/<SPAN_ID>`. The project in that
  link is the exporter's project; the trace must be in **Cloud Trace in that
  project** for the click-through to resolve. If traces go elsewhere (ClickStack),
  the exemplar still carries the trace id — copy it — but the console link will
  not open.
- Cloud Monitoring stores exemplars on distribution (histogram) values; we
  emit none on counters, so nothing depends on how a hop treats those.
- Verify: after a deploy, run in Metrics Explorer (PromQL)
  `histogram_quantile(0.99, sum by (le) (rate(lemma_http_server_duration_ms_bucket[5m])))`
  and confirm exemplar dots appear; or read the time series via the Monitoring
  API and check `distributionValue.exemplars[].attachments` contains a
  `SpanContext`. No dots after 10 minutes of traffic means a hop dropped them —
  start by checking the collector's `debug` exporter at `verbosity: detailed`
  for `Exemplars:` on `lemma.http.server.duration_ms`.

## LLM observability

Pydantic AI/OpenInference traces use a separate, disabled-by-default pipeline
that never touches the general OTLP endpoint or the sanitizer above:

```dotenv
LLM_OTEL_ENABLED=true
LLM_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:16317
LLM_OTEL_EXPORTER_OTLP_PROTOCOL=grpc
```

Unlike the general pipeline, this one ships **full prompt/response content**
by design whenever it's enabled — that's the entire point of turning it on.
There's no separate opt-in flag for content: `LLM_OTEL_ENABLED=true` means
"send everything a developer needs to review what was sent to the model,"
including system instructions, tool calls, and token usage. Never enable this
against a shared or production Phoenix instance without understanding that
implication.

The LLM pipeline defaults to independent deterministic 1% sampling. Configure
`LLM_OTEL_TRACES_SAMPLER` and `LLM_OTEL_TRACES_SAMPLER_ARG` as needed.
`make dev LLM_OTEL=1` sets `always_on` sampling instead, so every local call
shows up in Phoenix.

### A conversation is a session, not a trace

One agent run is one trace, rooted at the `agent.run` span. A conversation is
many runs, so a conversation is many traces — and what joins them back together
in Phoenix is the OpenInference `session.id` attribute.

**Two things write that attribute, and they have to agree.** On the model spans
it comes from the OpenInference instrumentation, which derives it from
pydantic-ai's `gen_ai.conversation.id` and overwrites whatever was on the span at
start — so the harness passes our conversation id into
`Agent.iter(conversation_id=...)` to make that value ours. Left unset,
pydantic-ai takes it from the most recent conversation id on `message_history`,
and we rebuild history from the database every run, so there is never one to
inherit and it mints a fresh UUID7 per run. On the root `agent.run` span it comes
from `agent_run_telemetry_context`, because that span lives on the general
provider and never sees that instrumentation. Phoenix binds a trace to a session
from whichever of the trace's spans it happens to insert first, so both writers
naming the same id is what makes the binding deterministic rather than a race. Our own
`lemma.conversation_id` rides along beside it for the general pipeline, but it
is a filter, not a grouping key: Phoenix's Sessions view reads `session.id` and
nothing else. `user.id` is set the same way, from the same context, and the
`lemma.*` fields are restated as a JSON `metadata` blob so they are filterable
as an object rather than as a dozen loose attributes.

The root `agent.run` span is created on the **general** tracer, because the SQL
and HTTP work beneath it belongs in the infrastructure pipeline, while the model
spans below it are created on the LLM tracer. Two providers, one trace id — so
the root has to be copied across, or Phoenix receives children whose parent it
was never sent and shows every run as a headless fragment with no session, no
input and no output. `_build_llm_fanout_processor` is that copy, and it forwards
only spans that already carry an OpenInference kind: fanning out everything is
what once filled Phoenix with `db.operation` noise.

**The copied span arrives re-rooted.** Phoenix is sent `agent.run` and not the
worker job span above it, so the span references a parent that backend will never
receive — and an orphan is not a root to the software reading it. Phoenix resolves
a trace's root with a literal `parent_id IS NULL`, with no orphan fallback in
`trace_root_spans` or in the session input/output loaders, so an orphaned trace
still counts toward `numTraces` while every panel that renders *through* the root
span comes back empty: the session header says "2 traces" above a pane that says
there are none. `TraceRootingSpanExporter` drops the parent on that one exporter's
copy. Cloud Trace still gets the real one, and the model spans still name
`agent.run` as their parent, because its span id is untouched.

The consequence for sampling: **the general ratio must not be lower than the LLM
ratio.** Set it lower and the root is sampled away while its children are kept,
which is the headless-fragment failure by another route. Both are `1.0` wherever
Phoenix is enabled today.

## The dashboard

`make observability-up` provisions "Lemma API Overview" in HyperDX
(`scripts/hyperdx_bootstrap.py`), organized into containers:

- **Overview** — Requests, Errors, P95 Duration (the three RED-metric KPIs,
  scoped to inbound `SpanKind:"Server"` spans — i.e. calls *to* this API, not
  calls this API makes outward).
- **Performance** — Latency percentiles (p50/p95/p99) and a duration
  distribution heatmap.
- **Endpoints** — Per-route request/error/P95 breakdown. This is a raw SQL
  tile, not a builder table — HyperDX's builder table + map-attribute
  `groupBy` combination silently ignores `where` filters (a known gap in the
  tool itself), so raw SQL is the reliable path here.
- **Errors** — Errors by span kind over time (inbound endpoint failures vs.
  outbound dependency failures look very different — most local errors are
  outbound `Client`-kind calls, e.g. sandbox polling), errors by HTTP status
  code, and a recent-error-spans search tile.
- **Logs** — Volume by severity and a recent warnings/errors search tile
  (now that `OTEL_LOGS=1` ships logs by default).

The dashboard defaults to a **15-minute time window** — if a tile looks
empty after `make dev`, widen it to 1h or 24h first before assuming
something's broken.

## The ClickStack MCP server

HyperDX ships a first-party MCP server at `/api/mcp`
(Streamable HTTP, Bearer auth using your **Personal API Access Key** — a
different credential from the OTLP ingestion key, printed by
`make observability-up` as `CLICKSTACK_ACCESS_KEY`). It lets a coding agent
query traces/logs/metrics, build and validate dashboards, and inspect
individual trace waterfalls directly — the same tools used to build the
dashboard above.

This repo's `.mcp.json` already registers it for Claude Code as `clickstack`,
reading the key from the `CLICKSTACK_ACCESS_KEY` env var (never committed).
One-time setup per shell session:

```shell
eval "$(make observability-up 2>&1 | grep CLICKSTACK_ACCESS_KEY)"
```

or just copy the `export CLICKSTACK_ACCESS_KEY=...` line `make observability-up`
prints and paste it before starting Claude Code.

For other clients:

```shell
# Codex CLI
export CLICKSTACK_ACCESS_KEY="<key>"
codex mcp add clickstack --url http://localhost:8080/api/mcp \
  --bearer-token-env-var CLICKSTACK_ACCESS_KEY

# Claude Code, manual registration (if not using the repo's .mcp.json)
claude mcp add --transport http clickstack http://localhost:8080/api/mcp \
  --header "Authorization: Bearer <key>"
```

```json
// Cursor — .cursor/mcp.json
{
  "mcpServers": {
    "clickstack": {
      "url": "http://localhost:8080/api/mcp",
      "headers": { "Authorization": "Bearer <key>" }
    }
  }
}
```

Example prompts once connected:

- "What are the slowest endpoints in the last hour?"
- "Show me the last 5 errors and their status codes."
- "Pull up the full prompt and response for the most recent LLM call."
- "Is there anything unusual in the logs in the last 15 minutes?"

## The Phoenix MCP server

Phoenix ships one too, at `/mcp` on the same port as its UI. It answers
questions the ClickStack one cannot: what was actually in a model request, how
the prompt grew turn by turn, which spans belong to one conversation.

The local Phoenix runs unauthenticated — the compose service sets only
`PHOENIX_WORKING_DIR`, with no `PHOENIX_ENABLE_AUTH` and no secret — so unlike
ClickStack there is no key to export and no OAuth to complete. `.mcp.json`
registers it as `phoenix-local`:

```json
"phoenix-local": { "type": "http", "url": "http://localhost:16006/mcp" }
```

Nothing else is needed beyond `make observability-up`. For other clients:

```shell
# Codex CLI
codex mcp add phoenix-local --url http://localhost:16006/mcp

# Claude Code, manual registration
claude mcp add --transport http phoenix-local http://localhost:16006/mcp
```

Prefer this over pointing an agent at a deployed Phoenix. A remote one needs an
interactive OAuth flow that a headless session cannot complete, and you are
then querying production data to debug a local change.

## Local debug Collector (CI-safe correctness check, not for dashboards)

Separate from the real stack above, the repository includes a pinned
OpenTelemetry Collector with separate general and LLM receivers and the
detailed CLI `debug` exporter — console-only, no ClickHouse/Mongo, fast to
start. It exists purely to back `make otel-smoke`, a deterministic
traces/metrics/logs/LLM canary test:

```shell
make otel-up
make otel-tail
make otel-smoke   # runs the canary end-to-end and asserts on collector output
make otel-down
```

`make otel-smoke` asserts the general pipeline never leaks the canary's
adversarial `db.statement`/`url.full` content (proving the sanitizer works)
and that the LLM pipeline *does* carry its canary prompt (proving content
capture works) — the same two invariants described above, exercised without
needing HyperDX or Phoenix running.
