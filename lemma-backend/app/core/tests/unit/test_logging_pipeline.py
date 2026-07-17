"""Unit tests for the unified structured logging pipeline.

Covers the Phase-1 logging contract: every application-owned and foreign
stdlib record is exactly one JSON object on one physical line, with required
static fields, escaped exception newlines, redaction of secrets in both paths,
idempotent ``setup_logging``, and active trace context injected exactly once.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog

import app.core.log.log as logmod
from app.core.log.log import get_logger, setup_logging


def _processor_formatter_handler() -> logging.Handler:
    for handler in logging.getLogger().handlers:
        if isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter):
            return handler
    raise AssertionError("ProcessorFormatter handler not installed")


@pytest.fixture
def captured_stdout():
    """Redirect the ProcessorFormatter handler to a buffer; yield line parser."""
    setup_logging("development", service_name="lemma-test", json_logs=True, log_level="DEBUG")
    buf = io.StringIO()
    handler = _processor_formatter_handler()
    original_stream = handler.stream
    handler.stream = buf

    def lines():
        return [line for line in buf.getvalue().splitlines() if line.strip()]

    yield lines
    handler.stream = original_stream


def _app_lines(lines):
    return [json.loads(line) for line in lines()]


def test_structlog_record_is_one_json_line_with_required_fields(captured_stdout):
    get_logger("app.demo").info("service.started", component="api")
    objs = _app_lines(captured_stdout)
    assert len(objs) == 1
    obj = objs[0]
    assert "\n" not in captured_stdout()[-1]
    assert obj["event"] == "service.started"
    assert obj["level"] == "info"
    assert obj["logger"] == "app.demo"
    assert obj["service.name"] == "lemma-test"
    assert obj["deployment.environment"] == "development"
    assert "timestamp" in obj
    # event is not duplicated into a message field.
    assert "message" not in obj


def test_foreign_stdlib_record_is_one_json_line(captured_stdout):
    logging.getLogger("some.foreign.lib").warning("hello world")
    objs = _app_lines(captured_stdout)
    assert len(objs) == 1
    obj = objs[0]
    assert obj["event"] == "hello world"
    assert obj["level"] == "warning"
    assert obj["logger"] == "some.foreign.lib"
    assert obj["service.name"] == "lemma-test"


def test_exception_renders_as_one_json_line_with_escaped_newlines(captured_stdout):
    logger = get_logger("app.demo")
    try:
        raise ValueError("boom\nsecond line")
    except ValueError:
        logger.exception("request.error", route="/x")
    lines = captured_stdout()
    assert len(lines) == 1, "exception must not fan out to multiple physical lines"
    obj = json.loads(lines[0])
    assert obj["event"] == "request.error"
    assert "exception" in obj
    assert isinstance(obj["exception"], str)
    assert "ValueError" in obj["exception"]


def test_foreign_exception_also_one_line(captured_stdout):
    try:
        raise RuntimeError("foreign fail")
    except RuntimeError:
        logging.getLogger("foreign.lib").exception("lib failed")
    lines = captured_stdout()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert "exception" in obj
    assert "RuntimeError" in obj["exception"]


def test_redaction_canary_absent_from_full_output(captured_stdout):
    canary = "CANARY-SECRET-1234567890"
    logger = get_logger("app.demo")
    logger.info(
        "event.with.secrets",
        authorization=f"Bearer {canary}",
        cookie=f"session={canary}",
        api_key=canary,
        nested={"token": canary, "ok": "keep"},
        url=f"https://ex.com/x?token={canary}",
        raw_bytes=canary.encode(),
    )
    out = captured_stdout()[-1]
    assert canary not in out
    obj = json.loads(out)
    assert obj["authorization"] == "[REDACTED]"
    assert obj["cookie"] == "[REDACTED]"
    assert obj["api_key"] == "[REDACTED]"
    assert obj["nested"]["token"] == "[REDACTED]"
    assert obj["nested"]["ok"] == "keep"
    # URL query param is redacted (urlencode percent-encodes the brackets).
    assert canary not in obj["url"]
    assert "REDACTED" in obj["url"]
    assert obj["raw_bytes"].startswith("<bytes:")


def test_foreign_record_redacted_through_same_pipeline(captured_stdout):
    canary = "CANARY-FOREIGN-9876543210"
    logging.getLogger("foreign.lib").info(
        f"call failed token={canary} url=https://ex.com/x?code={canary}"
    )
    out = captured_stdout()[-1]
    assert canary not in out


def test_setup_logging_is_idempotent(captured_stdout):
    setup_logging("development", service_name="lemma-a", json_logs=True)
    setup_logging("development", service_name="lemma-b", json_logs=True)
    setup_logging("development", service_name="lemma-c", json_logs=True)
    pf_handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    ]
    assert len(pf_handlers) == 1, "repeated setup must not duplicate the console handler"
    get_logger("x").info("once")
    objs = _app_lines(captured_stdout)
    assert len(objs) == 1
    assert objs[0]["service.name"] == "lemma-c"


def test_release_sha_appears_as_service_version_and_release_sha(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "release_sha", "a" * 40)
    setup_logging("development", service_name="lemma-api", json_logs=True)
    buf = io.StringIO()
    handler = _processor_formatter_handler()
    original_stream = handler.stream
    handler.stream = buf
    try:
        get_logger("app.demo").info("service.started")
        obj = json.loads(buf.getvalue().splitlines()[-1])
    finally:
        handler.stream = original_stream
    assert obj["service.version"] == "a" * 40
    assert obj["release.sha"] == "a" * 40


def test_active_trace_context_injected_once(monkeypatch, captured_stdout):
    from opentelemetry.trace import SpanContext, TraceFlags

    trace_id = 0x0123456789abcdef0123456789abcdef
    span_id = 0x0123456789abcdef

    class FakeSpan:
        def get_span_context(self):
            return SpanContext(
                trace_id=trace_id,
                span_id=span_id,
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )

    monkeypatch.setattr(logmod.trace, "get_current_span", lambda: FakeSpan())
    get_logger("app.demo").info("spanned.event")
    obj = _app_lines(captured_stdout)[-1]
    assert obj["trace_id"] == format(trace_id, "032x")
    assert obj["span_id"] == format(span_id, "016x")


def test_foreign_logger_levels_applied():
    setup_logging("production", service_name="lemma-api", json_logs=True)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("uvicorn.error").level == logging.INFO
    assert logging.getLogger("streaq").level == logging.WARNING
