"""Removing a member must invalidate their role snapshot, and do it after commit.

The ordering is the point, not the call. Invalidating inside the transaction
looks correct and is not: between the delete and the caller's commit, a
concurrent request for that user can miss the cache, rebuild the snapshot from
rows that still grant access, and store it again. The removed member then keeps
pod access until the TTL expires -- exactly what `revoke_member_authorization`
exists to prevent.

Nothing covered this before, so the fix that introduced `after_commit` here also
made the invalidation conditional on a commit happening at all. Both directions
are asserted.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.pod.services import pod_role_service as module


class _FakeUow:
    """Just enough unit of work to record and fire after-commit callbacks."""

    def __init__(self) -> None:
        self.session = SimpleNamespace()
        self._callbacks: list = []

    def after_commit(self, callback) -> None:
        self._callbacks.append(callback)

    async def commit(self) -> None:
        for callback in self._callbacks:
            await callback()
        self._callbacks.clear()


@pytest.fixture
def revoked(monkeypatch):
    """Drive `revoke_member_authorization` with every collaborator stubbed."""
    invalidated: list = []

    async def _invalidate(*, user_id):
        invalidated.append(user_id)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(module, "invalidate_role_snapshot_cache", _invalidate)
    monkeypatch.setattr(module, "delete_grantee_grants", _noop)
    monkeypatch.setattr(
        module, "create_authorization_data_service",
        lambda uow: SimpleNamespace(
            session=uow.session, delete_principal_role_assignments=_noop
        ),
    )
    monkeypatch.setattr(module, "PodRepository", lambda uow: SimpleNamespace())
    monkeypatch.setattr(module, "PodRoleQueryRepository", lambda uow: SimpleNamespace())

    uow = _FakeUow()
    return SimpleNamespace(
        uow=uow, invalidated=invalidated, service=module.PodRoleService(uow)
    )


@pytest.mark.asyncio
async def test_the_snapshot_is_not_invalidated_before_the_commit(revoked) -> None:
    """Invalidating early lets a concurrent reader repopulate the old snapshot."""
    await revoked.service.revoke_member_authorization(
        pod_id=uuid4(), pod_member_id=uuid4(), user_id=uuid4()
    )
    assert revoked.invalidated == [], (
        "the role snapshot was invalidated inside the transaction; a concurrent "
        "request can now rebuild it from rows that still grant access"
    )


@pytest.mark.asyncio
async def test_the_snapshot_is_invalidated_once_the_commit_lands(revoked) -> None:
    """And it must still actually happen -- deferring is not dropping."""
    user_id = uuid4()
    await revoked.service.revoke_member_authorization(
        pod_id=uuid4(), pod_member_id=uuid4(), user_id=user_id
    )
    await revoked.uow.commit()
    assert revoked.invalidated == [user_id]
