"""Another pod, asking this one: Public reads here, its grants, and nothing else.

The paths that need no grant lookup are pinned here, with no database. What a
grant to ``POD:A`` unlocks -- decided by the authorizer and projected row by row
in SQL -- is held against real grant rows in
``agent/tests/e2e/test_pod_link_authority_e2e.py``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.permissions import Permissions, pod_principal_may_hold
from app.core.authorization.pod_principal import (
    POD_PRINCIPAL_TYPE,
    build_pod_context,
    pod_principal,
)

pytestmark = pytest.mark.unit


def _asking(*, here: UUID, asker: UUID) -> Context:
    # No session: the decisions below are answered from the resource reference,
    # which carries its visibility, so nothing is read from a database.
    return build_pod_context(
        session=None,  # type: ignore[arg-type]
        pod_id=here,
        organization_id=uuid4(),
        asking_pod_id=asker,
    )


def _folder(pod_id: UUID | None, visibility: ResourceVisibility) -> ResourceRef:
    return ResourceRef(
        resource_type=ResourceType.FOLDER,
        resource_id=uuid4(),
        pod_id=pod_id,
        visibility=visibility,
    )


def test_the_context_is_the_asking_pod_and_nobody_else():
    here, asker = uuid4(), uuid4()
    ctx = _asking(here=here, asker=asker)

    assert ctx.actor_type is ActorType.POD
    assert ctx.user_id is None
    assert ctx.pod_id == here
    assert ctx.principal_refs == {pod_principal(asker)}
    assert pod_principal(asker).type == POD_PRINCIPAL_TYPE
    assert not ctx.permission_ids
    assert not ctx.role_names


async def test_it_reads_what_this_pod_marked_public():
    here = uuid4()
    ctx = _asking(here=here, asker=uuid4())

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(here, ResourceVisibility.PUBLIC)
    )

    assert decision.allowed
    assert decision.reason_code == "PUBLIC_RESOURCE"


async def test_nothing_in_another_pod_whatever_it_is_marked():
    ctx = _asking(here=uuid4(), asker=uuid4())

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(uuid4(), ResourceVisibility.PUBLIC)
    )

    assert not decision.allowed
    assert decision.reason_code == "POD_SCOPE_MISMATCH"


async def test_nothing_without_a_resource_to_have_been_granted():
    ctx = _asking(here=uuid4(), asker=uuid4())

    decision = await ctx.authorizer.authorize(ctx, Permissions.FOLDER_READ, None)

    assert not decision.allowed


async def test_a_personal_thing_is_never_another_pods():
    here = uuid4()
    ctx = _asking(here=here, asker=uuid4())

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(here, ResourceVisibility.PERSONAL)
    )

    assert not decision.allowed


@pytest.mark.parametrize(
    "permission_id",
    [
        Permissions.DATASTORE_RECORD_WRITE,
        Permissions.DATASTORE_TABLE_DELETE,
        Permissions.FOLDER_WRITE,
    ],
)
async def test_nothing_but_reading_whatever_a_grant_says(permission_id):
    """Refused before any grant is read: no session, so a lookup would raise."""
    here = uuid4()
    ctx = _asking(here=here, asker=uuid4())

    decision = await ctx.authorizer.authorize(
        ctx, permission_id, _folder(here, ResourceVisibility.PUBLIC)
    )

    assert not decision.allowed
    assert decision.reason_code == "POD_READ_ONLY"


@pytest.mark.parametrize(
    ("permission_id", "may_hold"),
    [
        (Permissions.DATASTORE_TABLE_READ, True),
        (Permissions.DATASTORE_RECORD_READ, True),
        (Permissions.FOLDER_READ, True),
        (Permissions.AGENT_EXECUTE, True),
        (Permissions.DATASTORE_RECORD_WRITE, False),
        (Permissions.FOLDER_WRITE, False),
        (Permissions.POD_MEMBER_MANAGE, False),
    ],
)
def test_a_pod_may_hold_reads_and_the_link_and_nothing_else(permission_id, may_hold):
    assert pod_principal_may_hold(permission_id) is may_hold
