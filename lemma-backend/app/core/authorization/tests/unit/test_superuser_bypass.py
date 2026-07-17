"""Pins the superuser bypass wiring in ``AuthorizationDataService.build_user_context``.

Without this wiring, ``users.is_superuser = True`` in the DB is never propagated
to ``Context.is_superuser``, so the bypass at ``Authorizer.authorize`` never
fires and superusers hit ``require(Permissions.ORG_UPDATE, ...)`` with a
"Missing permission org.update" 403 — exactly the symptom the cloud UI sees.

This test verifies:
  1. ``build_user_context`` reads the user row and propagates ``is_superuser`` to
     the returned Context (the no-org/pod fast-path and the cached and the
     re-computed paths).
  2. ``Context.require(Permissions.ORG_UPDATE, ...)`` succeeds without raising
     once ``is_superuser`` is True, even with no org-member role attached.
  3. ``is_superuser=False`` users continue to be denied ``org.update``.

The DB lookup is mocked because this is a unit test; the real DB+Redis path is
covered by the identity e2e suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.core.authorization.context import ResourceRef
from app.core.authorization.service import AuthorizationDataService
from app.modules.identity.infrastructure.models.user_models import User


def _user(*, is_superuser: bool) -> SimpleNamespace:
    """A minimal User row stub — only ``is_superuser`` is read by build_user_context."""
    return SimpleNamespace(id=uuid4(), is_superuser=is_superuser)


def _session_with_user(user: SimpleNamespace | None) -> AsyncMock:
    """AsyncMock session whose ``session.get(User, user_id)`` returns ``user``.

    Other session methods are stubbed so the `_get_org_member` select returns
    ``None`` (user is not a member of the test org), and ``session.execute``
    returns a result object whose ``.scalars().first()`` is None.
    """
    session = AsyncMock()
    session.get = AsyncMock(return_value=user)
    scalars_result = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))
    session.execute = AsyncMock(return_value=scalars_result)
    return session


@pytest.mark.asyncio
async def test_build_user_context_propagates_is_superuser_no_org():
    """No-org/pod fast path: the new user lookup runs and is_superuser lands."""
    user = _user(is_superuser=True)
    session = _session_with_user(user)
    service = AuthorizationDataService(session)

    ctx = await service.build_user_context(user_id=user.id)

    assert ctx.is_superuser is True
    session.get.assert_awaited_once_with(User, user.id)


@pytest.mark.asyncio
async def test_build_user_context_is_false_when_user_not_superuser():
    user = _user(is_superuser=False)
    session = _session_with_user(user)
    service = AuthorizationDataService(session)

    ctx = await service.build_user_context(user_id=user.id)

    assert ctx.is_superuser is False


@pytest.mark.asyncio
async def test_build_user_context_is_false_when_user_row_missing():
    """Defensive: a deleted user row should not silently grant superuser bypass."""
    session = _session_with_user(None)
    service = AuthorizationDataService(session)

    ctx = await service.build_user_context(user_id=uuid4())

    assert ctx.is_superuser is False


@pytest.mark.asyncio
async def test_superuser_bypasses_org_update_require():
    """The end-to-end bypass: require(ORG_UPDATE) does NOT raise for a superuser.

    Even though the superuser has no org-member principal_refs / permission_ids
    attached, the bypass at ``Authorizer.authorize`` short-circuits to
    ``Decision(allowed=True, reason="SUPERUSER")`` and ``require`` returns
    silently.
    """
    user = _user(is_superuser=True)
    session = _session_with_user(user)
    service = AuthorizationDataService(session)
    org_id = uuid4()

    ctx = await service.build_user_context(user_id=user.id, organization_id=org_id)

    # No exception — the bypass fires.
    await ctx.require(
        Permissions.ORG_UPDATE,
        ResourceRef.organization(org_id),
    )
    assert ctx.is_superuser is True


@pytest.mark.asyncio
async def test_non_superuser_still_denied_org_update():
    """Sanity: the bypass does NOT leak to non-superusers."""
    user = _user(is_superuser=False)
    session = _session_with_user(user)
    service = AuthorizationDataService(session)
    org_id = uuid4()

    ctx = await service.build_user_context(user_id=user.id, organization_id=org_id)

    with pytest.raises(Exception) as exc:
        await ctx.require(
            Permissions.ORG_UPDATE,
            ResourceRef.organization(org_id),
        )
    # DomainError or HTTPException both carry the permission id; we just need
    # the call to fail with the right shape, not pass.
    assert "org.update" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_delegated_workload_context_forwards_is_superuser():
    """Superuser flag must survive the non-default delegation merge.

    ``build_delegated_workload_context`` builds a user context (now carrying
    ``is_superuser``), then merges it with a workload context into a fresh
    ``Context(...)``. Without the explicit forward in the merge, the field
    defaults to ``False`` and the bypass silently dies for delegated
    workloads (functions, subagents) — which is the same root symptom the
    PR fixes, half-wired.
    """
    from app.core.authorization.context import ActorType, Context

    user = _user(is_superuser=True)
    session = _session_with_user(user)
    # build_user_context(user_id, pod_id=…) looks up Pod to find its
    # organization. Stub that out so the test stays focused on the user-ctx
    # path; the workload side is mocked below.
    org_id = uuid4()
    pod_id = uuid4()
    session.get = AsyncMock(
        side_effect=lambda model, _id: (
            user if model is User else SimpleNamespace(organization_id=org_id)
        )
    )

    service = AuthorizationDataService(session)

    # Stub the workload context builder — we only care about the user-side
    # propagation. Returning a minimal Context keeps the merge arithmetic
    # honest without fanning out to Pod/Role lookups.
    async def fake_workload_context(*, principal_type, principal_id, pod_id, request_id=None):
        return Context(
            actor_type=ActorType.FUNCTION,
            actor_id=f"{principal_type.lower()}:{principal_id}",
            organization_id=None,
                pod_id=pod_id,
                authorizer=AsyncMock(),
                principal_refs=frozenset(),
            )

    service.build_workload_context = fake_workload_context  # type: ignore[assignment]

    ctx = await service.build_delegated_workload_context(
        user_id=user.id,
        principal_type="FUNCTION",
        principal_id=uuid4(),
        pod_id=pod_id,
        is_default_pod_agent=False,
    )

    assert ctx.is_superuser is True
    assert ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
    # The forwarded flag must propagate to ``ctx.require`` so a superuser
    # calling a workload function bypasses org-scoped gates.
    await ctx.require(
        Permissions.ORG_UPDATE,
        ResourceRef.organization(org_id),
    )
