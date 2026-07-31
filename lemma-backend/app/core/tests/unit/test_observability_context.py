"""Logical lineage and explicit asyncio task-context semantics."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.core.request_context import (
    bind_job_context,
    bind_request_context,
    correlation_headers,
    create_background_task,
    create_inherited_task,
    current_observability_context,
    event_lineage,
    get_causation_id,
)


def test_event_logs_real_parent_while_child_uses_current_event_as_cause() -> None:
    correlation_id = uuid4()
    parent_id = uuid4()
    event_id = uuid4()
    with bind_request_context(request_id="request-1", correlation_id=correlation_id):
        with event_lineage(
            event_id=event_id,
            causation_id=parent_id,
            event_type="ThingChanged",
            consumer="unit.consumer",
        ):
            context = current_observability_context()
            assert context.correlation_id == correlation_id
            assert context.event_id == event_id
            assert context.causation_id == parent_id
            assert get_causation_id() == event_id
    assert current_observability_context().as_log_fields() == {}


def test_non_http_root_event_uses_its_event_id_as_correlation() -> None:
    root_event_id = uuid4()
    with event_lineage(event_id=root_event_id, event_type="ScheduleFired"):
        context = current_observability_context()
        assert context.correlation_id == root_event_id
        assert context.event_id == root_event_id
        assert context.request_id is None


def test_sibling_events_share_correlation_and_keep_distinct_lineage() -> None:
    correlation_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    observed = []
    with bind_request_context(request_id="request-2", correlation_id=correlation_id):
        for event_id in (first_id, second_id):
            with event_lineage(event_id=event_id):
                observed.append(current_observability_context())
    assert [item.event_id for item in observed] == [first_id, second_id]
    assert {item.correlation_id for item in observed} == {correlation_id}


async def test_background_task_starts_clean_and_inherited_task_keeps_lineage() -> None:
    correlation_id = uuid4()

    async def read_context():
        await asyncio.sleep(0)
        return current_observability_context()

    with bind_request_context(request_id="request-3", correlation_id=correlation_id):
        inherited = create_inherited_task(read_context())
        background = create_background_task(read_context())
        inherited_context, background_context = await asyncio.gather(
            inherited, background
        )
    assert inherited_context.request_id == "request-3"
    assert inherited_context.correlation_id == correlation_id
    assert background_context.as_log_fields() == {}


def test_trusted_headers_include_only_current_available_identifiers() -> None:
    correlation_id = uuid4()
    event_id = uuid4()
    with bind_request_context(request_id="request-4", correlation_id=correlation_id):
        with event_lineage(event_id=event_id):
            with bind_job_context(job_id="job-1", task_name="unit.task"):
                assert correlation_headers() == {
                    "x-request-id": "request-4",
                    "x-lemma-correlation-id": str(correlation_id),
                    "x-lemma-event-id": str(event_id),
                    "x-lemma-job-id": "job-1",
                }
