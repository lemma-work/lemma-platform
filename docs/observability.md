# OpenTelemetry

Lemma can export traces, metrics, and structured application logs to any OTLP
collector. The implementation has no dependency on a particular cloud or
observability vendor.

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

Root traces use `parentbased_traceidratio` sampling at 5% by default. Configure
the standard `OTEL_TRACES_SAMPLER` and `OTEL_TRACES_SAMPLER_ARG` variables to
change it. Metrics export every 60 seconds by default via
`OTEL_METRIC_EXPORT_INTERVAL`. Metric identifiers, URLs, SQL, Redis keys, and
other high-cardinality attributes are removed before export. Exemplars are
disabled so filtered attributes cannot survive inside exemplar payloads.

## LLM observability

Pydantic AI/OpenInference traces use a separate, disabled-by-default pipeline.
They never go to the general OTLP endpoint and never emit LLM metrics or logs.
Enable it with:

```dotenv
LLM_OTEL_ENABLED=true
LLM_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:16317
LLM_OTEL_EXPORTER_OTLP_PROTOCOL=grpc
```

The LLM pipeline defaults to independent deterministic 1% sampling. Configure
`LLM_OTEL_TRACES_SAMPLER` and `LLM_OTEL_TRACES_SAMPLER_ARG` as needed. Prompt,
response, tool argument/result, system-instruction, and binary capture is
disabled at instrumentation time and enforced again by the export allowlist.

## Local debug Collector

The repository includes a pinned OpenTelemetry Collector with separate general
and LLM receivers and the detailed CLI `debug` exporter:

```shell
make otel-up
make dev OTEL=1
make otel-tail
```

`make dev OTEL=1` enables general traces and metrics. Add `OTEL_LOGS=1` to test
OTLP logs and `LLM_OTEL=1` to test the separate LLM receiver. Neither is enabled
by default.

Run `make otel-smoke` for a deterministic traces/metrics/logs/LLM canary test.
It fails if a prompt, SQL statement, URL, or filtered metric attribute reaches
the Collector. `make otel-down` stops the debug Collector.
