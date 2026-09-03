"""Where OpenTelemetry meets Python's ``logging`` module, in both directions.

Two halves of one seam, split out of :mod:`telemetry` because they are the only
part of it that is about log records rather than about standing providers up.

Outbound: the app's structured records are bridged onto OTLP by
:class:`SanitizingLoggingHandler`, which copies across a fixed allowlist of
bounded fields and nothing else — a log pipeline is the easiest place for a
message body or a customer's name to leave the deployment, and a bridge that
forwarded whatever was on the record would be exactly that.

Inbound: the OTLP exporters log their own failures through the same ``logging``
module, once per failed export. A collector that is down or not yet serving
turns that into a wall of identical lines during precisely the outage somebody
is trying to read, so :func:`quiet_otlp_export_logs` rate-limits them.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import time

from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter as GrpcOTLPLogExporter,
)
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpOTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogExporter
from opentelemetry.sdk.resources import Resource

from app.core.redaction import redact_text

_SAFE_OTEL_LOG_FIELDS = frozenset(
    {
        "request_id",
        "correlation_id",
        "event_id",
        "causation_id",
        "job_id",
        "event_type",
        "consumer",
        "task_name",
        "job_attempt",
        "attempt",
        "outcome",
        "duration_ms",
        "incident_duration_ms",
        "failure_count",
        "count",
        "method",
        "route",
        "status_code",
        "latency_kind",
        "error_type",
        "error_code",
        "error_stack_hash",
        "retryable",
    }
)

_logs_initialized = False
_logger_provider: LoggerProvider | None = None


class SanitizingLoggingHandler(LoggingHandler):
    """Translate only bounded structured fields into OTLP log records."""

    def emit(self, record: logging.LogRecord) -> None:
        candidate = record.msg if isinstance(record.msg, Mapping) else None
        event = candidate.get("event") if isinstance(candidate, Mapping) else None
        if isinstance(candidate, Mapping):
            if not isinstance(event, str) or len(event) > 128:
                event = "logging.contract.violation"
        else:
            try:
                event = redact_text(record.getMessage())
            except Exception:
                event = "unrenderable log record"
        safe_record = logging.LogRecord(
            name=record.name,
            level=record.levelno,
            pathname="",
            lineno=0,
            msg=event[:512],
            args=(),
            exc_info=None,
        )
        # `object` rather than `Any`: every value is narrowed by the two
        # isinstance checks below before it is copied, and nothing is called on
        # one.
        source_fields: dict[str, object] = {}
        if isinstance(candidate, Mapping):
            source_fields.update(candidate)
        lemma_fields = getattr(record, "lemma_fields", None)
        if isinstance(lemma_fields, Mapping):
            source_fields.update(lemma_fields)
        for key, value in source_fields.items():
            if key not in _SAFE_OTEL_LOG_FIELDS:
                continue
            if isinstance(value, str):
                setattr(safe_record, key, " ".join(value.splitlines())[:256])
            elif isinstance(value, bool | int | float):
                setattr(safe_record, key, value)
        super().emit(safe_record)


def build_log_exporter(
    endpoint: str,
    *,
    protocol: str,
    headers: dict[str, str] | None = None,
) -> LogExporter:
    if protocol == "http/protobuf":
        return HttpOTLPLogExporter(endpoint=endpoint, headers=headers)
    return GrpcOTLPLogExporter(
        endpoint=endpoint,
        headers=headers,
        insecure=endpoint.startswith("http://") or "://" not in endpoint,
    )


def setup_otel_logs(
    *,
    resource: Resource,
    endpoint: str,
    protocol: str,
    headers: dict[str, str] | None,
) -> LoggerProvider:
    """Attach the OTLP log bridge to the root logger, once per process.

    Idempotent because the handler is added to the root logger: calling twice
    would double every record. Which signals are enabled and where they go is
    decided by the caller — this only knows how to wire up the one it is given.
    """
    global _logs_initialized
    global _logger_provider
    if _logs_initialized and _logger_provider is not None:
        return _logger_provider

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            build_log_exporter(endpoint, protocol=protocol, headers=headers)
        )
    )
    set_logger_provider(provider)
    logging.getLogger().addHandler(
        SanitizingLoggingHandler(level=logging.NOTSET, logger_provider=provider)
    )
    _logs_initialized = True
    _logger_provider = provider
    return provider


class _RateLimitedLogFilter(logging.Filter):
    """Collapse repeated OTLP exporter failures to one line per interval.

    The OTLP exporters log on every failed/retried export; when a collector is
    down or not yet serving this floods the dev logs. We keep the first
    occurrence of each distinct message, then suppress repeats for `interval`.
    """

    def __init__(self, interval_seconds: float = 60.0) -> None:
        super().__init__()
        self._interval = interval_seconds
        self._last_emit: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        key = f"{record.name}:{message[:48]}"
        now = time.monotonic()
        last = self._last_emit.get(key)
        if last is not None and (now - last) < self._interval:
            return False
        self._last_emit[key] = now
        return True


# OTLP exporter modules that emit the noisy "Transient error ... retrying" and
# "Failed to export ..." lines when a collector is unreachable.
_OTLP_EXPORTER_LOGGERS = (
    "opentelemetry.exporter.otlp.proto.grpc.exporter",
    "opentelemetry.exporter.otlp.proto.grpc._log_exporter",
    "opentelemetry.exporter.otlp.proto.grpc.metric_exporter",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http._log_exporter",
    "opentelemetry.exporter.otlp.proto.http.metric_exporter",
)

_otlp_log_filter = _RateLimitedLogFilter()
_otlp_logs_quieted = False


def quiet_otlp_export_logs() -> None:
    """Rate-limit OTLP exporter failure logs so a down collector can't spam."""
    global _otlp_logs_quieted
    if _otlp_logs_quieted:
        return
    for name in _OTLP_EXPORTER_LOGGERS:
        logging.getLogger(name).addFilter(_otlp_log_filter)
    _otlp_logs_quieted = True
