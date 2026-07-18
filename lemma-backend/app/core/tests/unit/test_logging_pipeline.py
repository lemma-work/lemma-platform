"""Unit coverage for the exact, bounded structured logging pipeline."""

from __future__ import annotations

import io
import json
import logging
import logging.config
from copy import deepcopy

import pytest
import structlog

import app.core.log.log as logmod
from app.core.log.log import (
    LoggingContractError,
    get_dependency_logger,
    get_logger,
    setup_logging,
)


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


def test_foreign_record_is_stable_and_drops_raw_message(captured_stdout) -> None:
    canary = "CANARY-FOREIGN-SECRET"
    logging.getLogger("some.foreign.lib").warning(
        "provider failed token=%s url=https://example.invalid/private", canary
    )
    record = captured_stdout()[0]
    assert record["event"] == "dependency.reported"
    assert record["logger"] == "some.foreign.lib"
    assert canary not in json.dumps(record)


def test_malformed_dependency_url_cannot_break_logging(captured_stdout) -> None:
    logging.getLogger("some.foreign.lib").warning(
        "dependency failed at https://example.com:bad/path?token=CANARY"
    )
    record = captured_stdout()[0]
    assert record["event"] == "dependency.reported"
    assert "CANARY" not in json.dumps(record)


def test_exception_contains_only_deterministic_safe_descriptor(captured_stdout) -> None:
    canary = "CANARY-EXCEPTION-SECRET"
    try:
        raise ValueError(f"{canary}\n/private/source/path.py")
    except ValueError:
        get_logger("app.demo").error("http.request.failed", exc_info=True)
    record = captured_stdout()[0]
    rendered = json.dumps(record)
    assert record["error_type"] == "ValueError"
    assert len(record["error_stack_hash"]) == 64
    assert len(record.get("error_frames", [])) <= 8
    assert canary not in rendered
    assert "/private/source/path.py" not in rendered
    assert "exception" not in record


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
        assert logging.getLogger("uvicorn.error").level == logging.WARNING
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
    assert records[0]["event"] == "dependency.reported"
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
    assert records[0]["event"] == "dependency.reported"
    assert records[0]["logger"] == "faststream.redis"
    assert "CANARY" not in json.dumps(records[0])
    assert dependency_logger.handlers == []


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
    assert records[0].exc_info is None
    safe = getattr(records[0], "lemma_safe_exception")
    assert safe["error_type"] == "RuntimeError"
    assert len(safe["error_stack_hash"]) == 64
    assert "CANARY" not in json.dumps(safe)


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
