"""AgentBox-owned structured logging and request correlation.

This module deliberately has no dependency on ``lemma-backend`` so the manager
image remains independently installable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterator, Mapping
from contextlib import contextmanager
from contextvars import Context, ContextVar
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
import traceback
from typing import Any, TypeVar
from uuid import UUID

from agentbox.event_catalog import EVENT_CATALOG


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_request_id: ContextVar[str | None] = ContextVar("agentbox_request_id", default=None)
_correlation_id: ContextVar[UUID | None] = ContextVar(
    "agentbox_correlation_id", default=None
)
_event_id: ContextVar[UUID | None] = ContextVar("agentbox_event_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("agentbox_job_id", default=None)
_warning_emitted = False
_contract_violation_emitted = False
_HANDLER_MARKER = "_agentbox_json_console_handler"


class ReleaseIdentityError(RuntimeError):
    pass


class LoggingContractError(ValueError):
    pass


def _strict_logging_contract_enabled() -> bool:
    configured = os.getenv("LEMMA_LOGGING_CONTRACT_STRICT")
    if configured is None:
        configured = os.getenv("LOGGING_CONTRACT_STRICT")
    enabled = (configured or "").strip().lower() in {"1", "true", "yes", "on"}
    raw_environment = (
        (
            os.getenv("LEMMA_ENVIRONMENT")
            or os.getenv("AGENTBOX_ENVIRONMENT")
            or os.getenv("ENVIRONMENT")
            or "development"
        )
        .strip()
        .lower()
    )
    return enabled and raw_environment in {"local", "test", "testing"}


def _environment() -> str:
    raw = (
        os.getenv("LEMMA_ENVIRONMENT")
        or os.getenv("AGENTBOX_ENVIRONMENT")
        or os.getenv("ENVIRONMENT")
        or "development"
    )
    return "production" if raw.lower() in {"prod", "production"} else "development"


def _configured_release_sha() -> str:
    canonical = os.getenv("LEMMA_RELEASE_SHA")
    raw = canonical if canonical is not None else os.getenv("RELEASE_SHA")
    return (raw or "").strip()


def _release_sha() -> str:
    raw = _configured_release_sha()
    return raw if _SHA_RE.fullmatch(raw) else "unknown"


def validate_release_identity() -> None:
    global _warning_emitted
    raw = _configured_release_sha()
    if _SHA_RE.fullmatch(raw):
        return
    if _environment() == "production":
        raise ReleaseIdentityError(
            "production AgentBox requires a valid LEMMA_RELEASE_SHA"
        )
    if not _warning_emitted:
        _warning_emitted = True
        if raw:
            get_logger(__name__).warning("release.identity.malformed")
        else:
            get_logger(__name__).warning("release.identity.missing")


def current_context() -> dict[str, str]:
    values = {
        "request_id": _request_id.get(),
        "correlation_id": str(_correlation_id.get()) if _correlation_id.get() else None,
        "event_id": str(_event_id.get()) if _event_id.get() else None,
        "job_id": _job_id.get(),
    }
    return {key: value for key, value in values.items() if value is not None}


@contextmanager
def bind_context(
    *,
    request_id: str,
    correlation_id: UUID,
    event_id: UUID | None = None,
    job_id: str | None = None,
) -> Iterator[None]:
    tokens = (
        (_request_id, _request_id.set(request_id)),
        (_correlation_id, _correlation_id.set(correlation_id)),
        (_event_id, _event_id.set(event_id)),
        (_job_id, _job_id.set(job_id)),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


T = TypeVar("T")


def create_background_task(
    coroutine: Coroutine[Any, Any, T], *, name: str | None = None
) -> asyncio.Task[T]:
    return asyncio.create_task(coroutine, name=name, context=Context())


def create_inherited_task(
    coroutine: Coroutine[Any, Any, T], *, name: str | None = None
) -> asyncio.Task[T]:
    return asyncio.create_task(coroutine, name=name)


def _safe_module_name(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    marker = "/agentbox/agentbox/"
    if marker in normalized:
        relative = normalized.split(marker, 1)[1].rsplit(".", 1)[0]
        return "agentbox." + relative.replace("/", ".")
    return Path(filename).stem


def _safe_exception_fields(exc_info: object) -> dict[str, Any]:
    info = sys.exc_info() if exc_info is True else exc_info
    if not isinstance(info, tuple) or len(info) != 3:
        return {}
    exc_type, _exc, tb = info
    if not isinstance(exc_type, type):
        return {}
    extracted = traceback.extract_tb(tb) if tb is not None else []
    application = [
        frame
        for frame in extracted
        if "/agentbox/agentbox/" in frame.filename.replace("\\", "/")
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


class BoundLogger:
    def __init__(self, name: str, fields: Mapping[str, Any] | None = None) -> None:
        self._logger = logging.getLogger(name)
        self._fields = dict(fields or {})

    def bind(self, **fields: Any) -> "BoundLogger":
        return BoundLogger(self._logger.name, {**self._fields, **fields})

    def _log(self, level: int, event: str, *args: Any, **fields: Any) -> None:
        global _contract_violation_emitted
        exc_info = fields.pop("exc_info", None)
        level_name = logging.getLevelName(level).lower()
        specification = EVENT_CATALOG.get(event)
        violation: str | None = None
        if args:
            violation = "positional_arguments"
        elif not _EVENT_RE.fullmatch(event):
            violation = "invalid_event_name"
        elif specification is None:
            violation = "unregistered_event"
        elif specification.level != level_name:
            violation = "unexpected_severity"
        elif set(fields) - set(specification.fields):
            violation = "unexpected_fields"
        if violation is not None:
            if _strict_logging_contract_enabled():
                raise LoggingContractError(violation)
            if _contract_violation_emitted:
                return
            _contract_violation_emitted = True
            level = logging.ERROR
            event = "logging.contract.violation"
            fields = {"contract_violation": violation}
            exc_info = None
        if exc_info:
            fields.update(_safe_exception_fields(exc_info))
        self._logger.log(
            level,
            event,
            extra={"lemma_fields": {**self._fields, **fields}},
            exc_info=None,
        )

    def debug(self, event: str, *args: Any, **fields: Any) -> None:
        self._log(logging.DEBUG, event, *args, **fields)

    def info(self, event: str, *args: Any, **fields: Any) -> None:
        self._log(logging.INFO, event, *args, **fields)

    def warning(self, event: str, *args: Any, **fields: Any) -> None:
        self._log(logging.WARNING, event, *args, **fields)

    def error(self, event: str, *args: Any, **fields: Any) -> None:
        self._log(logging.ERROR, event, *args, **fields)

    def exception(self, event: str, *args: Any, **fields: Any) -> None:
        fields["exc_info"] = True
        self._log(logging.ERROR, event, *args, **fields)


def get_logger(name: str) -> BoundLogger:
    return BoundLogger(name)


class DependencyIncident:
    """Emit one degraded/recovered pair after three consecutive failures."""

    def __init__(self, dependency: str, *, logger: BoundLogger) -> None:
        self._dependency = dependency
        self._logger = logger
        self._failure_count = 0
        self._started_at: float | None = None
        self._degraded = False

    def record_failure(self, exc: BaseException) -> None:
        now = time.monotonic()
        if self._started_at is None:
            self._started_at = now
        self._failure_count += 1
        if self._degraded or self._failure_count < 3:
            return
        self._degraded = True
        self._logger.warning(
            "dependency.degraded",
            dependency=self._dependency,
            error_type=type(exc).__name__,
            failure_count=self._failure_count,
            incident_duration_ms=round((now - self._started_at) * 1000, 1),
        )

    def record_success(self) -> None:
        if self._started_at is None:
            return
        if self._degraded:
            self._logger.info(
                "dependency.recovered",
                dependency=self._dependency,
                failure_count=self._failure_count,
                incident_duration_ms=round(
                    (time.monotonic() - self._started_at) * 1000, 1
                ),
            )
        self._failure_count = 0
        self._started_at = None
        self._degraded = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        app_owned = isinstance(getattr(record, "lemma_fields", None), dict)
        event = str(record.msg) if app_owned else "dependency.reported"
        data: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "event": event
            if _EVENT_RE.fullmatch(event)
            else "logging.contract.violation",
            "logger": record.name,
            "service.name": "lemma-agentbox",
            "service.version": _release_sha(),
            "release.sha": _release_sha(),
            "deployment.environment": _environment(),
            **current_context(),
        }
        fields = getattr(record, "lemma_fields", {}) if app_owned else {}
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key.lower() in {
                    "authorization",
                    "body",
                    "cookie",
                    "error",
                    "headers",
                    "message",
                    "payload",
                    "prompt",
                    "request",
                    "response",
                    "traceback",
                    "url",
                }:
                    continue
                if isinstance(value, str):
                    data[key] = " ".join(value.splitlines())[:512]
                elif isinstance(value, bool | int | float) or value is None:
                    data[key] = value
                elif isinstance(value, UUID):
                    data[key] = str(value)
                elif key == "error_frames" and isinstance(value, list):
                    data[key] = value[:8]
        precomputed = getattr(record, "lemma_safe_exception", None)
        if isinstance(precomputed, dict):
            data.update(precomputed)
        elif record.exc_info:
            data.update(_safe_exception_fields(record.exc_info))
        if record.exc_info:
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return json.dumps(data, separators=(",", ":"), default=str)


class _SafeExceptionFilter(logging.Filter):
    """Strip exception messages and tracebacks before any handler exports them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            record.lemma_safe_exception = _safe_exception_fields(record.exc_info)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _install_safe_exception_filter(handler: logging.Handler) -> None:
    if not any(isinstance(item, _SafeExceptionFilter) for item in handler.filters):
        handler.addFilter(_SafeExceptionFilter())


def _is_console_handler(handler: logging.Handler) -> bool:
    return (
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        and getattr(handler, "stream", None) in {sys.stdout, sys.stderr}
    )


def _is_otel_handler(handler: logging.Handler) -> bool:
    identity = f"{handler.__class__.__module__}.{handler.__class__.__name__}".lower()
    return "opentelemetry" in identity or "otel" in identity


def setup_logging(*, level: str = "INFO") -> None:
    root = logging.getLogger()
    owned = [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]
    preserved = [
        h
        for h in root.handlers
        if not getattr(h, _HANDLER_MARKER, False)
        and (not _is_console_handler(h) or _is_otel_handler(h))
    ]
    handler = owned[0] if owned else logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(JsonFormatter())
    _install_safe_exception_filter(handler)
    for preserved_handler in preserved:
        _install_safe_exception_filter(preserved_handler)
    root.handlers = [handler, *preserved]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for name in (
        "azure",
        "e2b",
        "httpcore",
        "httpx",
        "kubernetes",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
    ):
        dependency = logging.getLogger(name)
        dependency.handlers = [
            existing
            for existing in dependency.handlers
            if not _is_console_handler(existing) or _is_otel_handler(existing)
        ]
        for existing in dependency.handlers:
            _install_safe_exception_filter(existing)
        dependency.propagate = True
        dependency.setLevel(logging.WARNING)
