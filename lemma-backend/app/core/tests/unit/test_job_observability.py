from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from agentbox_client import AgentBoxClient
from unittest.mock import AsyncMock

from app.core.domain.events import DomainEvent
from app.core.infrastructure.events.inbox import InboxConsumer
from app.core.infrastructure.events.outbox import ClaimedEvent
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    job_context_key,
)
from app.core.infrastructure.jobs.streaq_runtime import load_job_observability_context
from app.core.request_context import (
    bind_job_context,
    bind_request_context,
    correlation_headers,
    event_lineage,
)


class _JourneyEvent(DomainEvent):
    event_type: str = "journey.started"

    @classmethod
    def stream_name(cls) -> str:
        return "journey_events"


class _Inbox(InboxConsumer):
    def __init__(self) -> None:
        super().__init__(AsyncMock())  # type: ignore[arg-type]

    async def _claim(self, consumer, event_id, event_type):
        del consumer, event_id, event_type
        return 1

    async def _finish(
        self,
        consumer,
        event_id,
        status,
        *,
        error_type=None,
        failed_attempts=None,
    ) -> None:
        del consumer, event_id, status, error_type, failed_attempts


class _Task:
    def __init__(self) -> None:
        self.id = "generated-job"
        self.scheduled_at = None
        self.published = False

    def start(self, *, schedule) -> None:
        self.scheduled_at = schedule

    def __await__(self):
        async def publish():
            self.published = True
            return self

        return publish().__await__()


class _Redis:
    def __init__(self, *, fail: bool = False, value=None) -> None:
        self.fail = fail
        self.value = value
        self.set_calls: list[tuple[str, str, int]] = []

    async def set(self, key, value, *, ex) -> None:
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.set_calls.append((key, value, ex))

    async def get(self, key):
        del key
        if self.fail:
            raise RuntimeError("redis unavailable")
        return self.value


class _Worker:
    def __init__(self, *, redis: _Redis | None = None) -> None:
        self.redis = redis or _Redis()
        self._initialized = True
        self.enqueued: list[tuple[str, dict]] = []
        self.task = _Task()

    def enqueue_unsafe(self, job_name: str, **kwargs):
        self.enqueued.append((job_name, kwargs))
        return self.task


def _connected_queue(worker: _Worker) -> SharedStreaqJobQueue:
    queue = SharedStreaqJobQueue(lambda: worker)  # type: ignore[arg-type]
    queue._stack = AsyncExitStack()  # type: ignore[attr-defined]
    return queue


@pytest.mark.asyncio
async def test_enqueue_keeps_payload_compatible_and_stores_context_sidecar() -> None:
    worker = _Worker()
    queue = _connected_queue(worker)
    correlation_id = uuid4()
    event_id = uuid4()
    deferred_until = datetime.now(timezone.utc) + timedelta(hours=72)

    with bind_request_context(request_id="request-1", correlation_id=correlation_id):
        with event_lineage(event_id=event_id, event_type="ThingCreated"):
            task = await queue.defer(
                "process_thing",
                defer_until=deferred_until,
                _job_id="job-1",
                thing_id="thing-1",
            )

    assert task is worker.task
    assert worker.enqueued == [("process_thing", {"thing_id": "thing-1"})]
    assert worker.task.id == "job-1"
    assert worker.task.published is True
    assert worker.task.scheduled_at == deferred_until
    key, raw, ttl = worker.redis.set_calls[0]
    assert key == job_context_key("job-1")
    stored = json.loads(raw)
    expected = {
        "request_id": "request-1",
        "correlation_id": str(correlation_id),
        "event_id": str(event_id),
        "event_type": "ThingCreated",
    }
    assert {key: stored[key] for key in expected} == expected
    assert ttl >= (72 + 48) * 60 * 60 - 5


@pytest.mark.asyncio
async def test_context_persistence_failure_never_fails_business_enqueue() -> None:
    worker = _Worker(redis=_Redis(fail=True))
    queue = _connected_queue(worker)
    with bind_request_context(request_id="request-2", correlation_id=uuid4()):
        task = await queue.enqueue("process_thing", thing_id="thing-2")
    assert task is worker.task
    assert worker.task.published is True


@pytest.mark.asyncio
async def test_old_or_missing_job_context_is_tolerated() -> None:
    assert await load_job_observability_context(_Redis(value=None), "old-job") == {}
    assert await load_job_observability_context(_Redis(fail=True), "missing-job") == {}
    assert await load_job_observability_context(
        _Redis(value=json.dumps({"request_id": "request-3", "attempt": 2})),
        "new-job",
    ) == {"request_id": "request-3", "attempt": "2"}


@pytest.mark.asyncio
async def test_full_correlation_journey_changes_only_boundary_identifiers() -> None:
    worker = _Worker()
    queue = _connected_queue(worker)
    request_id = "journey-request"
    correlation_id = uuid4()

    with bind_request_context(
        request_id=request_id, correlation_id=correlation_id
    ):
        event = _JourneyEvent()
    claimed = ClaimedEvent(
        id=event.event_id,
        stream=event.stream_name(),
        event_type=event.event_type,
        payload=event.model_dump(mode="json"),
        attempts=0,
        occurred_at=event.occurred_at,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        request_id=event.request_id,
    )

    async def enqueue_descendant_job() -> None:
        await queue.enqueue(
            "journey_worker",
            _job_id="journey-job",
            event_id=str(claimed.id),
        )

    await _Inbox().process(
        "journey.consumer", claimed.payload, enqueue_descendant_job
    )
    _, raw, _ = worker.redis.set_calls[0]
    inherited = json.loads(raw)
    with bind_job_context(
        job_id="journey-job",
        task_name="journey_worker",
        attempt=1,
        inherited=inherited,
    ):
        client = AgentBoxClient(
            base_url="https://agentbox.test",
            api_key="manager-key",
            context_headers_provider=correlation_headers,
        )
        agentbox_headers = client._context_headers()
        await client.close()

    assert event.request_id == request_id
    assert event.correlation_id == correlation_id
    assert claimed.id == event.event_id
    assert inherited["event_id"] == str(event.event_id)
    assert agentbox_headers == {
        "x-request-id": request_id,
        "x-lemma-correlation-id": str(correlation_id),
        "x-lemma-event-id": str(event.event_id),
        "x-lemma-job-id": "journey-job",
    }
    assert str(event.event_id) != "journey-job"
