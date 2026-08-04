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
  outbound `Client`-kind calls, e.g. AgentBox polling), errors by HTTP status
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
