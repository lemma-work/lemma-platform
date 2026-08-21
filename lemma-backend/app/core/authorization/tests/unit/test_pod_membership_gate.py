"""Enumeration stays membership-gated even as reads widen.

Listing endpoints carry no permission dependency — the visibility projection in
``sql_actions`` decides what a caller sees. Once ORGANIZATION and PUBLIC project
read actions for non-members, that projection alone would let anyone holding one
shared link enumerate the pod. ``require_pod_membership`` is the counterweight.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization.context import ActorType, Context, PrincipalRef
from app.core.authorization.dependencies import require_pod_membership
from app.core.domain.errors import DomainError

POD_ID = uuid4()


def _ctx(
    *,
    actor_type: ActorType = ActorType.USER,
    is_pod_member: bool = False,
    role_names: frozenset[str] = frozenset(),
    is_superuser: bool = False,
) -> Context:
    principal_refs = set()
    if is_pod_member:
        principal_refs.add(PrincipalRef("POD_MEMBER", uuid4()))
    return Context(
        actor_type=actor_type,
        actor_id="actor",
        user_id=uuid4(),
        pod_id=POD_ID,
        role_names=frozenset(role_names),
        principal_refs=frozenset(principal_refs),
        is_superuser=is_superuser,
        authorizer=object(),
    )


async def _run(ctx: Context) -> None:
    await require_pod_membership("browse files").dependency(ctx)


@pytest.mark.asyncio
async def test_denies_authenticated_non_member():
    with pytest.raises(DomainError) as exc:
        await _run(_ctx())

    assert exc.value.status_code == 403
    assert exc.value.code == "POD_MEMBERSHIP_REQUIRED"


@pytest.mark.asyncio
async def test_allows_pod_member():
    await _run(_ctx(is_pod_member=True))


@pytest.mark.asyncio
async def test_allows_org_owner_without_membership_row():
    # Org owners hold authority over every pod in their org without necessarily
    # having a pod_member row; gating them would break the pods they own.
    await _run(_ctx(role_names=frozenset({"ORG_OWNER"})))


@pytest.mark.asyncio
async def test_allows_superuser():
    await _run(_ctx(is_superuser=True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_type",
    [
        ActorType.DELEGATED_USER_WORKLOAD,
        ActorType.AGENT,
        ActorType.FUNCTION,
        ActorType.SYSTEM,
    ],
)
async def test_workload_actors_are_not_gated(actor_type: ActorType):
    # Workloads keep the grant-first projection this change never widened, so
    # gating them here would break agents listing files they already may read.
    await _run(_ctx(actor_type=actor_type))
