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
    origin_for_path,
    origin_from_payload,
    origin_scope,
    resolve_client_identity,
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


# -- inbound edges: the path is the only honest signal ---------------------


def test_a_surface_webhook_is_surface_work_not_somebody_s_script() -> None:
    """An inbound webhook carries the sending platform's headers, not Lemma's,
    so `X-Lemma-Client` resolves it to SDK. Without the path rule an agent
    answering in Slack is counted as a script."""
    assert resolve_client_identity(None).origin.kind is OriginKind.SDK

    resolved = origin_for_path("/surfaces/webhooks/slack")
    assert resolved is not None
    assert resolved.kind is OriginKind.SURFACE
    assert resolved.platform == "slack"


def test_a_surface_webhook_platform_is_lowercased_into_the_allowlist() -> None:
    resolved = origin_for_path("/surfaces/webhooks/TELEGRAM")
    assert resolved is not None
    assert resolved.platform == "telegram"


def test_a_surface_scoped_webhook_keeps_the_kind_without_inventing_a_platform() -> None:
    resolved = origin_for_path("/surfaces/0192f1a0-dead-beef/webhook")
    assert resolved is not None
    assert resolved.kind is OriginKind.SURFACE
    assert resolved.platform is None


def test_connector_ingress_is_not_confused_with_a_surface() -> None:
    """The catalog says never to sum inside reach with outside reach, which only
    works if the two never share an origin."""
    resolved = origin_for_path("/webhooks/composio")
    assert resolved is not None
    assert resolved.kind is OriginKind.CONNECTOR
    assert resolved.platform == "composio"


@pytest.mark.parametrize(
    "path", ["/pods", "/surfaces", "/surfaces/webhooks", "/webhooks"]
)
def test_a_path_that_is_not_an_edge_claims_no_origin(path: str) -> None:
    assert origin_for_path(path) is None


@pytest.mark.parametrize(
    ("header", "kind"),
    [
        ("lemma-web/0.7.0", OriginKind.WEB),
        ("lemma-desktop/0.7.0", OriginKind.DESKTOP),
        ("lemma-app/0.7.0", OriginKind.APP),
        ("lemma-sdk-ts/0.7.0", OriginKind.SDK),
        ("something-else/1.0", OriginKind.SDK),
    ],
)
def test_a_client_that_names_itself_gets_its_own_origin(
    header: str, kind: OriginKind
) -> None:
    assert resolve_client_identity(header).origin.kind is kind


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
