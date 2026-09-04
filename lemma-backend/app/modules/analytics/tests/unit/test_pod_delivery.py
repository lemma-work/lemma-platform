"""What counts as a pod delivering, and what does not.

Activation is the metric the product-analytics design is named for, and it is
the one where a plausible-looking definition quietly measures the wrong thing.
The two failure modes this file pins:

* counting a builder poking their own pod, which makes activation track effort
  rather than outcomes;
* *not* counting a scheduled run because nobody was watching, which scores the
  design doc's own canonical example of a working pod as churn.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.analytics.services.pod_delivery import (
    AUTONOMOUS_ORIGINS,
    DeliveryVia,
    qualifies,
)
from app.core.origin import Origin, OriginKind

BUILDER = uuid4()
SOMEBODY_ELSE = uuid4()


def _web() -> Origin:
    return Origin(OriginKind.WEB)


# -- branch (a): somebody other than the builder -------------------------


def test_a_second_person_receiving_an_outcome_is_delivery() -> None:
    assert qualifies(
        origin=_web(), recipient_user_id=SOMEBODY_ELSE, creator_user_id=BUILDER
    )


def test_the_builder_using_their_own_pod_is_not_delivery() -> None:
    """Otherwise activation measures how much someone poked their own work."""
    assert not qualifies(
        origin=_web(), recipient_user_id=BUILDER, creator_user_id=BUILDER
    )


@pytest.mark.parametrize(
    ("recipient", "creator"),
    [(None, BUILDER), (SOMEBODY_ELSE, None), (None, None)],
)
def test_an_unprovable_recipient_is_not_claimed_as_delivery(recipient, creator) -> None:
    """Fail closed. A pod that cannot be shown to have reached someone else has
    not been shown to activate, and inventing activations is worse than missing
    them -- the funnel is downstream of this."""
    assert not qualifies(
        origin=_web(), recipient_user_id=recipient, creator_user_id=creator
    )


# -- branch (b): autonomous origins --------------------------------------


@pytest.mark.parametrize("kind", sorted(AUTONOMOUS_ORIGINS, key=lambda k: k.value))
def test_autonomous_work_delivers_even_for_its_own_builder(kind: OriginKind) -> None:
    """The load-bearing half. A scheduled report pod, owned and read by the
    person who built it, is the design's canonical example of a pod earning its
    keep -- and branch (a) alone would never activate it."""
    assert qualifies(
        origin=Origin(kind), recipient_user_id=BUILDER, creator_user_id=BUILDER
    )


def test_autonomous_delivery_needs_no_recipient_at_all() -> None:
    assert qualifies(
        origin=Origin(OriginKind.SCHEDULE), recipient_user_id=None, creator_user_id=None
    )


@pytest.mark.parametrize(
    "kind", [OriginKind.WEB, OriginKind.CLI, OriginKind.SDK, OriginKind.DESKTOP]
)
def test_a_person_driven_origin_does_not_take_the_autonomous_shortcut(
    kind: OriginKind,
) -> None:
    """Otherwise every origin would deliver and the recipient test would be
    dead code."""
    assert not qualifies(
        origin=Origin(kind), recipient_user_id=BUILDER, creator_user_id=BUILDER
    )


def test_an_absent_origin_falls_back_to_the_recipient_test() -> None:
    assert not qualifies(
        origin=None, recipient_user_id=BUILDER, creator_user_id=BUILDER
    )
    assert qualifies(
        origin=None, recipient_user_id=SOMEBODY_ELSE, creator_user_id=BUILDER
    )


def test_via_is_a_closed_set() -> None:
    """`via` is a funnel dimension, so it must never become free text."""
    assert {v.value for v in DeliveryVia} == {
        "agent_run",
        "workflow_run",
        "schedule_run",
        "surface_message",
    }
