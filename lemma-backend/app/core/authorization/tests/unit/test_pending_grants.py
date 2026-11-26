"""Holding a grant for a resource that does not exist yet is a grant write.

So it is bounded like one, before anything is written: nobody holds a
permission for a workload that they do not hold themselves, and a workload
holds nothing for anyone.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization.conferral import CONFERRAL_EXCEEDS_HOLDER
from app.core.authorization.context import ResourceType
from app.core.authorization.pending_grants import (
    PendingGrant,
    replace_grantee_pending_grants,
)
from app.core.domain.errors import DomainError

pytestmark = pytest.mark.unit

_GRANT = PendingGrant(
    resource_type=ResourceType.CONNECTOR,
    resource_key="google_calendar:PER_MEMBER",
    permission_ids=("connector.use",),
)


class _Untouched:
    """A session the refusal must never reach."""

    def __getattr__(self, name: str):
        raise AssertionError(f"refused too late: the session's {name} was used")


def _ctx(*, held: frozenset[str], workload: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        is_superuser=False, is_named_workload=workload, permission_ids=held
    )


async def _hold(ctx: SimpleNamespace) -> None:
    await replace_grantee_pending_grants(
        _Untouched(),  # type: ignore[arg-type]
        ctx=ctx,  # type: ignore[arg-type]
        pod_id=uuid4(),
        grantee_type="FUNCTION",
        grantee_id=uuid4(),
        resource_type=ResourceType.CONNECTOR,
        grants=[_GRANT],
        created_by_user_id=None,
    )


async def test_a_permission_the_holder_lacks_is_refused() -> None:
    with pytest.raises(DomainError) as refused:
        await _hold(_ctx(held=frozenset({"datastore.record.read"})))
    assert refused.value.code == CONFERRAL_EXCEEDS_HOLDER


async def test_a_named_workload_holds_nothing() -> None:
    with pytest.raises(DomainError) as refused:
        await _hold(_ctx(held=frozenset({"connector.use"}), workload=True))
    assert refused.value.code == CONFERRAL_EXCEEDS_HOLDER
