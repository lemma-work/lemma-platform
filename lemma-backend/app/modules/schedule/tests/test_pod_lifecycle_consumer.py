"""Tests for the schedule pod-deletion cleanup handler."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.schedule.handlers import pod_lifecycle_consumer


@asynccontextmanager
async def _mock_uow_factory(uow_mock):
    yield uow_mock


@pytest.mark.asyncio
async def test_on_pod_deleted_cleans_up_pod_schedules(monkeypatch):
    service = AsyncMock()
    service.delete_all_for_pod.return_value = 3
    uow_mock = AsyncMock()
    monkeypatch.setattr(
        pod_lifecycle_consumer, "get_schedule_service", lambda uow: service
    )

    pod_id = uuid4()
    event = {
        "event_type": "pod.deleted",
        "pod_id": str(pod_id),
        "organization_id": str(uuid4()),
    }

    await pod_lifecycle_consumer.on_pod_deleted(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
    )

    service.delete_all_for_pod.assert_awaited_once_with(pod_id)


@pytest.mark.asyncio
async def test_on_pod_deleted_ignores_other_pod_events(monkeypatch):
    service = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(
        pod_lifecycle_consumer, "get_schedule_service", lambda uow: service
    )

    event = {
        "event_type": "pod.created",
        "pod_id": str(uuid4()),
        "organization_id": str(uuid4()),
    }

    await pod_lifecycle_consumer.on_pod_deleted(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
    )

    service.delete_all_for_pod.assert_not_awaited()
