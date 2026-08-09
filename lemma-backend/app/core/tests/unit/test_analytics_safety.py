"""Adversarial proof that the product-analytics boundary is default-deny.

Sibling to ``test_otel_safety.py``: feed content that must never reach a
third-party analytics database through the real emitter, and assert none of it
survives. Lemma renders customer business data -- table records, agent
transcripts, file contents -- so the failure this guards against is not
hypothetical.
"""

from __future__ import annotations

import pytest

from app.core.analytics import emitter as analytics_emitter
from app.core.analytics.emitter import AnalyticsActor, configure, emit
from app.core.analytics.event_catalog import (
    ANALYTICS_CATALOG,
    GROUP_TYPES,
    SPINE_PROPERTIES,
    UnknownAnalyticEventError,
)
from app.core.analytics.sink import MemorySink, NullSink
from app.core.authorization.context import ActorType
from app.core.origin import Origin, OriginKind


ORG = "0192f1a0-0000-7000-8000-000000000001"
POD = "0192f1a0-0000-7000-8000-000000000002"
USER = "0192f1a0-0000-7000-8000-000000000003"


#: Every one of these must be unable to cross, in an allowed key or otherwise.
ADVERSARIAL = {
    "email": "someone@customer.example",
    "pod_name": "<script>alert(1)</script>",
    "prompt": "You are a helpful assistant. The customer's SSN is 123-45-6789.",
    "path": "/Users/someone/Documents/payroll.xlsx",
    "url": "https://app.example.com/records?token=sk-live-abcdef",
    "record": "Acme Corp owes $48,201.55",
}


@pytest.fixture
def sink() -> MemorySink:
    memory = MemorySink()
    configure(memory, deployment="test", strict=False)
    yield memory
    configure(None)


def _emit_pod_created(**properties: object) -> None:
    emit(
        "pod.created",
        actor=AnalyticsActor.user(USER),
        origin=Origin(OriginKind.WEB),
        organization_id=ORG,
        pod_id=POD,
        properties=properties,
    )


def test_adversarial_content_never_crosses_the_boundary(sink: MemorySink) -> None:
    # Every adversarial value, offered under an *allowed* key. `template_id`
    # and `source` are in pod.created's allowlist, so the key check alone would
    # let these through -- the value check is what stops them.
    for label, value in ADVERSARIAL.items():
        _emit_pod_created(pod_id=POD, template_id=value, source=value)

    # And again under keys that are not in the allowlist at all.
    _emit_pod_created(pod_id=POD, **ADVERSARIAL)

    assert sink.events, "events should still be captured, just stripped"
    serialized = repr([event.properties for event in sink.events])
    for label, value in ADVERSARIAL.items():
        assert value not in serialized, f"{label} survived export"
    for event in sink.events:
        assert "template_id" not in event.properties
        assert "source" not in event.properties
        assert set(event.properties) <= (
            ANALYTICS_CATALOG["pod.created"].properties | SPINE_PROPERTIES
        )


def test_bounded_identifier_values_do_cross(sink: MemorySink) -> None:
    """The boundary must not be so tight that it drops legitimate data."""
    _emit_pod_created(pod_id=POD, source="import", template_id="crm-starter")
    event = sink.events[-1]
    assert event.properties["source"] == "import"
    assert event.properties["template_id"] == "crm-starter"
    assert event.properties["pod_id"] == POD


def test_unknown_event_is_dropped_and_raises_in_strict_mode(sink: MemorySink) -> None:
    emit("pod.definitely_not_a_real_event", actor=AnalyticsActor.user(USER), pod_id=POD)
    assert sink.events == []

    configure(sink, deployment="test", strict=True)
    with pytest.raises(UnknownAnalyticEventError):
        emit("pod.definitely_not_a_real_event", actor=AnalyticsActor.user(USER), pod_id=POD)


def test_event_restricted_to_an_origin_rejects_every_other_origin(
    sink: MemorySink,
) -> None:
    # surface.message_answered can only come from a surface. One bearing CLI is
    # a bug in the emitting code, not a data point.
    emit(
        "surface.message_answered",
        actor=AnalyticsActor.user(USER),
        origin=Origin(OriginKind.CLI),
        organization_id=ORG,
        pod_id=POD,
        properties={"pod_id": POD},
    )
    assert sink.events == []

    emit(
        "surface.message_answered",
        actor=AnalyticsActor.user(USER),
        origin=Origin(OriginKind.SURFACE, platform="slack"),
        organization_id=ORG,
        pod_id=POD,
        properties={"pod_id": POD},
    )
    assert sink.events[-1].properties["origin_platform"] == "slack"


def test_unknown_origin_platform_is_dropped_not_forwarded() -> None:
    assert Origin(OriginKind.SURFACE, platform="slack").platform == "slack"
    # A platform outside the allowlist degrades the dimension rather than
    # widening it -- and never fails the request that carried it.
    assert Origin(OriginKind.SURFACE, platform="carrier-pigeon").platform is None
    # Origins that carry no platform never acquire one.
    assert Origin(OriginKind.WEB, platform="slack").platform is None


def test_call_site_cannot_forge_the_spine(sink: MemorySink) -> None:
    """A caller must not be able to relabel who acted or how work arrived."""
    emit(
        "pod.created",
        actor=AnalyticsActor.user(USER),
        origin=Origin(OriginKind.WEB),
        organization_id=ORG,
        pod_id=POD,
        properties={
            "actor_type": "SYSTEM",
            "origin": "SCHEDULE",
            "on_behalf_of_user": "somebody-else",
            "deployment": "production",
        },
    )
    event = sink.events[-1]
    assert event.properties["actor_type"] == ActorType.USER.value
    assert event.properties["origin"] == OriginKind.WEB.value
    assert event.properties["deployment"] == "test"
    assert "on_behalf_of_user" not in event.properties


def test_autonomous_work_lands_on_the_pod_not_a_fabricated_person(
    sink: MemorySink,
) -> None:
    emit(
        "schedule_run.completed",
        actor=AnalyticsActor.autonomous(),
        origin=Origin(OriginKind.SCHEDULE),
        organization_id=ORG,
        pod_id=POD,
        properties={"pod_id": POD, "status": "succeeded"},
    )
    event = sink.events[-1]
    assert event.distinct_id == f"pod:{POD}"
    assert event.properties["actor_type"] == ActorType.SYSTEM.value
    assert event.groups == {"organization": ORG, "pod": POD}


def test_delegated_work_records_both_the_agent_and_the_human(sink: MemorySink) -> None:
    emit(
        "agent_run.completed",
        actor=AnalyticsActor.delegated(delegated_by_user_id=USER),
        origin=Origin(OriginKind.WEB),
        organization_id=ORG,
        pod_id=POD,
        properties={"pod_id": POD, "status": "succeeded"},
    )
    event = sink.events[-1]
    # The work belongs on the human's timeline...
    assert event.distinct_id == USER
    # ...while staying distinguishable from work the human did themselves.
    assert event.properties["actor_type"] == ActorType.DELEGATED_USER_WORKLOAD.value
    assert event.properties["on_behalf_of_user"] == USER


def test_default_sink_is_null_so_an_unconfigured_process_sends_nothing() -> None:
    configure(None)
    assert isinstance(analytics_emitter.current_sink(), NullSink)
    emit(
        "pod.created",
        actor=AnalyticsActor.user(USER),
        pod_id=POD,
        properties={"pod_id": POD},
    )  # must not raise


def test_catalog_is_internally_consistent() -> None:
    failures: list[str] = []
    for name, spec in ANALYTICS_CATALOG.items():
        if not name.replace(".", "").replace("_", "").isalnum() or "." not in name:
            failures.append(f"{name}: expected noun.verb_past")
        if overlap := spec.properties & SPINE_PROPERTIES:
            failures.append(f"{name}: redeclares spine properties {sorted(overlap)}")
        if unknown := spec.groups - GROUP_TYPES:
            failures.append(f"{name}: unknown group types {sorted(unknown)}")
        for prop in spec.properties:
            if any(
                part in prop for part in ("name", "email", "prompt", "url", "path", "text")
            ):
                failures.append(f"{name}: property {prop!r} names a PII-shaped field")
    assert not failures, "\n" + "\n".join(failures)
