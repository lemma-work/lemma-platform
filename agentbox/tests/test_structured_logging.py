from __future__ import annotations

import io
import json
import logging
import sys
from uuid import UUID

import pytest

import agentbox.observability as observability
from agentbox.observability import (
    LoggingContractError,
    ReleaseIdentityError,
    bind_context,
    get_logger,
    setup_logging,
    validate_release_identity,
)


def test_logging_reconciliation_context_and_exception_safety(monkeypatch) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "development")
    monkeypatch.setenv("LEMMA_RELEASE_SHA", "a" * 40)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    obsolete_console = logging.StreamHandler(sys.stderr)
    capture = CaptureHandler()
    root.handlers = [obsolete_console, capture]
    output = io.StringIO()
    try:
        setup_logging(level="DEBUG")
        setup_logging(level="DEBUG")
        owned = [
            handler
            for handler in root.handlers
            if getattr(handler, "_agentbox_json_console_handler", False)
        ]
        assert len(owned) == 1
        assert obsolete_console not in root.handlers
        assert capture in root.handlers
        owned[0].setStream(output)

        with bind_context(
            request_id="request-1",
            correlation_id=UUID("11111111-1111-1111-1111-111111111111"),
            event_id=UUID("22222222-2222-2222-2222-222222222222"),
            job_id="job-1",
        ):
            get_logger("agentbox.test").info(
                "dependency.recovered",
                dependency="docker",
                failure_count=1,
                incident_duration_ms=25,
            )
            try:
                raise RuntimeError("CANARY secret /private/source.py\nprovider payload")
            except RuntimeError:
                get_logger("agentbox.test").exception(
                    "http.request.failed",
                    method="POST",
                    route="/sandboxes/{workload_kind}/{logical_id}",
                    status_code=500,
                    duration_ms=25,
                    error_type="RuntimeError",
                    error_code="INTERNAL",
                )
            try:
                raise ValueError("CANARY foreign secret /private/provider.py")
            except ValueError:
                logging.getLogger("foreign.provider").exception(
                    "dependency response token=CANARY"
                )
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(lines) == 3
    assert lines[0] == {
        "timestamp": lines[0]["timestamp"],
        "level": "info",
        "event": "dependency.recovered",
        "logger": "agentbox.test",
        "service.name": "lemma-agentbox",
        "service.version": "a" * 40,
        "release.sha": "a" * 40,
        "deployment.environment": "development",
        "deployment.environment.name": "development",
        "request_id": "request-1",
        "correlation_id": "11111111-1111-1111-1111-111111111111",
        "event_id": "22222222-2222-2222-2222-222222222222",
        "job_id": "job-1",
        "dependency": "docker",
        "failure_count": 1,
        "incident_duration_ms": 25,
    }
    assert lines[1]["error_type"] == "RuntimeError"
    assert len(lines[1]["error_stack_hash"]) == 64
    assert len(lines[1].get("error_frames", [])) <= 8
    assert lines[2]["event"] == "dependency response token=[REDACTED]"
    assert lines[2]["error_type"] == "ValueError"
    assert "CANARY" not in output.getvalue()
    assert records[-1].exc_info is None
    assert records[-1].exc_text is None
    assert getattr(records[-1], "lemma_safe_exception")["error_type"] == "ValueError"


def test_dependency_records_keep_message_and_follow_configured_level(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "development")
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    dependency = logging.getLogger("httpx")
    original_dependency_level = dependency.level
    output = io.StringIO()
    root.handlers = []
    try:
        setup_logging(level="INFO")
        owned = next(
            handler
            for handler in root.handlers
            if getattr(handler, "_agentbox_json_console_handler", False)
        )
        owned.setStream(output)
        dependency.debug("request details")
        dependency.info(
            "request completed authorization=Bearer abc.def.ghi "
            "url=https://user:password@example.test/path?token=secret"
        )
        dependency.warning("request failed token=secret")
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
        dependency.setLevel(original_dependency_level)

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(lines) == 1
    assert lines[0]["logger"] == "httpx"
    assert lines[0]["level"] == "warning"
    assert lines[0]["event"] == "request failed token=[REDACTED]"
    assert "abc.def.ghi" not in output.getvalue()
    assert "password" not in output.getvalue()
    assert "secret" not in output.getvalue()


def test_local_logging_contract_rejects_unregistered_event(monkeypatch) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "local")
    monkeypatch.setenv("LEMMA_LOGGING_CONTRACT_STRICT", "true")
    with pytest.raises(LoggingContractError, match="unregistered_event"):
        get_logger("agentbox.test").info("agentbox.test.not_registered")


def test_production_requires_canonical_release_sha(monkeypatch) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "production")
    monkeypatch.setenv("LEMMA_RELEASE_SHA", "not-a-release")
    monkeypatch.setenv("RELEASE_SHA", "b" * 40)
    with pytest.raises(ReleaseIdentityError, match="LEMMA_RELEASE_SHA"):
        validate_release_identity()


def test_production_contract_violation_is_bounded_and_emitted_once(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "production")
    monkeypatch.setenv("LEMMA_RELEASE_SHA", "c" * 40)
    monkeypatch.setattr(observability, "_contract_violation_emitted", False)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    output = io.StringIO()
    root.handlers = []
    try:
        setup_logging(level="INFO")
        owned = next(
            handler
            for handler in root.handlers
            if getattr(handler, "_agentbox_json_console_handler", False)
        )
        owned.setStream(output)
        logger = get_logger("agentbox.test")
        logger.info("CANARY invalid event")
        logger.info("CANARY second invalid event")
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "logging.contract.violation"
    assert lines[0]["contract_violation"] == "invalid_event_name"
    assert "CANARY" not in output.getvalue()


def test_deployed_development_never_raises_for_contract_violation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LEMMA_ENVIRONMENT", "development")
    monkeypatch.setenv("LEMMA_LOGGING_CONTRACT_STRICT", "true")
    monkeypatch.setenv("LEMMA_RELEASE_SHA", "d" * 40)
    monkeypatch.setattr(observability, "_contract_violation_emitted", False)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    output = io.StringIO()
    root.handlers = []
    try:
        setup_logging(level="INFO")
        owned = next(
            handler
            for handler in root.handlers
            if getattr(handler, "_agentbox_json_console_handler", False)
        )
        owned.setStream(output)
        get_logger("agentbox.test").info("CANARY invalid event")
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "logging.contract.violation"
    assert "CANARY" not in output.getvalue()
