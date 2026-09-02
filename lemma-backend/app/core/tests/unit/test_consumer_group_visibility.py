"""What an operator sees when a Redis consumer group is lost.

A consumer group vanishing -- a Redis restart without persistence, a failover to
an un-replicated replica, an eviction, a `FLUSHDB` -- is the ordinary self-host
accident, and it is the one that stops surface messages, schedule events and
workflow resumes from being consumed. Every record on that path used to be
`logger.debug`, and production runs at `LOG_LEVEL=INFO`, which drops a debug
record before it is formatted. So the failure that stops delivery produced an
empty log while `/health/ready` stayed 200.

These tests assert the level, not the call: a record nobody's log destination
keeps is the same as no record.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.core.infrastructure.events import stream_subscriber as ss
from app.core.infrastructure.events import consumer_groups


@pytest.fixture(autouse=True)
def _isolate_declared_topology(monkeypatch):
    monkeypatch.setattr(ss, "_DECLARED_STREAM_GROUPS", set())
    monkeypatch.setattr(
        ss, "_REGISTERED_STREAM_GROUPS", {("agent_events", "agent-events")}
    )


def _records(caplog, level: int) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= level]


async def test_a_group_recreated_mid_run_is_reported_at_warning(caplog):
    """The group was gone. That is the whole signal, and INFO has to keep it."""
    client = AsyncMock()
    client.xgroup_create = AsyncMock()

    with caplog.at_level(logging.DEBUG):
        created = await ss.ensure_consumer_groups(client)

    assert created == 1
    warnings = _records(caplog, logging.WARNING)
    assert [record.msg["event"] for record in warnings] == [
        "infrastructure.stream_subscriber.recreated_missing_consumer_groups.degraded"
    ]
    # Which groups were missing is what tells the operator what stopped being
    # delivered, so it has to survive into the record.
    assert "agent_events/agent-events" in warnings[0].msg["groups"]


async def test_creating_groups_at_startup_is_not_an_anomaly(caplog):
    """`warn_on_create=False` is the pre-create pass; a fresh Redis is expected."""
    client = AsyncMock()
    client.xgroup_create = AsyncMock()

    with caplog.at_level(logging.DEBUG):
        await ss.ensure_consumer_groups(client, warn_on_create=False)

    assert _records(caplog, logging.WARNING) == []


async def test_a_group_that_cannot_be_ensured_is_reported_at_error(caplog):
    client = AsyncMock()
    client.xgroup_create = AsyncMock(side_effect=ConnectionError("connection refused"))

    with caplog.at_level(logging.DEBUG):
        created = await ss.ensure_consumer_groups(client)

    # Still never raises: group plumbing must not crash the worker.
    assert created == 0
    errors = _records(caplog, logging.ERROR)
    assert [record.msg["event"] for record in errors] == [
        "infrastructure.stream_subscriber.consumer_group_ensure.failed"
    ]
    assert errors[0].msg["error_type"] == "ConnectionError"
    # The pipeline moves the traceback onto the record as a field; an error
    # record without one is a type name and no way to find the fault.
    assert "connection refused" in errors[0].msg["error_traceback"]


async def test_an_existing_group_says_nothing(caplog):
    client = AsyncMock()
    client.xgroup_create = AsyncMock(
        side_effect=Exception("BUSYGROUP Consumer Group name already exists")
    )

    with caplog.at_level(logging.DEBUG):
        created = await ss.ensure_consumer_groups(client)

    assert created == 0
    assert _records(caplog, logging.INFO) == []


async def test_the_reconcile_tick_reports_its_own_failure(caplog, monkeypatch):
    """The loop is the only thing that revives a lost group. Its death is news."""

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("redis pool exhausted")

    monkeypatch.setattr(consumer_groups, "ensure_consumer_groups", _boom)

    with caplog.at_level(logging.DEBUG):
        await consumer_groups.reconcile_consumer_groups_once(AsyncMock())

    errors = _records(caplog, logging.ERROR)
    assert [record.msg["event"] for record in errors] == [
        "infrastructure.consumer_groups.reconcile.failed"
    ]
    assert "redis pool exhausted" in errors[0].msg["error_traceback"]
