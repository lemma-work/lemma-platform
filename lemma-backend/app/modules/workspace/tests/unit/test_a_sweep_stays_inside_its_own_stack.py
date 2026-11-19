"""One stack's cleanup must not reach another stack's sandboxes.

This is a regression test for an incident, not a hypothetical. A developer was
using a dev stack's browser pane while an e2e suite ran on the same Docker
daemon. The e2e harness swept by `managed-by=lemma-workspace` -- a label every
Lemma sandbox carries, not just the ones it made -- and deleted the dev
stack's container and its workspace volume. Then again. The sandbox was
destroyed and recreated four times in ninety seconds, some instances living
two seconds, and the person watched the page they were typing into go black
each time.

It happened again between two e2e runs: the owner tag named the harness, not
the run, so one run's teardown deleted another run's live Postgres, Redis and
sandboxes on the same daemon, several times in an evening.

So the property is: the filters a run deletes by must not match an object
another stack -- or another run -- created, and a run's leftovers are removed
only once that run's process is gone. Asserted against the filter strings themselves,
because those are what is handed to `docker rm` -- a test that mocked Docker
would agree with whatever filters it was given.
"""

from __future__ import annotations

import pytest

from app.core.test_utils import (
    OWNER_LABEL_KEY,
    invocation_owner,
    owned_by_gone,
    owner_is_gone,
)
from app.modules.test_support.e2e_base import E2E_OWNER_TAG, sweep_filter_sets
from app.modules.workspace.providers.base import LABEL_OWNER

pytestmark = pytest.mark.unit

_OWNER_FILTER = f"label={LABEL_OWNER}={E2E_OWNER_TAG}"
_MANAGED_BY = "label=managed-by=lemma-workspace"


@pytest.mark.parametrize("sandboxes_only", [True, False])
def test_every_workspace_filter_names_this_stack(sandboxes_only: bool) -> None:
    """A set that mentions the workspace label must also mention the owner.

    `managed-by=lemma-workspace` on its own is not a scope. Docker's filters
    are conjunctive, so pairing it with the owner is what turns "every Lemma
    sandbox on this machine" into "the ones this harness made".
    """
    for filters in sweep_filter_sets(sandboxes_only=sandboxes_only):
        if _MANAGED_BY in filters:
            assert _OWNER_FILTER in filters, (
                f"{filters} would delete another stack's sandboxes: it selects "
                "every Lemma workspace container on the daemon"
            )


@pytest.mark.parametrize("sandboxes_only", [True, False])
def test_no_filter_set_is_unscoped(sandboxes_only: bool) -> None:
    """Every set must name this run's owner. `lemma.e2e=true` alone is every
    e2e run on the daemon, which is how parallel runs killed each other."""
    run_owner = f"label={OWNER_LABEL_KEY}={invocation_owner()}"
    for filters in sweep_filter_sets(sandboxes_only=sandboxes_only):
        assert _OWNER_FILTER in filters or run_owner in filters, (
            f"{filters} is not scoped to this run"
        )


def test_the_owner_names_this_invocation_not_the_harness() -> None:
    assert E2E_OWNER_TAG.startswith(f"{invocation_owner()}-")
    assert E2E_OWNER_TAG != "lemma-e2e"


def test_two_live_runs_never_sweep_each_other() -> None:
    """Run A's and run B's containers, both processes alive: the sweep of the
    departed takes nothing. (Each run's own sweeps name only its own owner:
    `test_no_filter_set_is_unscoped`.)"""
    rows = [
        ("pg-a", "lemma-e2e-1001"),
        ("ws-a", "lemma-e2e-1001-gw0"),
        ("pg-b", "lemma-e2e-2002"),
        ("ws-b", "lemma-e2e-2002-master"),
    ]

    assert owned_by_gone(rows, alive=lambda pid: True) == []


def test_a_run_whose_process_is_gone_is_cleaned_up_and_only_it() -> None:
    rows = [
        ("pg-dead", "lemma-e2e-1001"),
        ("ws-dead", "lemma-e2e-1001-gw3"),
        ("pg-live", "lemma-e2e-2002"),
        ("legacy", "lemma-e2e"),  # names no pid: left to the age reaper
        ("dev-stack", ""),  # no owner at all: never ours
        ("other", "someone-else-1001"),
    ]

    gone = owned_by_gone(rows, alive=lambda pid: pid != 1001)

    assert gone == ["pg-dead", "ws-dead"]


def test_an_owner_is_gone_only_when_its_pid_is_provably_dead() -> None:
    assert owner_is_gone("lemma-e2e-1001", alive=lambda pid: False)
    assert not owner_is_gone("lemma-e2e-1001", alive=lambda pid: True)
    assert not owner_is_gone("lemma-e2e", alive=lambda pid: False)
    assert not owner_is_gone("lemma-e2e-gw0", alive=lambda pid: False)


def test_a_stack_with_no_tag_stamps_no_owner() -> None:
    """The default has to stay unstamped.

    Every container created before this label existed has no owner, and a
    provider that suddenly required one would stop recognising them -- leaking
    each one forever. An untagged stack behaves exactly as it did.
    """
    from app.modules.workspace.providers.docker import owner_label_for

    assert owner_label_for("") == {}
    assert owner_label_for(None) == {}
    assert owner_label_for("   ") == {}
    assert owner_label_for(E2E_OWNER_TAG) == {LABEL_OWNER: E2E_OWNER_TAG}
