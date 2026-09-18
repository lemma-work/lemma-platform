"""One stack's cleanup must not reach another stack's sandboxes.

This is a regression test for an incident, not a hypothetical. A developer was
using a dev stack's browser pane while an e2e suite ran on the same Docker
daemon. The e2e harness swept by `managed-by=lemma-workspace` -- a label every
Lemma sandbox carries, not just the ones it made -- and deleted the dev
stack's container and its workspace volume. Then again. The sandbox was
destroyed and recreated four times in ninety seconds, some instances living
two seconds, and the person watched the page they were typing into go black
each time.

So the property is: the filters the harness deletes by must not match an
object another stack created. Asserted against the filter strings themselves,
because those are what is handed to `docker rm` -- a test that mocked Docker
would agree with whatever filters it was given.
"""

from __future__ import annotations

import pytest

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
    """Every set must name something only this harness creates -- either its
    own `lemma.e2e` marker or its owner tag. A set with neither is a sweep of
    the whole machine."""
    for filters in sweep_filter_sets(sandboxes_only=sandboxes_only):
        joined = " ".join(filters)
        assert "lemma.e2e=true" in joined or _OWNER_FILTER in joined, (
            f"{filters} is not scoped to this run"
        )


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
