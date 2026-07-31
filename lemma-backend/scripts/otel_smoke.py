"""Emit safe traces, metrics, and logs to the local debug Collector."""

from __future__ import annotations

import argparse
import os


def _configure(args: argparse.Namespace) -> None:
    os.environ.update(
        {
            "OBSERVABILITY_ENABLED": "true",
            "OTEL_EXPORTER_OTLP_ENDPOINT": args.endpoint,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "otlp",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_TRACES_SAMPLER": "always_on",
            "OTEL_METRIC_EXPORT_INTERVAL": "1000",
            "LEMMA_ENVIRONMENT": "local",
            "LEMMA_RELEASE_SHA": "0" * 40,
        }
    )
    if args.llm_endpoint:
        os.environ.update(
            {
                "LLM_OTEL_ENABLED": "true",
                "LLM_OTEL_EXPORTER_OTLP_ENDPOINT": args.llm_endpoint,
                "LLM_OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                "LLM_OTEL_TRACES_SAMPLER": "always_on",
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:14317")
    parser.add_argument("--llm-endpoint", default="http://127.0.0.1:15317")
    args = parser.parse_args()
    _configure(args)

    from opentelemetry import metrics, trace

    from app.core.log.log import get_logger, setup_logging
    from app.core.observability import telemetry

    setup_logging("local", service_name="lemma-otel-smoke", json_logs=True)
    telemetry.init_telemetry("lemma-otel-smoke")
    tracer = trace.get_tracer("app.otel_smoke")
    with tracer.start_as_current_span(
        "observability.smoke",
        attributes={
            "http.request.method": "GET",
            "http.route": "/otel-smoke/{id}",
            "url.full": "https://CANARY.invalid/private?token=CANARY",
            "db.statement": "SELECT 'CANARY'",
        },
    ):
        get_logger("app.otel_smoke").info("service.started")
        metrics.get_meter("app.otel_smoke").create_counter(
            "lemma.observability.smoke"
        ).add(1, {"outcome": "succeeded", "request_id": "CANARY"})

    llm_provider = telemetry._llm_trace_provider
    if llm_provider is not None:
        llm_tracer = llm_provider.get_tracer("pydantic-ai")
        with llm_tracer.start_as_current_span(
            "model request CANARY",
            attributes={
                "openinference.span.kind": "LLM",
                "gen_ai.request.model": "smoke-model",
                "gen_ai.prompt": "CANARY prompt",
                "input.value": "CANARY input",
            },
        ):
            pass
    telemetry.shutdown_telemetry()


if __name__ == "__main__":
    main()
