"""Whose saved login a run may use.

The previous version declared `web_login.use`, made `web_login.manage`
destructive, put both in the role templates, and never called `require()` --
so taking the permission away from a role changed nothing at all. These are the
tests that would have failed then.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization.context import (
    Actor,
    Assistant,
    AuthorizationDecision,
    Context,
    Person,
    Workload,
    WorkloadKind,
)
from app.core.domain.errors import DomainError
from app.modules.web_login.services.resolution import (
    WebLoginAccessDenied,
    resolve_owner,
)


class _RecordingAuthorizer:
    """Answers every check one way and records which permissions were asked."""

    def __init__(self, *, allow: bool) -> None:
        self.required: list[str] = []
        self._allow = allow

    async def authorize(self, ctx, permission_id, resource=None):
        self.required.append(permission_id)
        return AuthorizationDecision(self._allow, "TEST", permission_id, resource)

    async def accessible_resource_ids(self, *args, **kwargs):
        return frozenset()


def _ctx(actor: Actor, *, user_id=None, allow=True) -> Context:
    return Context(
        actor=actor,
        actor_id="actor",
        user_id=user_id,
        pod_id=uuid4(),
        authorizer=_RecordingAuthorizer(allow=allow),
    )


def _person(user_id) -> Context:
    return _ctx(Person(user_id=user_id), user_id=user_id)


def _agent_for(owner, *, user_id=None, allow=True) -> Context:
    """A named agent acting for ``owner``."""
    agent = Workload(kind=WorkloadKind.AGENT, workload_id=uuid4(), for_user_id=owner)
    return _ctx(agent, user_id=user_id, allow=allow)


async def test_a_person_acting_for_themselves_needs_no_grant() -> None:
    me = uuid4()
    ctx = _person(me)
    assert await resolve_owner(auth_ctx=ctx) == me
    assert ctx.authorizer.required == []


async def test_a_workload_must_hold_the_permission() -> None:
    """The check that did not exist before."""
    owner = uuid4()
    ctx = _agent_for(owner, user_id=None)
    assert await resolve_owner(auth_ctx=ctx) == owner
    assert ctx.authorizer.required == ["web_login.use"]


async def test_a_workload_without_the_permission_is_refused() -> None:
    ctx = _agent_for(uuid4(), allow=False)
    with pytest.raises(DomainError):
        await resolve_owner(auth_ctx=ctx)


async def test_the_pod_default_agent_mirrors_its_person() -> None:
    """It carries the person's own authority, so it needs no separate grant --
    the same early return `account_resolution_service` makes."""
    owner = uuid4()
    ctx = _ctx(Assistant(user_id=owner))
    assert await resolve_owner(auth_ctx=ctx) == owner
    assert ctx.authorizer.required == []


async def test_naming_somebody_else_is_refused_rather_than_ignored() -> None:
    """A saved session is one human's identity at a site. Lending it would mean
    that site seeing actions it attributes to the owner, with nothing at its end
    able to tell the difference."""
    ctx = _person(uuid4())
    with pytest.raises(WebLoginAccessDenied):
        await resolve_owner(auth_ctx=ctx, requested_user_id=uuid4())


async def test_no_context_means_no_login() -> None:
    with pytest.raises(WebLoginAccessDenied):
        await resolve_owner(auth_ctx=None)


async def test_a_context_with_nobody_behind_it_is_refused() -> None:
    with pytest.raises(WebLoginAccessDenied):
        await resolve_owner(
            auth_ctx=_ctx(Workload(kind=WorkloadKind.FUNCTION, workload_id=uuid4()))
        )
