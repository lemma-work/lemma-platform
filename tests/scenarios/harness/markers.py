"""The vocabulary a scenario test uses to say what it is.

Five decorators, all thin wrappers over pytest marks so that everything pytest
already does — selection with ``-m``, ``-k``, ``--lf``, xdist distribution —
keeps working untouched.

    pytestmark = [journey("Getting started"), capability("Sign up")]

    @scenario("A new person signs up and lands in their own organization")
    @proves("PS-ONB-001")
    @covers("org.create", "auth.signed_up")
    async def test_new_person_signs_up(world): ...

``journey`` and ``capability`` are normally set once per module via
``pytestmark``; ``scenario``, ``proves`` and ``covers`` are per test.

These are read by two things that never import each other: ``reporting.py``
turns them into the run's output, and ``scripts/check_scenario_coverage.py``
reads them straight out of the source with ``ast``, so the traceability gates do
not need to import or execute the suite.
"""

from __future__ import annotations

import pytest

__all__ = ["capability", "covers", "journey", "proves", "scenario", "stack_lane"]


def journey(title: str):
    """The stretch of product this test belongs to. One per module."""
    return pytest.mark.journey(title)


def capability(title: str):
    """The coherent thing a person can do. One per module, or per class."""
    return pytest.mark.capability(title)


def scenario(title: str):
    """What this test proves, in a sentence a person would say.

    Also the selection marker for the whole suite: ``-m scenario``.
    """
    return pytest.mark.scenario(title)


def proves(*scenario_ids: str):
    """The ``PS-`` promises in docs/product this test proves.

    Gated: an id that does not exist there fails ``make quality``, and a
    scenario marked ``covered`` with nothing proving it fails the same way.
    """
    return pytest.mark.proves(*scenario_ids)


def covers(*contract_names: str):
    """The OpenAPI operation ids and analytics events this test exercises.

    No new identifier space: both already exist and are already CI-gated, so a
    typo here is caught against the real specification rather than against a
    second list that could itself drift.
    """
    return pytest.mark.covers(*contract_names)


def stack_lane(why: str):
    """This scenario needs a deployment configured to be *broken* a certain way.

    Three of these, and they are not second-class: a converter that is not
    installed, a search provider that is not configured, an organization capped
    at zero spend. Each is a real promise about how the product behaves when
    something it depends on is missing, and each one runs and proves that
    promise in the fast lane, where `harness/stack.py` boots exactly that
    deployment on purpose.

    What they cannot do is run against somebody else's Lemma, because a healthy
    deployment is by definition not in the state under test — and nobody is going
    to break dev so a scenario can watch. Before this mark they reported as
    skips there, which put three permanent entries on a skip list people are
    meant to read. A skip is supposed to mean "this could have run and did not".

    So they are deselected rather than skipped when the suite did not boot the
    target. `why` is for the reader of the source, not for a report: nothing is
    reported, which is the point.
    """
    return pytest.mark.stack_lane(why)
