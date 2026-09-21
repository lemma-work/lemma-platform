"""What the pod-membership statements ask about the person, not just the rows.

Membership is the authority gate on the inbound path: `matches_user` asks
`get_user_pod_ids`, and a sender who belongs to none of a surface's pods gets
the access-denied reply instead of an agent run. So a predicate missing here
does not fail -- it grants.

Asserted on the compiled SQL for the reason the routing statements next door
are: dropping a liveness predicate returns *more* rows, which reads as ordinary
success everywhere downstream.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _CapturingSession:
    """A session that answers nothing and keeps the statement it was asked."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _EmptyResult()

    async def scalar(self, statement):
        self.statements.append(statement)


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _Uow:
    def __init__(self, session) -> None:
        self.session = session


def _compiled(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


async def _statement_for(call) -> str:
    session = _CapturingSession()
    await call(SqlAlchemySurfaceRoutingResolutionAdapter(_Uow(session)))
    assert len(session.statements) == 1
    return _compiled(session.statements[0])


async def test_pod_membership_is_only_answered_for_a_live_account():
    sql = await _statement_for(lambda adapter: adapter.get_user_pod_ids(uuid4()))

    assert "JOIN users" in sql, (
        "the join to users is what lets membership ask whether the member is "
        "still here; without it the answer stops at organization_members"
    )
    assert "users.is_active IS true" in sql
    assert "users.is_deleted IS false" in sql


async def test_a_pod_member_id_is_only_answered_for_a_live_account():
    sql = await _statement_for(
        lambda adapter: adapter.get_pod_member_id(uuid4(), uuid4())
    )

    assert "JOIN users" in sql
    assert "users.is_active IS true" in sql
    assert "users.is_deleted IS false" in sql


async def test_membership_does_not_also_require_a_verified_account():
    """The predicate is the weakest one every live-user lookup shares.

    Identity's email lookup does not ask for `is_verified`, so requiring it here
    would refuse membership to somebody a fresh resolution had just accepted --
    a person who could be identified but never reach a pod.
    """
    sql = await _statement_for(lambda adapter: adapter.get_user_pod_ids(uuid4()))

    assert "is_verified" not in sql
