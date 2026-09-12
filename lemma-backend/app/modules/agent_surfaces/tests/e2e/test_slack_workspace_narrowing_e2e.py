"""Which surfaces an inbound event selects, and how many rows that costs.

Every routing path read every surface of the platform in the deployment and then
applied its predicate in Python -- a workspace, a receiver's own list, shared
system credentials. The signing-secret lookup is the one that matters most,
because it runs *before* the signature is checked: it is what chooses the secret
to check against, which puts it on the wrong side of `PS-SURF-010`'s "verify
every inbound message before acting on it".

Narrowing in SQL can fail in two directions and only one of them is loud. A
predicate that is too strict returns no candidate, and the request is then
rejected exactly as an unsigned one would be -- which reads as a signature
failure rather than a routing bug. So each narrowing is asserted against the
Python it replaced, on data containing every case that Python distinguished.
"""

from __future__ import annotations

from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select

from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.test_support.query_counting import counted_queries

pytestmark = pytest.mark.e2e

_MINE = f"T{uuid4().hex[:10].upper()}"
_THEIRS = f"T{uuid4().hex[:10].upper()}"


class _Uow:
    def __init__(self, session):
        self.session = session


async def _slack_surface(db_session, pod_id, agent_id, *, workspace: str | None):
    """Insert one ACTIVE Slack surface directly.

    Through the model rather than the API because creating a Slack surface there
    needs a wired platform and credentials, none of which this question involves:
    what is under test is which rows a `team_id` selects.
    """
    surface = AgentSurface(
        id=uuid7(),
        pod_id=UUID(str(pod_id)),
        agent_id=agent_id,
        name=f"slack-{uuid4().hex[:8]}",
        surface_type=SurfacePlatform.SLACK.value,
        mode="DM",
        event_mode="WEBHOOK",
        credential_mode="SYSTEM",
        config={},
        external_workspace_id=workspace,
        status=AgentSurfaceStatus.ACTIVE.value,
    )
    db_session.add(surface)
    await db_session.commit()
    return surface


@pytest.fixture
async def pod_agent_id(db_session, test_pod):
    return (
        await db_session.execute(
            select(AgentModel.id)
            .where(AgentModel.pod_id == UUID(str(test_pod["id"])))
            .limit(1)
        )
    ).scalar_one()


def _python_filter(surfaces, team_id):
    """The predicate that used to run after every Slack surface was hydrated."""
    return [
        surface
        for surface in surfaces
        if str(surface.external_workspace_id or "").strip() == team_id
    ]


async def test_a_team_id_selects_its_own_workspaces_surfaces(
    test_pod, pod_agent_id, db_session
):
    pod_id = test_pod["id"]
    mine = await _slack_surface(db_session, pod_id, pod_agent_id, workspace=_MINE)
    await _slack_surface(db_session, pod_id, pod_agent_id, workspace=_THEIRS)
    # A surface that has not recorded a workspace. `matches_tenant` reads NULL as
    # "any workspace"; this question is "whose workspace is this", and a surface
    # with no answer is not in it. The Python agreed, and the SQL has to as well
    # -- `NULL = 'T123'` is NULL, which is not true.
    await _slack_surface(db_session, pod_id, pod_agent_id, workspace=None)

    repository = SurfaceRepository(_Uow(db_session))
    everything = await repository.list_active_by_type(SurfacePlatform.SLACK.value)
    narrowed = await repository.list_active_for_routing(
        SurfacePlatform.SLACK.value, external_workspace_id=_MINE
    )

    assert [surface.id for surface in narrowed] == [
        surface.id for surface in _python_filter(everything, _MINE)
    ]
    assert [surface.id for surface in narrowed] == [mine.id]


async def test_the_narrowed_read_keeps_the_tiebreak_order(
    test_pod, pod_agent_id, db_session
):
    """`created_at, id` is the documented tiebreak and picks the identity surface.

    Narrowing inherits the ordering rather than restating it, which is the point
    of building both statements from one place -- but inheriting it silently is
    exactly the kind of thing a refactor drops, so it is asserted.
    """
    pod_id = test_pod["id"]
    created = [
        await _slack_surface(db_session, pod_id, pod_agent_id, workspace=_MINE)
        for _ in range(3)
    ]

    repository = SurfaceRepository(_Uow(db_session))
    narrowed = await repository.list_active_for_routing(
        SurfacePlatform.SLACK.value, external_workspace_id=_MINE
    )
    everything = _python_filter(
        await repository.list_active_by_type(SurfacePlatform.SLACK.value), _MINE
    )

    assert [surface.id for surface in narrowed] == [
        surface.id for surface in everything
    ]
    assert len(narrowed) == len(created)


async def test_the_workspace_predicate_reaches_the_database(
    test_pod, pod_agent_id, db_session
):
    """The narrowing has to be in the statement, not after it.

    Asserted on the SQL rather than on the result, because a Python filter
    applied to a full read returns exactly the same rows -- which is what made
    the original defect invisible.
    """
    await _slack_surface(db_session, test_pod["id"], pod_agent_id, workspace=_MINE)

    repository = SurfaceRepository(_Uow(db_session))
    with counted_queries() as statements:
        await repository.list_active_for_routing(
            SurfacePlatform.SLACK.value, external_workspace_id=_MINE
        )

    reads = [text for text in statements if "agent_surfaces" in text]
    assert len(reads) == 1, f"expected one read, got {len(reads)}"
    assert "external_workspace_id" in reads[0], (
        f"the workspace never reached the database: {reads[0]}"
    )


async def test_a_receivers_own_list_selects_only_its_surfaces(
    test_pod, pod_agent_id, db_session
):
    """A native receiver says which surfaces its bot serves, and nothing else is.

    Without this, a custom bot's update can be attributed to a different bot's
    surface. It was a `set` membership test over the whole platform's surfaces;
    it is now the same test, asked as `IN`.
    """
    pod_id = test_pod["id"]
    mine = await _slack_surface(db_session, pod_id, pod_agent_id, workspace=_MINE)
    theirs = await _slack_surface(db_session, pod_id, pod_agent_id, workspace=_THEIRS)

    repository = SurfaceRepository(_Uow(db_session))
    selected = await repository.list_active_for_routing(
        SurfacePlatform.SLACK.value, surface_ids=[mine.id]
    )

    assert [surface.id for surface in selected] == [mine.id]
    assert theirs.id not in {surface.id for surface in selected}


async def test_a_receiver_serving_nothing_selects_nothing(
    test_pod, pod_agent_id, db_session
):
    """An empty list means "none of them", which is what `IN ()` says.

    The distinction that carries the meaning is empty-versus-absent: absent is a
    shared platform webhook and selects the platform, empty is a receiver that
    serves no surface and selects nothing. Collapsing the two would route a
    native receiver's event to every surface on the platform.
    """
    await _slack_surface(db_session, test_pod["id"], pod_agent_id, workspace=_MINE)
    repository = SurfaceRepository(_Uow(db_session))

    assert (
        await repository.list_active_for_routing(
            SurfacePlatform.SLACK.value, surface_ids=[]
        )
        == []
    )
    assert await repository.list_active_for_routing(SurfacePlatform.SLACK.value) != []


async def test_shared_credentials_narrowing_excludes_a_custom_bot(
    test_pod, pod_agent_id, db_session
):
    """A platform-wide webhook arrives on shared credentials, so a custom bot is not it.

    The predicate has a second half -- a surface bound to its own account is
    excluded even on SYSTEM credentials -- which needs a real `accounts` row to
    exercise here and is asserted on the statement itself in
    ``tests/unit/test_surface_routing_sql.py`` instead.
    """
    pod_id = test_pod["id"]
    shared = await _slack_surface(db_session, pod_id, pod_agent_id, workspace=_MINE)
    custom = await _slack_surface(db_session, pod_id, pod_agent_id, workspace=_MINE)
    custom.credential_mode = "CUSTOM"
    await db_session.commit()

    repository = SurfaceRepository(_Uow(db_session))
    selected = await repository.list_active_for_routing(
        SurfacePlatform.SLACK.value,
        external_workspace_id=_MINE,
        system_credentials_only=True,
    )

    assert [surface.id for surface in selected] == [shared.id]
    assert custom.id not in {surface.id for surface in selected}
