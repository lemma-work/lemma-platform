"""Unit coverage for the exact, bounded structured logging pipeline."""

from __future__ import annotations

import io
import json
import logging
import logging.config
from copy import deepcopy
from uuid import UUID

import pytest
import structlog

import app.core.log.log as logmod
from app.core.log.log import (
    LoggingContractError,
    get_dependency_logger,
    get_logger,
    setup_logging,
)
from app.core.request_context import bind_request_context


def _processor_formatter_handler() -> logging.Handler:
    return next(
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter)
    )


@pytest.fixture
def captured_stdout():
    setup_logging(
        "development",
        service_name="lemma-test",
        json_logs=True,
        log_level="DEBUG",
    )
    buffer = io.StringIO()
    handler = _processor_formatter_handler()
    original_stream = handler.stream
    handler.stream = buffer

    def records() -> list[dict]:
        return [json.loads(line) for line in buffer.getvalue().splitlines() if line]

    yield records
    handler.stream = original_stream


def test_application_record_has_one_line_and_required_schema(captured_stdout) -> None:
    get_logger("app.demo").info("service.started")
    records = captured_stdout()
    assert len(records) == 1
    assert records[0]["event"] == "service.started"
    assert records[0]["level"] == "info"
    assert records[0]["logger"] == "app.demo"
    assert records[0]["service.name"] == "lemma-test"
    assert records[0]["deployment.environment"] == "development"
    assert {"timestamp", "service.version", "release.sha"} <= records[0].keys()
    assert "message" not in records[0]


def test_foreign_record_keeps_redacted_bounded_message(captured_stdout) -> None:
    canary = "CANARY-FOREIGN-SECRET"
    logging.getLogger("some.foreign.lib").warning(
        "provider failed token=%s url=https://example.invalid/private", canary
    )
    record = captured_stdout()[0]
    assert record["event"] == (
        "provider failed token=[REDACTED] url=https://example.invalid/private"
    )
    assert record["logger"] == "some.foreign.lib"
    assert record["level"] == "warning"
    assert canary not in json.dumps(record)


def test_malformed_dependency_url_cannot_break_logging(captured_stdout) -> None:
    logging.getLogger("some.foreign.lib").warning(
        "dependency failed at https://example.com:bad/path?token=CANARY"
    )
    record = captured_stdout()[0]
    assert record["event"] == "dependency failed at [REDACTED_URL]"
    assert "CANARY" not in json.dumps(record)


def test_an_exception_is_logged_with_everything_needed_to_diagnose_it(
    captured_stdout,
) -> None:
    """An error you cannot diagnose is not protected, it is lost.

    This used to assert the opposite — that the exception's message and
    traceback were stripped — which left "ValueError somewhere in this module"
    and nothing about *which* value. The message and the full traceback are the
    diagnosis, so they are emitted.
    """
    detail = "row 41 has no tenant_id"
    try:
        raise ValueError(detail)
    except ValueError:
        get_logger("app.demo").error("http.request.failed", exc_info=True)

    record = captured_stdout()[0]
    assert record["error_type"] == "ValueError"
    assert len(record["error_stack_hash"]) == 64
    assert len(record.get("error_frames", [])) <= 8
    assert record["error_message"] == detail
    # A real traceback, with its frames and newlines intact.
    assert record["error_traceback"].startswith("Traceback (most recent call last):")
    assert "ValueError: row 41 has no tenant_id" in record["error_traceback"]
    assert "\n" in record["error_traceback"], "a one-line traceback is useless"
    assert "exception" not in record


def test_credential_named_fields_are_still_dropped(captured_stdout) -> None:
    """Widening diagnostics must not turn into logging the secrets themselves."""
    get_logger("app.demo").error(
        "http.request.failed",
        authorization="Bearer sk-should-not-appear",
        cookie="session=should-not-appear",
        password="hunter2",
        secret="should-not-appear",
    )

    rendered = json.dumps(captured_stdout()[0])
    assert "should-not-appear" not in rendered
    assert "hunter2" not in rendered


def test_unregistered_event_and_field_fail_in_explicit_local_strict_mode(
    captured_stdout, monkeypatch
) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "local")
    monkeypatch.setenv("LEMMA_LOGGING_CONTRACT_STRICT", "true")
    logger = get_logger("app.demo")
    with pytest.raises(LoggingContractError, match="unregistered_event"):
        logger.info("uncatalogued.event")
    with pytest.raises(LoggingContractError, match="unexpected_fields"):
        logger.info("service.started", payload="must never render")
    assert captured_stdout() == []


def test_production_emits_one_bounded_contract_violation(monkeypatch) -> None:
    monkeypatch.setattr(logmod, "_contract_violation_emitted", False)
    setup_logging("production", service_name="lemma-test", json_logs=True)
    buffer = io.StringIO()
    handler = _processor_formatter_handler()
    original_stream = handler.stream
    handler.stream = buffer
    try:
        logger = get_logger("app.demo")
        logger.info("not.registered", payload="CANARY")
        logger.info("also.not.registered", payload="SECOND-CANARY")
    finally:
        handler.stream = original_stream
    records = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assert len(records) == 1
    assert records[0]["event"] == "logging.contract.violation"
    assert records[0]["contract_violation"] == "unregistered_event"
    assert "CANARY" not in json.dumps(records[0])


def test_deployed_development_contract_violation_is_fail_safe(monkeypatch) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "development")
    monkeypatch.setenv("LEMMA_LOGGING_CONTRACT_STRICT", "true")
    monkeypatch.setattr(logmod, "_contract_violation_emitted", False)
    setup_logging("development", service_name="lemma-test", json_logs=True)
    buffer = io.StringIO()
    handler = _processor_formatter_handler()
    original_stream = handler.stream
    handler.stream = buffer
    try:
        get_logger("app.demo").info("CANARY invalid event", payload="secret")
    finally:
        handler.stream = original_stream
    records = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assert len(records) == 1
    assert records[0]["event"] == "logging.contract.violation"
    assert "CANARY" not in json.dumps(records[0])


def test_setup_reconciles_one_console_and_preserves_non_console_handler(
    captured_stdout,
) -> None:
    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            del record

    preserved = CaptureHandler()
    logging.getLogger().addHandler(preserved)
    try:
        setup_logging("development", service_name="lemma-a", json_logs=True)
        setup_logging("development", service_name="lemma-b", json_logs=True)
        processor_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter)
        ]
        assert len(processor_handlers) == 1
        assert preserved in logging.getLogger().handlers
        assert logging.getLogger("uvicorn.access").handlers == []
        assert (
            logging.getLogger("uvicorn.access").getEffectiveLevel()
            == logging.WARNING
        )
        assert logging.getLogger("uvicorn.error").getEffectiveLevel() == logging.INFO
    finally:
        logging.getLogger().removeHandler(preserved)


def test_reconciliation_after_real_uvicorn_and_streaq_console_configuration() -> None:
    from uvicorn.config import LOGGING_CONFIG

    logging.config.dictConfig(deepcopy(LOGGING_CONFIG))
    streaq_logger = logging.getLogger("streaq.worker")
    streaq_logger.addHandler(logging.StreamHandler())

    setup_logging(
        "development",
        service_name="lemma-worker",
        json_logs=True,
        log_level="DEBUG",
    )
    buffer = io.StringIO()
    handler = _processor_formatter_handler()
    original_stream = handler.stream
    handler.stream = buffer
    try:
        logging.getLogger("streaq.worker").warning("task failed secret=CANARY")
    finally:
        handler.stream = original_stream

    records = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assert len(records) == 1
    assert records[0]["event"] == "task failed secret=[REDACTED]"
    assert "CANARY" not in json.dumps(records[0])
    assert logging.getLogger("uvicorn.access").handlers == []
    assert logging.getLogger("uvicorn.error").handlers == []
    assert logging.getLogger("streaq.worker").handlers == []


def test_lazy_dependency_logger_cannot_install_its_own_console_handler(
    captured_stdout,
) -> None:
    dependency_logger = logging.getLogger("faststream.redis")
    dependency_logger.addHandler(logging.StreamHandler())

    dependency_logger = get_dependency_logger("faststream.redis")
    dependency_logger.warning("consumer failed secret=CANARY")

    records = captured_stdout()
    assert len(records) == 1
    assert records[0]["event"] == "consumer failed secret=[REDACTED]"
    assert records[0]["logger"] == "faststream.redis"
    assert "CANARY" not in json.dumps(records[0])
    assert dependency_logger.handlers == []


def test_production_uses_log_level_and_preserves_every_allowed_dependency_record(
    captured_stdout,
) -> None:
    setup_logging(
        "production",
        service_name="lemma-api",
        json_logs=True,
        log_level="INFO",
    )
    dependency_logger = get_dependency_logger("httpx")

    dependency_logger.debug("request assembly detail")
    dependency_logger.info("routine request completed")
    dependency_logger.warning("retrying request attempt=%s", 1)
    dependency_logger.warning("retrying request attempt=%s", 2)
    dependency_logger.error("request failed attempt=%s", 3)
    dependency_logger.error("request failed attempt=%s", 4)

    records = captured_stdout()
    assert [record["level"] for record in records] == [
        "warning",
        "warning",
        "error",
        "error",
    ]
    assert [record["event"] for record in records] == [
        "retrying request attempt=1",
        "retrying request attempt=2",
        "request failed attempt=3",
        "request failed attempt=4",
    ]


def test_development_debug_level_preserves_dependency_debug_for_diagnosis(
    captured_stdout,
) -> None:
    dependency_logger = get_dependency_logger("httpx")

    dependency_logger.debug("request assembly detail")
    dependency_logger.info("routine request completed")

    records = captured_stdout()
    assert [record["event"] for record in records] == [
        "request assembly detail",
        "routine request completed",
    ]


@pytest.mark.parametrize(
    "logger_name",
    [
        "com.supertokens",
        "fastmcp.server.server",
        "mcp.server.lowlevel.server",
        "openai._base_client",
        "filelock",
        "coredis",
        "asyncio",
    ],
)
def test_known_dependency_families_follow_configured_production_level(
    captured_stdout,
    logger_name,
) -> None:
    setup_logging(
        "production",
        service_name="lemma-api",
        json_logs=True,
        log_level="INFO",
    )
    dependency_logger = logging.getLogger(logger_name)

    dependency_logger.debug("routine client detail")
    dependency_logger.info("useful client lifecycle")

    records = captured_stdout()
    assert len(records) == 1
    assert records[0]["logger"] == logger_name
    assert records[0]["level"] == "info"
    assert records[0]["event"] == "useful client lifecycle"


def test_dependency_console_handlers_are_reconciled_to_safe_pipeline(
    captured_stdout,
) -> None:
    supertokens_logger = logging.getLogger("com.supertokens")
    supertokens_console = logging.StreamHandler()
    supertokens_logger.addHandler(supertokens_console)
    supertokens_logger.propagate = False
    fastmcp_logger = logging.getLogger("fastmcp")
    fastmcp_console = logging.StreamHandler()
    fastmcp_logger.addHandler(fastmcp_console)
    fastmcp_logger.propagate = False

    setup_logging(
        "production",
        service_name="lemma-api",
        json_logs=True,
        log_level="INFO",
    )

    assert supertokens_console not in supertokens_logger.handlers
    assert supertokens_logger.propagate is True
    assert fastmcp_console not in fastmcp_logger.handlers
    assert fastmcp_logger.propagate is True


def test_repeated_faststream_errors_are_not_hidden_and_keep_correlation(
    captured_stdout,
) -> None:
    setup_logging(
        "production",
        service_name="lemma-worker",
        json_logs=True,
        log_level="INFO",
    )
    dependency_logger = get_dependency_logger("faststream.redis")
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")

    with bind_request_context(
        request_id="request-123",
        correlation_id=correlation_id,
    ):
        for _ in range(3):
            dependency_logger.error("consumer failed credential=CANARY")

    records = captured_stdout()
    assert len(records) == 3
    assert all(record["logger"] == "faststream.redis" for record in records)
    assert all(record["level"] == "error" for record in records)
    assert all(
        record["event"] == "consumer failed credential=[REDACTED]" for record in records
    )
    assert all(record["request_id"] == "request-123" for record in records)
    assert all(record["correlation_id"] == str(correlation_id) for record in records)


def test_level_survives_a_foreign_handler_rewriting_levelname(
    captured_stdout,
) -> None:
    """A handler that formats a record before ours must not corrupt `level`.

    FastStream's colourising formatter rewrites `record.levelname` in place with
    an ANSI-wrapped, padded copy, and anything reading the *name* afterwards
    inherits the escape codes. `level` therefore comes from `levelno`. Without
    that, this record lands with a level of "\\u001b[31merror\\u001b[0m   " and
    every consumer filtering by level silently stops matching it.

    This is also what made the repeated-errors test above order-dependent: the
    mutating handler only survives on the logger when an earlier test leaves one
    attached, so the corruption appeared and vanished with collection order.
    """
    setup_logging(
        "production",
        service_name="lemma-worker",
        json_logs=True,
        log_level="INFO",
    )
    coloured_error = "\x1b[31mERROR\x1b[0m   "

    class _RewritesLevelName(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            record.levelname = coloured_error

    dependency_logger = get_dependency_logger("faststream.redis")
    mutating_handler = _RewritesLevelName()
    dependency_logger.addHandler(mutating_handler)
    try:
        dependency_logger.error("consumer failed")
    finally:
        dependency_logger.removeHandler(mutating_handler)

    records = captured_stdout()
    assert len(records) == 1
    assert records[0]["level"] == "error"


def test_uvicorn_access_records_follow_configured_debug_level(
    captured_stdout,
) -> None:
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.debug("request routing detail")
    access_logger.info('127.0.0.1 - "GET /health/ready HTTP/1.1" 200')

    records = captured_stdout()
    assert [record["level"] for record in records] == ["debug", "info"]
    assert all(record["logger"] == "uvicorn.access" for record in records)
    assert [record["event"] for record in records] == [
        "request routing detail",
        '127.0.0.1 - "GET /health/ready HTTP/1.1" 200',
    ]


def test_preserved_handlers_never_receive_raw_exception_data() -> None:
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = CaptureHandler()
    logging.getLogger().addHandler(capture)
    setup_logging("development", service_name="lemma-api", json_logs=True)
    try:
        try:
            raise RuntimeError("CANARY-RAW-EXCEPTION /private/source.py")
        except RuntimeError:
            logging.getLogger("foreign.dependency").exception("dependency failed")
    finally:
        logging.getLogger().removeHandler(capture)

    assert len(records) == 1
    # The point of the filter: a third-party handler never gets a live exception
    # object (with its frames and locals) to export however it likes. What it
    # gets is the structured descriptor we built.
    assert records[0].exc_info is None
    assert records[0].exc_text is None
    safe = getattr(records[0], "lemma_safe_exception")
    assert safe["error_type"] == "RuntimeError"
    assert len(safe["error_stack_hash"]) == 64
    # And that descriptor carries the diagnosis, for foreign loggers too — a
    # dependency's failure is usually the one you most need to read.
    assert "CANARY-RAW-EXCEPTION" in safe["error_message"]
    assert "RuntimeError" in safe["error_traceback"]


def test_release_and_trace_identity_are_top_level(monkeypatch, captured_stdout) -> None:
    from app.core.config import settings
    from opentelemetry.trace import SpanContext, TraceFlags

    sha = "a" * 40
    monkeypatch.setattr(settings, "release_sha", sha)

    class FakeSpan:
        def get_span_context(self):
            return SpanContext(
                trace_id=1,
                span_id=2,
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )

    monkeypatch.setattr(logmod.trace, "get_current_span", lambda: FakeSpan())
    setup_logging("development", service_name="lemma-api", json_logs=True)
    get_logger("app.demo").info("service.started")
    record = captured_stdout()[-1]
    assert record["service.version"] == sha
    assert record["release.sha"] == sha
    assert record["trace_id"] == format(1, "032x")
    assert record["span_id"] == format(2, "016x")


@pytest.mark.parametrize(
    "logger_name",
    (
        "sqlalchemy.engine.Engine",
        "sqlalchemy.orm.mapper.Mapper",
        "sqlalchemy.orm.properties.ColumnProperty",
        "sqlalchemy.orm.relationships.RelationshipProperty",
        "sqlalchemy.orm.strategies.LazyLoader",
    ),
)
def test_sqlalchemy_info_noise_is_suppressed_at_info(
    captured_stdout,
    logger_name,
) -> None:
    setup_logging(
        "development",
        service_name="lemma-api",
        json_logs=True,
        log_level="INFO",
    )
    sqlalchemy_logger = logging.getLogger(logger_name)

    sqlalchemy_logger.info("routine SQLAlchemy detail token=CANARY")
    sqlalchemy_logger.warning("SQLAlchemy warning token=CANARY")
    get_logger("app.demo").info("service.started")

    records = captured_stdout()
    assert [record["event"] for record in records] == [
        "SQLAlchemy warning token=[REDACTED]",
        "service.started",
    ]
    assert "CANARY" not in json.dumps(records)


@pytest.mark.parametrize(
    "logger_name",
    (
        "sqlalchemy.engine.Engine",
        "sqlalchemy.orm.mapper.Mapper",
        "sqlalchemy.orm.properties.ColumnProperty",
        "sqlalchemy.orm.relationships.RelationshipProperty",
        "sqlalchemy.orm.strategies.LazyLoader",
    ),
)
def test_sqlalchemy_debug_remains_available_when_requested(
    captured_stdout,
    logger_name,
) -> None:
    setup_logging(
        "development",
        service_name="lemma-api",
        json_logs=True,
        log_level="DEBUG",
    )
    logging.getLogger(logger_name).debug("SQLAlchemy debug detail")

    assert captured_stdout()[-1]["event"] == "SQLAlchemy debug detail"


@pytest.mark.parametrize(
    "logger_name",
    (
        "com.supertokens",
        "mcp.server.lowlevel.server",
        "filelock",
        "apscheduler.scheduler",
        "urllib3.connectionpool",
    ),
)
def test_debug_only_noise_is_suppressed_when_quiet_dependencies_requested(
    captured_stdout,
    monkeypatch,
    logger_name,
) -> None:
    monkeypatch.setenv("LOG_QUIET_DEPENDENCIES", "1")
    setup_logging(
        "development",
        service_name="lemma-api",
        json_logs=True,
        log_level="DEBUG",
    )
    dependency_logger = logging.getLogger(logger_name)

    dependency_logger.debug("routine protocol chatter")
    dependency_logger.info("routine protocol chatter")
    dependency_logger.warning("dependency warning")
    get_logger("app.demo").info("service.started")

    records = captured_stdout()
    assert [record["event"] for record in records] == [
        "dependency warning",
        "service.started",
    ]


@pytest.mark.parametrize(
    "logger_name",
    (
        "com.supertokens",
        "mcp.server.lowlevel.server",
        "filelock",
        "apscheduler.scheduler",
        "urllib3.connectionpool",
    ),
)
def test_debug_only_noise_remains_available_by_default_at_debug(
    captured_stdout,
    logger_name,
) -> None:
    setup_logging(
        "development",
        service_name="lemma-api",
        json_logs=True,
        log_level="DEBUG",
    )
    logging.getLogger(logger_name).debug("routine protocol chatter")

    assert captured_stdout()[-1]["event"] == "routine protocol chatter"


def test_a_diagnostic_stack_field_is_not_swallowed_by_structlog(
    captured_stdout,
) -> None:
    """The runtime detectors' stacks must survive to the log line.

    ``stack`` is a reserved key: structlog's renderers pop it and handle it
    themselves, so a field passed as ``stack=`` vanishes without any error --
    and the event catalog happily listed ``stack`` as an expected field, so
    nothing anywhere complained. Both runtime detectors shipped that way: they
    reported *that* the loop stalled or a connection was held, and silently
    dropped the one thing that says *where*, which is the entire reason they
    capture a stack.

    This asserts the property (a stack-bearing diagnostic field arrives intact)
    rather than the current spelling, so renaming the field again is fine and
    reintroducing a reserved name is not.
    """
    get_logger("app.demo").warning(
        "runtime.loop_stall.degraded",
        service="lemma-test",
        stalled_ms=1049.6,
        threshold_ms=1000.0,
        stack_frames="app/foo.py:12 in slow_thing\napp/bar.py:44 in caller",
    )
    records = captured_stdout()
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "runtime.loop_stall.degraded"
    assert "slow_thing" in record.get("stack_frames", ""), (
        "the blocking call's stack did not survive the logging pipeline; a "
        f"stall report without it names no culprit. Got: {sorted(record)}"
    )
