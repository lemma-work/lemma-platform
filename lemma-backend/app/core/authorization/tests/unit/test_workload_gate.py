"""The pod-scoped workload gate denies delegated workloads but not humans.

Member-management / membership-minting endpoints route through
``reject_delegated_workload_pod`` so a delegated agent cannot confer membership
on the invoking user's behalf, while human admins are unaffected.
"""

from __future__ import annotations

import pytest

from app.core.authorization.context import ActorType, Context
from app.core.authorization.dependencies import reject_delegated_workload_pod
from app.core.domain.errors import DomainError


def _ctx(actor_type: ActorType) -> Context:
    return Context(actor_type=actor_type, actor_id="actor", authorizer=object())


@pytest.mark.asyncio
async def test_pod_workload_gate_denies_delegated_workload():
    dependency = reject_delegated_workload_pod("approve join requests").dependency

    with pytest.raises(DomainError) as exc:
        await dependency(_ctx(ActorType.DELEGATED_USER_WORKLOAD))

    assert exc.value.status_code == 403
    assert exc.value.code == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"


@pytest.mark.asyncio
async def test_pod_workload_gate_allows_human_user():
    dependency = reject_delegated_workload_pod("approve join requests").dependency

    # No raise for a human actor.
    await dependency(_ctx(ActorType.USER))
