"""Another pod, asking this one: Public reads here, its grants, and nothing else.

The paths that need no grant lookup are pinned here, with no database. What a
grant to ``POD:A`` unlocks is held against a real database in the agent
module's pod-ask e2e tests, where the grant rows exist.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.pod_principal import (
    POD_PRINCIPAL_TYPE,
    build_pod_context,
    pod_principal,
)
from app.core.authorization.sql_actions import allowed_actions_expr

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


def _projected_sql(ctx: Context, *, with_pod_column: bool) -> str:
    rows = Table(
        "resources",
        MetaData(),
        Column("id", PGUUID(as_uuid=True)),
        Column("pod_id", PGUUID(as_uuid=True)),
        Column("visibility", String),
    )
    expression = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.FOLDER,
        resource_id_col=rows.c.id,
        pod_id_col=rows.c.pod_id if with_pod_column else None,
        visibility_col=rows.c.visibility,
    )
    return str(
        expression.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_the_row_projection_matches_its_grants_inside_its_pod():
    here, asker = uuid4(), uuid4()

    sql = _projected_sql(_asking(here=here, asker=asker), with_pod_column=True)

    assert f"'{POD_PRINCIPAL_TYPE}'" in sql
    assert asker.hex in sql.replace("-", "")
    assert here.hex in sql.replace("-", "")
    assert "resources.visibility = 'PERSONAL'" in sql


def test_a_row_whose_pod_cannot_be_told_projects_nothing():
    sql = _projected_sql(_asking(here=uuid4(), asker=uuid4()), with_pod_column=False)

    assert POD_PRINCIPAL_TYPE not in sql
