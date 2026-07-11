import logging
import structlog
import sys
from typing import Any, Protocol

from opentelemetry import trace

from app.core.redaction import redact_event_dict

_logging_context: dict[str, Any] = {}


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


def _normalize_event(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    event = event_dict.get("event")
    if isinstance(event, str) and "message" not in event_dict:
        event_dict["message"] = event
    return event_dict


def _is_otel_handler(handler: logging.Handler) -> bool:
    module = handler.__class__.__module__
    return module.startswith("opentelemetry.")


def setup_logging(
    env: str = "production",
    *,
    service_name: str | None = None,
    json_logs: bool = True,
    log_level: str = "INFO",
) -> None:
    """
    Configure structured logging for the application.
    Call this once at application startup.
    
    Args:
        env: Environment name ('production', 'development', 'test')
    """
    _logging_context.clear()
    _logging_context.update(
        {
            "service.name": service_name,
            "deployment.environment": env,
        }
    )
    resolved_level = getattr(logging, log_level.upper(), logging.INFO)
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_trace_context,
        _add_static_context,
        _normalize_event,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_event_dict,
    ]

    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            resolved_level
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    
    # Configure standard library logging while preserving OTEL handlers that may
    # have been attached earlier in this process. The local app embeds the worker
    # in the API process, so setup_logging can run more than once after telemetry
    # is initialized.
    root_logger = logging.getLogger()
    preserved_handlers = [
        handler for handler in root_logger.handlers if _is_otel_handler(handler)
    ]
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.handlers = [stream_handler, *preserved_handlers]
    root_logger.setLevel(resolved_level)


def get_logger(name: str) -> Logger:
    """
    Get a logger instance for the given module.
    
    This wrapper allows us to:
    1. Provide proper type hints for IDE autocomplete
    2. Easily swap logging libraries in the future
    3. Add custom behavior if needed
    
    Args:
        name: Module name, typically __name__
        
    Returns:
        Logger instance with proper type hints
        
    Example:
        >>> from config.logging import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("user_created", user_id=123)
    """
    return structlog.get_logger().bind(logger=name)  # type: ignore[return-value]


# Optional: Add custom log levels or methods
class CustomLogger:
    """
    Optional: If you want to add custom logging methods,
    wrap the structlog logger in a class
    """
    
    def __init__(self, logger: Any):
        self._logger = logger
    
    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(event, **kwargs)
    
    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(event, **kwargs)
    
    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(event, **kwargs)
    
    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(event, **kwargs)
    
    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(event, **kwargs)
    
    def bind(self, **kwargs: Any) -> "CustomLogger":
        return CustomLogger(self._logger.bind(**kwargs))
    
    # Add custom methods
    def audit(self, event: str, **kwargs: Any) -> None:
        """Log audit events with special handling"""
        self._logger.info(event, log_type="audit", **kwargs)
    
    def security(self, event: str, **kwargs: Any) -> None:
        """Log security events with special handling"""
        self._logger.warning(event, log_type="security", **kwargs)


def get_custom_logger(name: str) -> CustomLogger:
    """Get a custom logger with additional methods"""
    return CustomLogger(structlog.get_logger().bind(logger=name))


# Configure a safe default immediately so module-level loggers created during
# imports already emit structured logs. Applications reconfigure this later
# with the right service metadata.
setup_logging()
