"""Release identity validation (LEMMA_RELEASE_SHA).

Release identity is best-effort log/release correlation: a missing or malformed
value never blocks startup. ``validate_release_identity`` only warns, and the
log context falls back to ``"unknown"`` so unattributed deployments are still
queryable (``release.sha == "unknown"``).
"""

from __future__ import annotations


from app.core.config import settings
from app.core.log.log import setup_logging, validate_release_identity


def test_valid_sha_does_not_warn(monkeypatch):
    monkeypatch.setattr(settings, "release_sha", "a" * 40)
    # Must not raise; no warning expected.
    validate_release_identity("production")


def test_production_does_not_raise_on_missing_sha(monkeypatch):
    monkeypatch.setattr(settings, "release_sha", None)
    # Must not raise in production.
    validate_release_identity("production")


def test_production_does_not_raise_on_malformed_sha(monkeypatch):
    monkeypatch.setattr(settings, "release_sha", "not-a-sha")
    validate_release_identity("production")


def test_production_does_not_raise_on_short_sha(monkeypatch):
    monkeypatch.setattr(settings, "release_sha", "abc123")
    validate_release_identity("production")


def test_missing_sha_falls_back_to_unknown_in_log_context(monkeypatch):
    import io
    import json
    import logging

    import structlog

    from app.core.log.log import get_logger

    monkeypatch.setattr(settings, "release_sha", None)
    setup_logging("production", service_name="lemma-api", json_logs=True)
    buf = io.StringIO()
    handler = next(
        h for h in logging.getLogger().handlers
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    )
    original = handler.stream
    handler.stream = buf
    try:
        get_logger("app.demo").info("service.started")
        obj = json.loads(buf.getvalue().splitlines()[-1])
    finally:
        handler.stream = original
    # Falls back to "unknown" (queryable), not omitted.
    assert obj["release.sha"] == "unknown"
    assert obj["service.version"] == "unknown"


def test_malformed_sha_falls_back_to_unknown_in_log_context(monkeypatch):
    import io
    import json
    import logging

    import structlog

    from app.core.log.log import get_logger

    monkeypatch.setattr(settings, "release_sha", "not-a-sha")
    setup_logging("production", service_name="lemma-api", json_logs=True)
    buf = io.StringIO()
    handler = next(
        h for h in logging.getLogger().handlers
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    )
    original = handler.stream
    handler.stream = buf
    try:
        get_logger("app.demo").info("service.started")
        obj = json.loads(buf.getvalue().splitlines()[-1])
    finally:
        handler.stream = original
    # A malformed value must NOT be emitted as service.version; fall back to "unknown".
    assert obj["release.sha"] == "unknown"
    assert obj["service.version"] == "unknown"


def test_valid_sha_appears_in_log_context(monkeypatch):
    import io
    import json
    import logging

    import structlog

    from app.core.log.log import get_logger

    sha = "b" * 40
    monkeypatch.setattr(settings, "release_sha", sha)
    setup_logging("production", service_name="lemma-api", json_logs=True)
    buf = io.StringIO()
    handler = next(
        h for h in logging.getLogger().handlers
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    )
    original = handler.stream
    handler.stream = buf
    try:
        get_logger("app.demo").info("service.started")
        obj = json.loads(buf.getvalue().splitlines()[-1])
    finally:
        handler.stream = original
    assert obj["release.sha"] == sha
    assert obj["service.version"] == sha
