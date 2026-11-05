"""What the components' answers add up to.

The endpoint runs the probes; this decides what they mean. Kept apart because
every component added to readiness used to add another conditional to an
endpoint already carrying six, and the shape of "not applicable" versus "not
healthy" is the thing that keeps getting confused between them.
"""

from __future__ import annotations

import pytest

from app.core.observability.readiness import (
    build_readiness_report,
    dependency_state,
    migrations_state,
    reported_only,
    worker_state,
)

pytestmark = pytest.mark.unit


def test_every_component_healthy_is_ready():
    report = build_readiness_report(
        components={
            "db": dependency_state(True),
            "redis": dependency_state(True),
        },
        instance_id=None,
    )

    assert report.status_code == 200
    assert report.payload == {
        "status": "ready",
        "components": {"db": "ok", "redis": "ok"},
    }


def test_one_unhealthy_component_refuses_work_and_is_still_named():
    report = build_readiness_report(
        components={
            "db": dependency_state(True),
            "supertokens": dependency_state(False),
        },
        instance_id="replica-7",
    )

    assert report.status_code == 503
    assert report.payload["status"] == "not_ready"
    assert report.payload["components"]["supertokens"] == "down"
    # The healthy ones are still reported, so the payload says which one it was.
    assert report.payload["components"]["db"] == "ok"
    assert report.payload["instance_id"] == "replica-7"


def test_a_component_that_does_not_apply_here_is_left_out_entirely():
    """`None` means "there is no worker in this topology to ask about".

    Reported as a status it would read as a failure to a probe and to a person;
    counted toward readiness it would take every API-only deployment out of
    rotation.
    """
    report = build_readiness_report(
        components={"db": dependency_state(True), "worker": worker_state(None)},
        instance_id=None,
    )

    assert report.status_code == 200
    assert "worker" not in report.payload["components"]


def test_only_a_stalled_worker_and_a_pending_schema_refuse_work():
    assert worker_state("ok").ready is True
    assert worker_state("stalled").ready is False
    assert migrations_state("current").ready is True
    assert migrations_state("pending").ready is False
    # A question that could not be asked is not an answer: it is reported and
    # does not hold the process out of rotation.
    assert migrations_state("unknown").ready is True


def test_a_datastore_role_that_cannot_create_roles_is_named_but_refuses_nothing():
    """Without CREATEROLE every pod query answers 503, and the only sign was a
    warning per schema. Named here; never blocking, because the rest of the
    product works and one feature's missing grant must not become an outage."""
    report = build_readiness_report(
        components={
            "db": dependency_state(True),
            "datastore_roles": reported_only("missing_createrole"),
        },
        instance_id=None,
    )

    assert report.status_code == 200
    assert report.payload["components"]["datastore_roles"] == "missing_createrole"
    assert reported_only(None) is None
