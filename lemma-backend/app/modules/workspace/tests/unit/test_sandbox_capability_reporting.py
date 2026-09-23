"""What `/health/capabilities` says about sandboxes, and when.

The startup probe proves a provider object can be constructed -- for
`lemma_local`, that the bridge executable exists on disk. It never touches the
guest. So during an outage in which every file listing spun for five minutes
and returned a 500, the capability endpoint reported `ready` the whole time.
Operations now move it, because what matters is whether the thing callers
actually do is working.
"""

from __future__ import annotations

import pytest

from app import sandbox_health

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_capability():
    before = sandbox_health.sandbox_capability()
    yield
    sandbox_health._capability.clear()
    sandbox_health._capability.update(before)


def test_an_operation_that_gave_up_is_not_reported_as_ready() -> None:
    sandbox_health._capability.update({"status": "ready", "detail": "provisioned"})

    sandbox_health.record_sandbox_unreachable()

    assert sandbox_health.sandbox_capability()["status"] == "unavailable"


def test_a_later_success_clears_it() -> None:
    sandbox_health._capability.update({"status": "ready", "detail": "provisioned"})
    sandbox_health.record_sandbox_unreachable()

    sandbox_health.record_sandbox_reachable()

    assert sandbox_health.sandbox_capability()["status"] == "ready"


def test_a_misconfiguration_is_not_overwritten_by_a_symptom_of_it() -> None:
    """`needs_setup` names the cause; `unavailable` only names the effect."""
    sandbox_health._capability.update(
        {"status": "needs_setup", "detail": "No Docker Engine socket"}
    )

    sandbox_health.record_sandbox_unreachable()

    assert sandbox_health.sandbox_capability()["status"] == "needs_setup"
    assert "Docker" in sandbox_health.sandbox_capability()["detail"]


def test_success_does_not_invent_readiness_for_an_unconfigured_fabric() -> None:
    sandbox_health._capability.update(
        {"status": "needs_setup", "detail": "No Docker Engine socket"}
    )

    sandbox_health.record_sandbox_reachable()

    assert sandbox_health.sandbox_capability()["status"] == "needs_setup"


def test_the_detail_never_carries_an_exception_message() -> None:
    """`/health/capabilities` is unauthenticated; reasons go to the log."""
    sandbox_health._capability.update({"status": "ready", "detail": "provisioned"})

    sandbox_health.record_sandbox_unreachable()

    detail = sandbox_health.sandbox_capability()["detail"]
    assert "Traceback" not in detail
    assert "Error" not in detail
