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
_configured_log_level = logging.INFO

_CONTRACT_METADATA_FIELDS = {
    "causation_id",
    "consumer",
    "correlation_id",
    "deployment.environment",
    "dropped_field_count",
    "dropped_fields",
    "error_frames",
    "error_message",
    "error_stack_hash",
    "error_traceback",
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

_FOREIGN_LOGGER_PREFIXES = frozenset(
    {
        "asyncio",
        "azure",
        "com.supertokens",
        "coredis",
        "e2b",
        "fastmcp",
        "faststream",
        "filelock",
        "httpcore",
        "httpx",
        "mcp",
        "openai",
        "sqlalchemy",
        "streaq",
        "urllib3",
        "uvicorn",
    }
)
_INFO_NOISE_LOGGER_PREFIXES = (
    # These libraries narrate every successful request/message at INFO. Their
    # warnings and errors remain visible; timing belongs in OTel spans instead
    # of duplicate console records.
    "faststream",
    "httpcore",
    "httpx",
    "sqlalchemy.engine",
    # SQLAlchemy's mapper configuration narrates every relationship, column
    # property, and loader strategy through class-specific descendants such as
    # ``sqlalchemy.orm.relationships.RelationshipProperty`` and
    # ``sqlalchemy.orm.strategies.LazyLoader``. Keep the ORM at its documented
    # WARNING default during normal INFO operation; warnings/errors still
    # propagate through the redacted JSON pipeline.
    "sqlalchemy.orm",
    "sqlalchemy.pool",
    "streaq",
    "uvicorn.access",
)
_DEBUG_ONLY_NOISE_LOGGER_PREFIXES = (
    # Unlike _INFO_NOISE_LOGGER_PREFIXES, these are never floored at INFO —
    # their INFO-level output (login outcomes, scheduler faults) is
    # legitimate in production. Only their sub-INFO chatter is pure protocol
    # narration with no diagnostic value: SuperTokens logs internal
    # getSession/middleware plumbing on every single request, the MCP SDK
    # logs handler registration on every connection, filelock logs every
    # acquire/release pair.
    "com.supertokens",
    "filelock",
    "mcp",
    "urllib3",
)


def _quiet_dependencies_enabled() -> bool:
    return os.getenv("LOG_QUIET_DEPENDENCIES", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _dependency_floor_applies(configured_level: int, name: str) -> bool:
    """Whether a chatty dependency is held at WARNING.

    At INFO, always for ``_INFO_NOISE_LOGGER_PREFIXES``: these libraries
    narrate every successful operation and the console is for the
    application's own story.

    At DEBUG, only when ``LOG_QUIET_DEPENDENCIES`` is set, for both
    ``_INFO_NOISE_LOGGER_PREFIXES`` and ``_DEBUG_ONLY_NOISE_LOGGER_PREFIXES``.
    Asking for DEBUG deliberately means "show me everything", and a developer
    debugging SQLAlchemy (or SuperTokens, or the scheduler) itself must still
    be able to get it - so the default stays as it was. `make dev` opts in,
    because SQLAlchemy alone emits a record per mapped column at import:
    thousands of lines before the first request, which buries the
    application logs the flag was turned on to read.
    """
    if name.startswith(_INFO_NOISE_LOGGER_PREFIXES):
        if configured_level == logging.INFO:
            return True
        if configured_level < logging.INFO:
            return _quiet_dependencies_enabled()
        return False
    if name.startswith(_DEBUG_ONLY_NOISE_LOGGER_PREFIXES):
        return configured_level < logging.INFO and _quiet_dependencies_enabled()
    return False


# Emitted whole rather than flattened and truncated to 512 characters — a
# one-line traceback is not a traceback. The caps are generous; they exist only
# so a pathological recursion cannot produce a megabyte log line.
_UNTRUNCATED_FIELDS = {"error_traceback", "error_message", "stack_frames"}
_MAX_ERROR_MESSAGE_CHARS = 4_000
_MAX_ERROR_TRACEBACK_CHARS = 20_000
# ``stack_frames`` joins them: the runtime detectors' captured stacks are
# tracebacks by another name, and were being flattened and cut at 512
# characters -- which kept the outermost scaffolding frames and discarded the
# innermost ones, the only part that names what blocked. It carries no cap here
# because the bound belongs at the producer: `format_stall_stack` and
# `format_hold_stack` clip from the front, and only they know that the tail is
# the end worth keeping.

# Only genuine credentials. This list used to also hide `body`, `message`,
# `response`, `traceback`, `sql` and `url` — which meant that when something
# broke, the log recorded *that* it broke and discarded every fact needed to
# work out why. An error nobody can diagnose is not protected, it is lost.
#
# Dropping whole fields was always the blunt instrument anyway. Secrets are
# caught by `redact_event_dict`, which runs over every value and matches on the
# *pattern*: a traceback containing `Bearer sk-live-…` still reaches the log with
# its frames, file names and error text intact and the key replaced by
# [REDACTED]. That is strictly better than losing the traceback.
#
# The invariant that matters is at the other boundary: full detail in logs,
# never in an HTTP response or an SSE error frame. See
# `app/core/tests/unit/test_error_disclosure_boundary.py`, which pins both.
_PROHIBITED_FIELDS = {
    "authorization",
    "cookie",
    "password",
    "secret",
}


_MAX_FIELD_CHARS = 512
_FULL_DETAIL_LEVELS = {"error", "critical", "exception"}


def _is_full_detail(event_dict: dict[str, Any]) -> bool:
    """Whether this record gets everything, uncut.

    An error is the most valuable thing the system emits, and it is emitted
    once, after the state that produced it is gone. Bounding it trades the
    diagnosis for log volume that nobody was struggling to afford: errors are
    rare by construction, and a service emitting enough of them to matter has a
    bigger problem than its log bill.

    Warnings and below stay bounded. Those are the high-cardinality records --
    one per request, per job, per tick -- and they are where an unbounded field
    genuinely does become a megabyte of console per minute.
    """
    return str(event_dict.get("level", "")).lower() in _FULL_DETAIL_LEVELS


def _bounded(value: str) -> str:
    """Flatten and cap a string, and *say so* when something was removed.

    Silent truncation is its own bug: a cut value is indistinguishable from a
    complete one, so a half-printed URL or a stack ending mid-frame reads as
    the whole truth and sends the reader looking in the wrong place.
    """
    flattened = " ".join(value.splitlines())
    if len(flattened) <= _MAX_FIELD_CHARS:
        return flattened
    removed = len(flattened) - _MAX_FIELD_CHARS
    return f"{flattened[:_MAX_FIELD_CHARS]}…[+{removed} chars truncated]"


def _renderable(value: object) -> str:
    """A faithful string for a value the JSON renderer cannot take as-is.

    Used only on full-detail records, where dropping the field outright is the
    worse answer: the shape of a payload is often the whole diagnosis.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        import json as _json

        return _json.dumps(value, default=repr, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


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
    for marker in ("/lemma-backend/app/", "/lemma-backend/sandbox_runtime/"):
        if marker in normalized:
            relative = normalized.split(marker, 1)[1].rsplit(".", 1)[0]
            prefix = "app" if marker.endswith("/app/") else "sandbox_runtime"
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

    exc_type, exc, tb = info
    event_dict.update(_safe_exception_fields(exc_type, tb, exc))
    return event_dict


def _safe_exception_fields(
    exc_type: type[BaseException],
    tb: Any,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """Everything known about a failure, in a form someone can act on.

    This used to emit only the exception's *type* plus module/function/line
    frames — no message, no traceback. That is enough to count failures and
    almost never enough to fix one: "ValueError in service.py:412" does not say
    which value. The message and the formatted traceback are the diagnosis, so
    they are included.
    """
    extracted = traceback.extract_tb(tb) if tb is not None else []
    application = [
        frame
        for frame in extracted
        if "/lemma-backend/app/" in frame.filename.replace("\\", "/")
        or "/lemma-backend/sandbox_runtime/" in frame.filename.replace("\\", "/")
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
    if exc is not None:
        message = str(exc).strip()
        if message:
            fields["error_message"] = message[:_MAX_ERROR_MESSAGE_CHARS]
        if tb is not None:
            fields["error_traceback"] = "".join(
                traceback.format_exception(exc_type, exc, tb)
            )[-_MAX_ERROR_TRACEBACK_CHARS:]
    return fields


class _SafeExceptionFilter(logging.Filter):
    """Replace raw LogRecord exception data before any handler can export it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            exc_type, exc, tb = record.exc_info
            if isinstance(exc_type, type):
                record.lemma_safe_exception = _safe_exception_fields(exc_type, tb, exc)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _install_safe_exception_filter(handler: logging.Handler) -> None:
    if not any(isinstance(item, _SafeExceptionFilter) for item in handler.filters):
        handler.addFilter(_SafeExceptionFilter())


def _bound_fields(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Bound, render or drop each field, according to the record's level.

    Split out from the contract check so it can be tested on its own: the two
    answer different questions -- "is this event allowed to exist" versus "how
    much of it survives to the log" -- and only the second one is what a reader
    browsing pod logs actually feels.
    """
    # An error record keeps everything. Below error, fields stay bounded and a
    # value the renderer cannot take is dropped -- those records are emitted per
    # request and per tick, and that is where unbounded output actually hurts.
    full_detail = _is_full_detail(event_dict)
    dropped: list[str] = []
    for key in list(event_dict):
        if key.startswith("_"):
            continue
        value = event_dict[key]
        lowered = key.lower()
        if lowered in _PROHIBITED_FIELDS:
            # The one thing still withheld from an error, because a credential
            # in a log is an incident rather than a diagnosis. Everything else
            # about the error survives, and `redact_event_dict` has already
            # replaced secrets *inside* values by pattern, so the traceback that
            # carried one still arrives with its frames intact.
            event_dict.pop(key, None)
            dropped.append(key)
            continue
        if lowered in _UNTRUNCATED_FIELDS:
            # A traceback flattened onto one line and cut at 512 characters is
            # not a traceback. These carry the actual diagnosis, so they are
            # emitted whole, newlines and all.
            continue
        if isinstance(value, str):
            event_dict[key] = value if full_detail else _bounded(value)
        elif isinstance(value, UUID):
            event_dict[key] = str(value)
        elif isinstance(value, Enum) and isinstance(value.value, str | int):
            event_dict[key] = value.value
        elif key == "error_frames" and isinstance(value, list):
            continue
        elif isinstance(value, bytes | dict | list | tuple | set) or (
            not isinstance(value, bool | int | float) and value is not None
        ):
            if full_detail:
                event_dict[key] = _renderable(value)
            else:
                event_dict.pop(key, None)
                dropped.append(key)
    if dropped:
        event_dict["dropped_field_count"] = len(dropped)
        # Names, never values. A bare count tells you something was lost and
        # leaves you guessing which call site to go read; the names are
        # code-authored identifiers, so they carry no payload.
        event_dict["dropped_fields"] = ",".join(sorted(dropped))
    return event_dict


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
        # Lemma. Keep the original message as the log event after centralized
        # redaction, while dropping uncontrolled auxiliary fields. This keeps
        # third-party diagnostics useful without allowing their payloads to
        # bypass the safe logging boundary.
        allowed = _CONTRACT_METADATA_FIELDS
        for key in list(event_dict):
            if key not in allowed and not key.startswith("_"):
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

    return _bound_fields(event_dict)


def _is_otel_handler(handler: logging.Handler) -> bool:
    return handler.__class__.__module__.startswith("opentelemetry.")


def _is_console_handler(handler: logging.Handler) -> bool:
    if getattr(handler, _CONSOLE_HANDLER_MARKER, False):
        return True
    if handler.__class__.__module__ == "rich.logging":
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


def _add_log_level(logger: Any, method_name: str, event_dict: Any) -> Any:
    """Set ``level`` from the record's numeric level, not from its name.

    ``structlog.stdlib.add_log_level`` takes the level from ``method_name``,
    which for a foreign record is ``record.levelname.lower()``. That name is not
    ours to trust: a handler that formats the record before we do can rewrite it
    in place, and some do -- FastStream's colourising formatter replaces it with
    an ANSI-wrapped, padded copy, so ``level`` would arrive in our JSON as
    ``"\\u001b[31merror\\u001b[0m   "`` instead of ``"error"``. Anything
    consuming these logs by level (alerting, dashboards, log-level filters)
    silently stops matching.

    ``levelno`` is the field no formatter reformats, so derive the name from it
    and leave the rest of the chain alone.
    """
    record = event_dict.get("_record") if isinstance(event_dict, dict) else None
    if record is not None:
        name = logging.getLevelName(record.levelno)
        # Unregistered numeric levels stringify as "Level 25"; those have no
        # canonical name, so fall back rather than emit the placeholder.
        if isinstance(name, str) and not name.startswith("Level "):
            event_dict["level"] = name.lower()
            return event_dict
    return structlog.stdlib.add_log_level(logger, method_name, event_dict)


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        _add_log_level,
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


def _reconcile_named_loggers(configured_level: int) -> None:
    manager = logging.root.manager.loggerDict
    prefixes = tuple(_FOREIGN_LOGGER_PREFIXES)
    names = set(prefixes)
    names.update(_INFO_NOISE_LOGGER_PREFIXES)
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
        logger_level = configured_level
        if _dependency_floor_applies(configured_level, name):
            logger_level = logging.WARNING
        logger.setLevel(logger_level)


def setup_logging(
    env: str | None = None,
    *,
    service_name: str | None = None,
    json_logs: bool = True,
    log_level: str = "INFO",
) -> None:
    """Install or reconcile the one application JSON console pipeline."""
    global _configured_log_level
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
    _configured_log_level = resolved_level
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
    _reconcile_named_loggers(resolved_level)


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


def get_dependency_logger(name: str, *, level: int | None = None) -> logging.Logger:
    """Return a foreign-library logger routed through Lemma's safe root pipeline.

    Some libraries (notably FastStream) create their own stdout handler lazily
    when a broker starts. Supplying this logger prevents that late handler from
    being installed while retaining configured records with their original
    bounded, redacted messages.
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
    requested_level = _configured_log_level if level is None else level
    if level is None and _dependency_floor_applies(_configured_log_level, name):
        requested_level = max(requested_level, logging.WARNING)
    dependency_logger.setLevel(
        max(
            _configured_log_level,
            requested_level,
        )
    )
    return dependency_logger


setup_logging()
