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
other high-cardinality attributes are removed before export. Exemplars are
disabled so filtered attributes cannot survive inside exemplar payloads.

The general pipeline's export boundary (`app/core/observability/span_sanitizer.py`)
is default-deny: only a fixed allowlist of attribute keys crosses it (route
templates, status codes, token counts, bounded Lemma business-context fields),
strings are truncated to 256 chars, and span names are collapsed to a small
safe set (`http.server`, `db.operation`, ...) rather than the raw operation
name. This is why the dashboard's "Endpoints" tile groups by the
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

### Metrics worth knowing about

| Metric | Labels | Answers |
|---|---|---|
| `lemma.http.server.requests` | `http.route`, `http.request.method`, `http.response.status_code` | Per-route error rate. Exact status, so it joins the FastAPI instrumentation's own histogram |
| `http.client.request.duration` | `server.address`, `http.request.method`, `http.response.status_code` | Which third party is slow |
| `db.client.connections.usage` | `pool.name`, `state` | Pool utilisation per pool. `pool.name` is safe only because the engines set an explicit `pool_logging_name`; the instrumentation's fallback is the DSN |
| `lemma.worker.queue.depth` | `lane` | How much work is *waiting*, which throughput cannot tell you |
| `lemma.event.outbox.pending` / `.inbox.pending` | — | A growing pile behind a healthy-looking publish rate |
| `lemma.llm.tokens` | `gen_ai.request.model`, `gen_ai.token.type` | Tokens per day by model |
| `lemma.llm.cost_usd` | `gen_ai.request.model` | Spend, next to everything else |

The backlog gauges are sampled by a loop on the **worker** and report nothing
until the first successful sample — a gauge that reports a stale level reads as
a healthy steady state, which is the failure these exist to catch.

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
in Phoenix is the OpenInference `session.id` attribute, which
`agent_run_telemetry_context` sets to the conversation id. Our own
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
