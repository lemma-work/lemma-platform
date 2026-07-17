"""Structured logging for Lemma services.

Every application-owned stdout/stderr event is exactly one JSON object on one
physical line. Two producer paths feed a single handler:

* **Application code** uses :func:`get_logger` and goes through the structlog
  processor chain.
* **Foreign loggers** (the standard library ``logging`` module used by httpx,
  uvicorn, Azure SDK, …) are routed through the same processor chain by
  :class:`structlog.stdlib.ProcessorFormatter`.

Both paths share one pre-chain — timestamping, severity, trace context, static
service/release context, exception formatting, and redaction — so a foreign
``httpx`` line and an application ``service.started`` line are indistinguishable
in shape and both safe (no credentials, no bodies, one line).
"""

import logging
import re
import structlog
import sys
from typing import Any, Protocol

from opentelemetry import trace

from app.core.redaction import redact_event_dict

# Static context merged onto every event dict by ``_add_static_context``.
# Populated by ``setup_logging`` with service/environment/release metadata.
_logging_context: dict[str, Any] = {}

# Whether the single console handler has already been installed. ``setup_logging``
# is called at import time with a safe default and again from each service
# entrypoint with real metadata; the handler must only be attached once so
# repeated calls never duplicate output.
_logging_configured: bool = False

# 40-character lowercase-hex Git SHA.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Foreign (stdlib) loggers whose routine INFO output is not useful console
# telemetry. Successful dependency requests are suppressed; failures still
# surface at WARNING+. Applied in every environment — the volume problem is the
# same in dev and prod, and a wedged dependency is visible either way.
_FOREIGN_LOGGER_LEVELS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "uvicorn.access": logging.WARNING,
    "uvicorn.error": logging.INFO,
    "streaq": logging.WARNING,
    "faststream": logging.WARNING,
    "azure": logging.WARNING,
    "azure.core": logging.WARNING,
    "e2b": logging.WARNING,
}


class Logger(Protocol):
    """Protocol defining our logger interface for type checking"""

    def debug(self, event: str, **kwargs: Any) -> None: ...
    def info(self, event: str, **kwargs: Any) -> None: ...
    def warning(self, event: str, **kwargs: Any) -> None: ...
    def error(self, event: str, **kwargs: Any) -> None: ...
    def exception(self, event: str, **kwargs: Any) -> None: ...
    def bind(self, **kwargs: Any) -> "Logger": ...


def _add_trace_context(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    span = trace.get_current_span()
    span_context = span.get_span_context() if span else None
    if span_context and span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


def _add_static_context(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for key, value in _logging_context.items():
        if value is not None and key not in event_dict:
            event_dict[key] = value
    return event_dict


def _add_logger_name(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Set ``logger`` for foreign stdlib records (structlog records bind it already)."""
    if "logger" not in event_dict:
        record: logging.LogRecord | None = event_dict.get("_record")
        if record is not None:
            event_dict["logger"] = record.name
    return event_dict


def _add_exc_info(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Copy ``exc_info`` from a foreign stdlib record so ``format_exc_info`` renders it.

    structlog records already carry ``exc_info`` when produced by ``.exception()``;
    this is a no-op for them (``_record`` is not yet attached during the structlog
    pre-chain). For foreign records, ``ProcessorFormatter`` attaches ``_record``
    before the foreign pre-chain runs, and this is how their traceback enters the
    shared exception formatter — as one JSON-escaped string, not a fan-out of
    physical lines.
    """
    record: logging.LogRecord | None = event_dict.get("_record")
    if (
        record is not None
        and record.exc_info
        and "exc_info" not in event_dict
    ):
        event_dict["exc_info"] = record.exc_info
    return event_dict


def _is_otel_handler(handler: logging.Handler) -> bool:
    module = handler.__class__.__module__
    return module.startswith("opentelemetry.")


def _deployment_environment(env: str) -> str:
    """Collapse the runtime environment to the bounded cardinality the log contract uses."""
    return "production" if env == "production" else "development"


def _shared_processors() -> list:
    """Processors applied to both structlog and foreign records, in order."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_trace_context,
        _add_static_context,
        _add_logger_name,
        _add_exc_info,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_event_dict,
    ]


def _resolve_release_sha() -> tuple[str | None, bool]:
    """Return ``(raw_value, is_valid)`` for the configured release SHA.

    ``raw_value`` is the stripped ``LEMMA_RELEASE_SHA`` (or ``None`` when unset/
    settings unavailable). ``is_valid`` is True only for a 40-char lowercase-hex
    SHA. Callers decide the display value (real SHA vs ``"unknown"``) and whether
    to warn; release identity is best-effort metadata and never blocks startup.
    """
    try:
        from app.core.config import settings

        raw = (settings.release_sha or "").strip()
    except Exception:  # pragma: no cover - settings unavailable during early import
        return None, False
    if _SHA_RE.match(raw):
        return raw, True
    return (raw or None), False


# Display value used on every log line when no valid release SHA is configured.
# Kept queryable (``release.sha == "unknown"``) rather than omitted so an alert
# can surface unattributed deployments.
_RELEASE_SHA_UNKNOWN = "unknown"


def setup_logging(
    env: str = "production",
    *,
    service_name: str | None = None,
    json_logs: bool = True,
    log_level: str = "INFO",
) -> None:
    """Configure structured logging for the application.

    Safe to call more than once: the console handler is attached exactly once
    and OpenTelemetry handlers attached later (by ``init_telemetry``) are
    preserved. Re-calls only refresh static context and levels.
    """
    global _logging_configured

    sha_value, is_valid_sha = _resolve_release_sha()
    release_sha = sha_value if is_valid_sha else _RELEASE_SHA_UNKNOWN
    _logging_context.clear()
    _logging_context.update(
        {
            "service.name": service_name,
            "deployment.environment": _deployment_environment(env),
            "service.version": release_sha,
            "release.sha": release_sha,
        }
    )

    resolved_level = getattr(logging, log_level.upper(), logging.INFO)
    shared = _shared_processors()
    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=shared + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(resolved_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    root_logger = logging.getLogger()
    if not _logging_configured:
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        # Preserve any OpenTelemetry handlers that may already be attached; add
        # our single console handler once.
        preserved_handlers = [
            handler for handler in root_logger.handlers if _is_otel_handler(handler)
        ]
        root_logger.handlers = [stream_handler, *preserved_handlers]
        _logging_configured = True

    root_logger.setLevel(resolved_level)
    for name, level in _FOREIGN_LOGGER_LEVELS.items():
        logging.getLogger(name).setLevel(level)


def validate_release_identity(env: str) -> None:
    """Warn when ``LEMMA_RELEASE_SHA`` is missing or malformed.

    Release identity is best-effort log/release correlation metadata — it never
    blocks startup. A missing or non-40-hex value logs a single warning (visible
    to on-call and alertable via ``release.sha == "unknown"``) and the log
    context falls back to ``"unknown"``. This keeps the logging remediation
    decoupled from infra-side env wiring: the volume fix lands safely even if
    ``LEMMA_RELEASE_SHA`` is not yet set in a given environment.
    """
    sha, is_valid = _resolve_release_sha()
    if is_valid:
        return
    if sha is None:
        get_logger(__name__).warning(
            "release.sha missing",
            hint="set LEMMA_RELEASE_SHA to the 40-char source git sha for log/release correlation",
            deployment_environment=_deployment_environment(env),
        )
    else:
        get_logger(__name__).warning(
            "release.sha malformed",
            value=sha,
            hint="LEMMA_RELEASE_SHA must be a 40-char lowercase-hex git sha; falling back to 'unknown'",
            deployment_environment=_deployment_environment(env),
        )


def get_logger(name: str) -> Logger:
    """Get a logger instance for the given module (typically ``__name__``)."""
    return structlog.get_logger().bind(logger=name)  # type: ignore[return-value]


# Configure a safe default immediately so module-level loggers created during
# imports already emit structured logs. Applications reconfigure this later
# with the right service metadata (and call validate_release_identity).
setup_logging()
