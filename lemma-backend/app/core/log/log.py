"""Single-line, bounded structured logging for every Lemma service."""

from __future__ import annotations

import hashlib
from enum import Enum
import logging
import os
from pathlib import Path
import re
import sys
import traceback
from typing import Any, Protocol
from uuid import UUID

from opentelemetry import trace
import structlog

from app.core.log.event_catalog import EVENT_CATALOG
from app.core.redaction import redact_event_dict
from app.core.request_context import current_observability_context


_logging_context: dict[str, Any] = {}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_STABLE_EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_RELEASE_SHA_UNKNOWN = "unknown"
_CONSOLE_HANDLER_MARKER = "_lemma_json_console_handler"
_APP_RECORD_MARKER = "_lemma_app_owned"
_release_warning_emitted: set[str] = set()
_contract_violation_emitted = False

_CONTRACT_METADATA_FIELDS = {
    "causation_id",
    "consumer",
    "correlation_id",
    "deployment.environment",
    "dropped_field_count",
    "error_frames",
    "error_stack_hash",
    "error_type",
    "event",
    "event_id",
    "event_type",
    "job_attempt",
    "job_id",
    "level",
    "logger",
    "release.sha",
    "request_id",
    "service.name",
    "service.version",
    "span_id",
    "task_name",
    "timestamp",
    "trace_id",
}

_FOREIGN_LOGGER_LEVELS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "uvicorn": logging.WARNING,
    "uvicorn.access": logging.WARNING,
    "uvicorn.error": logging.WARNING,
    "streaq": logging.WARNING,
    "faststream": logging.WARNING,
    "apscheduler": logging.WARNING,
    "sqlalchemy": logging.WARNING,
    "azure": logging.WARNING,
    "azure.core": logging.WARNING,
    "e2b": logging.WARNING,
}

_PROHIBITED_FIELDS = {
    "authorization",
    "body",
    "cookie",
    "headers",
    "message",
    "payload",
    "prompt",
    "request",
    "response",
    "source_text",
    "sql",
    "traceback",
    "url",
}


class ReleaseIdentityError(RuntimeError):
    """Raised when a production process cannot identify its deployed source."""


class LoggingContractError(ValueError):
    """Raised when local code violates the exact structured-log contract."""


def _strict_logging_contract_enabled() -> bool:
    configured = os.getenv("LEMMA_LOGGING_CONTRACT_STRICT")
    if configured is None:
        configured = os.getenv("LOGGING_CONTRACT_STRICT")
    enabled = (configured or "").strip().lower() in {"1", "true", "yes", "on"}
    raw_environment = (
        (os.getenv("LEMMA_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "local")
        .strip()
        .lower()
    )
    return enabled and raw_environment in {"local", "test", "testing"}


class Logger(Protocol):
    def debug(self, event: str, **kwargs: Any) -> None: ...
    def info(self, event: str, **kwargs: Any) -> None: ...
    def warning(self, event: str, **kwargs: Any) -> None: ...
    def error(self, event: str, **kwargs: Any) -> None: ...
    def exception(self, event: str, **kwargs: Any) -> None: ...
    def bind(self, **kwargs: Any) -> "Logger": ...


def _mark_app_record(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict[_APP_RECORD_MARKER] = True
    return event_dict


def _add_trace_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    span = trace.get_current_span()
    span_context = span.get_span_context() if span else None
    if span_context and span_context.is_valid:
        event_dict.setdefault("trace_id", format(span_context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span_context.span_id, "016x"))
    return event_dict


def _add_execution_context(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for key, value in current_observability_context().as_log_fields().items():
        event_dict.setdefault(key, value)
    return event_dict


def _add_static_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in _logging_context.items():
        if value is not None:
            event_dict.setdefault(key, value)
    return event_dict


def _add_logger_name(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    if "logger" not in event_dict:
        record: logging.LogRecord | None = event_dict.get("_record")
        if record is not None:
            event_dict["logger"] = record.name
    return event_dict


def _exception_info(
    event_dict: dict[str, Any],
) -> tuple[type[BaseException], BaseException, Any] | None:
    exc_info = event_dict.get("exc_info")
    if exc_info is True:
        exc_info = sys.exc_info()
    if not exc_info:
        record: logging.LogRecord | None = event_dict.get("_record")
        exc_info = record.exc_info if record is not None else None
    if not exc_info or not isinstance(exc_info, tuple) or len(exc_info) != 3:
        return None
    exc_type, exc, tb = exc_info
    if not isinstance(exc, BaseException) or not isinstance(exc_type, type):
        return None
    return exc_type, exc, tb


def _safe_module_name(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    for marker in ("/lemma-backend/app/", "/agentbox/agentbox/"):
        if marker in normalized:
            relative = normalized.split(marker, 1)[1].rsplit(".", 1)[0]
            prefix = "app" if "lemma-backend" in marker else "agentbox"
            return prefix + "." + relative.replace("/", ".")
    return Path(filename).stem


def _add_safe_exception(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    record: logging.LogRecord | None = event_dict.get("_record")
    precomputed = getattr(record, "lemma_safe_exception", None) if record else None
    info = _exception_info(event_dict)
    event_dict.pop("exc_info", None)
    event_dict.pop("exception", None)
    event_dict.pop("stack", None)
    event_dict.pop("stack_info", None)
    if isinstance(precomputed, dict):
        event_dict.update(precomputed)
        return event_dict
    if info is None:
        return event_dict

    exc_type, _exc, tb = info
    event_dict.update(_safe_exception_fields(exc_type, tb))
    return event_dict


def _safe_exception_fields(exc_type: type[BaseException], tb: Any) -> dict[str, Any]:
    extracted = traceback.extract_tb(tb) if tb is not None else []
    application = [
        frame
        for frame in extracted
        if "/lemma-backend/app/" in frame.filename.replace("\\", "/")
        or "/agentbox/agentbox/" in frame.filename.replace("\\", "/")
    ]
    selected = (application or extracted)[-8:]
    frames = [
        {
            "module": _safe_module_name(frame.filename),
            "function": frame.name,
            "line": frame.lineno,
        }
        for frame in selected
    ]
    fingerprint = "|".join(
        [exc_type.__name__]
        + [f"{frame['module']}:{frame['function']}:{frame['line']}" for frame in frames]
    )
    fields: dict[str, Any] = {
        "error_type": exc_type.__name__,
        "error_stack_hash": hashlib.sha256(fingerprint.encode()).hexdigest(),
    }
    if frames:
        fields["error_frames"] = frames
    return fields


class _SafeExceptionFilter(logging.Filter):
    """Replace raw LogRecord exception data before any handler can export it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            exc_type, _exc, tb = record.exc_info
            if isinstance(exc_type, type):
                record.lemma_safe_exception = _safe_exception_fields(exc_type, tb)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _install_safe_exception_filter(handler: logging.Handler) -> None:
    if not any(isinstance(item, _SafeExceptionFilter) for item in handler.filters):
        handler.addFilter(_SafeExceptionFilter())


def _bounded_contract(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Drop unsafe/unbounded values before they can reach stdout or OTLP."""
    global _contract_violation_emitted
    app_owned = bool(event_dict.pop(_APP_RECORD_MARKER, False))
    event = event_dict.get("event")
    violation: str | None = None
    if app_owned:
        if not isinstance(event, str) or not _STABLE_EVENT_RE.fullmatch(event):
            violation = "invalid_event_name"
        else:
            specification = EVENT_CATALOG.get(event)
            if specification is None:
                violation = "unregistered_event"
            elif event_dict.get("level") != specification.level:
                violation = "unexpected_severity"
            else:
                extra_fields = (
                    set(event_dict)
                    - _CONTRACT_METADATA_FIELDS
                    - set(specification.fields)
                )
                extra_fields = {key for key in extra_fields if not key.startswith("_")}
                if extra_fields:
                    violation = "unexpected_fields"
    else:
        # Dependency messages and interpolation arguments are not controlled by
        # Lemma and may contain URLs, SQL, or provider response content.
        event_dict["event"] = "dependency.reported"
        for key in list(event_dict):
            if key not in _CONTRACT_METADATA_FIELDS and not key.startswith("_"):
                event_dict.pop(key, None)

    if violation is not None:
        if _strict_logging_contract_enabled():
            raise LoggingContractError(violation)
        if _contract_violation_emitted:
            raise structlog.DropEvent
        _contract_violation_emitted = True
        safe = {
            key: value
            for key, value in event_dict.items()
            if key in _CONTRACT_METADATA_FIELDS and key not in {"event", "level"}
        }
        event_dict.clear()
        event_dict.update(safe)
        event_dict["event"] = "logging.contract.violation"
        event_dict["level"] = "error"
        event_dict["contract_violation"] = violation

    dropped = 0
    for key in list(event_dict):
        if key.startswith("_"):
            continue
        value = event_dict[key]
        lowered = key.lower()
        if lowered in _PROHIBITED_FIELDS or lowered == "error":
            event_dict.pop(key, None)
            dropped += 1
            continue
        if isinstance(value, str):
            event_dict[key] = " ".join(value.splitlines())[:512]
        elif isinstance(value, UUID):
            event_dict[key] = str(value)
        elif isinstance(value, Enum) and isinstance(value.value, str | int):
            event_dict[key] = value.value
        elif isinstance(value, bytes):
            event_dict.pop(key, None)
            dropped += 1
        elif key == "error_frames" and isinstance(value, list):
            continue
        elif isinstance(value, (dict, list, tuple, set)):
            event_dict.pop(key, None)
            dropped += 1
        elif not isinstance(value, bool | int | float) and value is not None:
            event_dict.pop(key, None)
            dropped += 1
    if dropped:
        event_dict["dropped_field_count"] = dropped
    return event_dict


def _is_otel_handler(handler: logging.Handler) -> bool:
    return handler.__class__.__module__.startswith("opentelemetry.")


def _is_console_handler(handler: logging.Handler) -> bool:
    if getattr(handler, _CONSOLE_HANDLER_MARKER, False):
        return True
    if isinstance(handler, logging.FileHandler):
        return False
    return isinstance(handler, logging.StreamHandler) and getattr(
        handler, "stream", None
    ) in {sys.stdout, sys.stderr}


def _deployment_environment(env: str) -> str:
    return "production" if env.lower() in {"prod", "production"} else "development"


def _bootstrap_environment() -> str:
    raw = (
        os.getenv("LEMMA_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "local"
    ).lower()
    return "production" if raw in {"prod", "production"} else "development"


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace_context,
        _add_execution_context,
        _add_static_context,
        _add_logger_name,
        _add_safe_exception,
        redact_event_dict,
        _bounded_contract,
    ]


def resolve_release_sha() -> tuple[str | None, bool]:
    try:
        from app.core.config import settings

        raw = (settings.release_sha or "").strip()
    except Exception:  # pragma: no cover - settings unavailable during bootstrap
        return None, False
    return (raw or None), bool(_SHA_RE.fullmatch(raw))


def release_sha_for_resource() -> str:
    value, valid = resolve_release_sha()
    return value if valid and value is not None else _RELEASE_SHA_UNKNOWN


def _processor_formatter(renderer: Any) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )


def _reconcile_named_loggers() -> None:
    manager = logging.root.manager.loggerDict
    prefixes = tuple(_FOREIGN_LOGGER_LEVELS)
    names = set(prefixes)
    names.update(
        name
        for name in manager
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)
    )
    for name in names:
        logger = logging.getLogger(name)
        logger.handlers = [
            handler for handler in logger.handlers if not _is_console_handler(handler)
        ]
        for handler in logger.handlers:
            _install_safe_exception_filter(handler)
        logger.propagate = True
        level = next(
            (
                configured
                for prefix, configured in _FOREIGN_LOGGER_LEVELS.items()
                if name == prefix or name.startswith(prefix + ".")
            ),
            logging.WARNING,
        )
        logger.setLevel(level)


def setup_logging(
    env: str | None = None,
    *,
    service_name: str | None = None,
    json_logs: bool = True,
    log_level: str = "INFO",
) -> None:
    """Install or reconcile the one application JSON console pipeline."""
    resolved_env = env or _bootstrap_environment()
    release_sha = release_sha_for_resource()
    _logging_context.clear()
    _logging_context.update(
        {
            "service.name": service_name or "lemma-bootstrap",
            "deployment.environment": _deployment_environment(resolved_env),
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
        processors=[
            _mark_app_record,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(resolved_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    root = logging.getLogger()
    owned = [
        handler
        for handler in root.handlers
        if getattr(handler, _CONSOLE_HANDLER_MARKER, False)
    ]
    preserved = [
        handler
        for handler in root.handlers
        if not getattr(handler, _CONSOLE_HANDLER_MARKER, False)
        and (not _is_console_handler(handler) or _is_otel_handler(handler))
    ]
    if owned:
        console = owned[0]
    else:
        console = logging.StreamHandler(sys.stdout)
        setattr(console, _CONSOLE_HANDLER_MARKER, True)
    console.setFormatter(_processor_formatter(renderer))
    _install_safe_exception_filter(console)
    for handler in preserved:
        _install_safe_exception_filter(handler)
    root.handlers = [console, *preserved]
    root.setLevel(resolved_level)
    _reconcile_named_loggers()


def validate_release_identity(env: str) -> None:
    value, valid = resolve_release_sha()
    if valid:
        return
    deployment = _deployment_environment(env)
    if deployment == "production":
        raise ReleaseIdentityError(
            "production requires a valid LEMMA_RELEASE_SHA release identity"
        )

    reason = "missing" if value is None else "malformed"
    if reason in _release_warning_emitted:
        return
    _release_warning_emitted.add(reason)
    if reason == "missing":
        get_logger(__name__).warning(
            "release.identity.missing",
            deployment_environment=deployment,
        )
    else:
        get_logger(__name__).warning(
            "release.identity.malformed",
            deployment_environment=deployment,
        )


def get_logger(name: str) -> Logger:
    return structlog.get_logger().bind(logger=name)  # type: ignore[return-value]


def get_dependency_logger(name: str, *, level: int = logging.WARNING) -> logging.Logger:
    """Return a foreign-library logger routed through Lemma's safe root pipeline.

    Some libraries (notably FastStream) create their own stdout handler lazily
    when a broker starts. Supplying this logger prevents that late handler from
    being installed while retaining warning/error records as bounded
    ``dependency.reported`` events.
    """
    dependency_logger = logging.getLogger(name)
    dependency_logger.handlers = [
        handler
        for handler in dependency_logger.handlers
        if not _is_console_handler(handler)
    ]
    for handler in dependency_logger.handlers:
        _install_safe_exception_filter(handler)
    dependency_logger.propagate = True
    dependency_logger.setLevel(level)
    return dependency_logger


setup_logging()
