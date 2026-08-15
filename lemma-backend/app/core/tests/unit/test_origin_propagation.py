"""Origin has to survive the two hops that separate work from its own events.

``DomainEvent.origin`` is read from a contextvar at construction, and the events
that matter most are constructed far from where the work arrived: in a streaq
task minutes later, or in a consumer in another process. Both hops therefore
carry origin explicitly, and this file holds them honest.

Without this, every schedule, trigger, import and surface message produces
events with a null origin -- and three catalog entries are origin-pinned, so
they would be dropped outright by ``emitter.emit`` and log a contract violation
on every occurrence.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest

from app.core.domain.events import DomainEvent
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
)
from app.core.origin import (
    Origin,
    OriginKind,
    current_origin,
    origin_from_payload,
    origin_scope,
)
from app.core.request_context import bind_request_context
from app.core.tests.unit.test_job_observability import _Worker


class _Thing(DomainEvent):
    event_type: str = "thing.happened"


def _connected_queue(worker: _Worker) -> SharedStreaqJobQueue:
    queue = SharedStreaqJobQueue(lambda: worker)  # type: ignore[arg-type]
    queue._stack = AsyncExitStack()  # type: ignore[attr-defined]
    return queue


# -- the contextvar itself ------------------------------------------------


def test_an_event_records_the_origin_the_work_arrived_on() -> None:
    with origin_scope(Origin(OriginKind.SCHEDULE)):
        event = _Thing()
    assert event.origin == "SCHEDULE"


def test_an_event_raised_outside_any_scope_claims_no_origin() -> None:
    assert _Thing().origin is None


def test_an_unknown_origin_degrades_instead_of_raising() -> None:
    """A value written by a newer replica mid-deploy must cost one dimension on
    one event, never an exception -- the reclaim subscriber has no attempt cap,
    so a raise here would nack forever."""
    assert origin_from_payload({"origin": "TELEPORTER"}) is None
    assert origin_from_payload({}) is None


def test_a_platform_the_origin_does_not_recognise_is_dropped_not_carried() -> None:
    """`SurfacePlatform` values are uppercase and the origin allowlist is
    lowercase, so a caller forgetting `.lower()` silently loses the platform.
    Pinned here so the next person finds it in a test, not in a dashboard."""
    assert Origin(OriginKind.SURFACE, platform="SLACK").platform is None
    assert Origin(OriginKind.SURFACE, platform="slack").platform == "slack"


# -- hop one: enqueue -> streaq task --------------------------------------


async def test_enqueue_carries_origin_on_the_sidecar() -> None:
    worker = _Worker()
    queue = _connected_queue(worker)

    with origin_scope(Origin(OriginKind.SURFACE, platform="slack")):
        await queue.enqueue("process_thing", _job_id="job-1", thing_id="t1")

    _, raw, _ = worker.redis.set_calls[0]
    stored = json.loads(raw)
    assert stored["origin"] == "SURFACE"
    assert stored["origin_platform"] == "slack"


async def test_enqueue_without_an_origin_adds_no_keys() -> None:
    """Absent origin must stay absent rather than becoming a string 'None' that
    then fails to parse on the other side.

    A request context is bound only so a sidecar gets written at all -- with
    nothing to inherit, `enqueue` skips the write entirely.
    """
    worker = _Worker()
    queue = _connected_queue(worker)
    with bind_request_context(request_id="request-1", correlation_id=uuid4()):
        await queue.enqueue("process_thing", _job_id="job-2", thing_id="t2")

    _, raw, _ = worker.redis.set_calls[0]
    stored = json.loads(raw)
    assert "origin" not in stored
    assert "origin_platform" not in stored


def test_the_job_side_rebuilds_what_enqueue_stored() -> None:
    rebuilt = origin_from_payload({"origin": "SCHEDULE", "origin_platform": None})
    assert rebuilt is not None
    assert rebuilt.kind is OriginKind.SCHEDULE


# -- hop two: consumer -> child events ------------------------------------


def test_a_scope_restores_the_previous_origin_on_exit() -> None:
    """Consumers nest: a job running under IMPORT may consume an event that
    arrived under WEB, and the outer value must come back."""
    with origin_scope(Origin(OriginKind.IMPORT)):
        with origin_scope(Origin(OriginKind.WEB)):
            assert current_origin() is not None
            assert current_origin().kind is OriginKind.WEB  # type: ignore[union-attr]
        assert current_origin().kind is OriginKind.IMPORT  # type: ignore[union-attr]
    assert current_origin() is None


@pytest.mark.parametrize(
    "kind", [OriginKind.SCHEDULE, OriginKind.DATA_TRIGGER, OriginKind.IMPORT]
)
def test_an_event_raised_while_handling_another_inherits_its_origin(
    kind: OriginKind,
) -> None:
    """What the inbox does for every consumer: bind the inbound event's origin
    so anything the handler raises is attributed to how the original work
    arrived, not to the worker that happens to be running."""
    inbound = {"origin": kind.value}
    with origin_scope(origin_from_payload(inbound)):
        child = _Thing()
    assert child.origin == kind.value
