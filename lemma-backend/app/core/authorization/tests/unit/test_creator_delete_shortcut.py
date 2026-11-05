"""A creator may delete what they made without the role permission -- while they
belong to the pod.

`created_by` outlives a removal from the pod. The delete dependency's creator
shortcut must not: the delete handlers happen to re-check today, but the
dependency is the one place that says who may delete, and it must not vouch for
someone who is no longer there.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization.context import (
    AuthorizationDecision,
    Context,
    Person,
    PrincipalRef,
    ResourceType,
)
from app.core.authorization.dependencies import require_resource_admin_or_creator
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError

POD_ID = uuid4()
AGENT_ID = uuid4()


class _DenyingAuthorizer:
    """No role permission: whatever passes, passes through the shortcut."""

    async def authorize(self, ctx, permission_id, resource):
        return AuthorizationDecision(
            allowed=False,
            reason_code="NOT_A_POD_MEMBER",
            permission_id=permission_id,
            resource=resource,
        )


class _Session:
    """Answers the one query the shortcut makes: who created the resource."""

    def __init__(self, creator_user_id):
        self._creator_user_id = creator_user_id

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self._creator_user_id)


class _Uow:
    def __init__(self, creator_user_id):
        self.session = _Session(creator_user_id)

    async def commit(self):
        return None


def _ctx(*, is_pod_member: bool) -> Context:
    user_id = uuid4()
    principal_refs = {PrincipalRef("ORG_MEMBER", uuid4())}
    if is_pod_member:
        principal_refs.add(PrincipalRef("POD_MEMBER", uuid4()))
    return Context(
        actor=Person(user_id=user_id),
        actor_id=str(user_id),
        user_id=user_id,
        pod_id=POD_ID,
        principal_refs=frozenset(principal_refs),
        authorizer=_DenyingAuthorizer(),
    )


async def _delete(ctx: Context) -> None:
    dependency = require_resource_admin_or_creator(
        Permissions.AGENT_DELETE,
        resource_type=ResourceType.AGENT,
        id_param="agent_id",
    ).dependency
    request = SimpleNamespace(path_params={"agent_id": str(AGENT_ID)})
    await dependency(request, ctx, _Uow(creator_user_id=ctx.user_id))


@pytest.mark.asyncio
async def test_a_member_may_delete_what_they_created():
    await _delete(_ctx(is_pod_member=True))


@pytest.mark.asyncio
async def test_a_creator_removed_from_the_pod_may_not():
    with pytest.raises(DomainError) as exc:
        await _delete(_ctx(is_pod_member=False))

    assert exc.value.status_code == 403
