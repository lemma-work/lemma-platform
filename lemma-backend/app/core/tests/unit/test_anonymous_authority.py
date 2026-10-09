"""Somebody outside a pod may read what that pod marked Public, and nothing else.

A run answering a stranger in a group authorizes as nobody. The authorizer has
always answered an anonymous actor with Public reads; what is new is the pin to
one pod, because the stranger is talking to *this* pod's bot -- a Public page in
another pod is not its to quote. The row-level projection has to agree with the
single-resource check, or a listing and a read would disagree about what the
stranger can see.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core.authorization.anonymous import (
    build_anonymous_context,
    build_outsider_context,
)
from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import allowed_actions_expr

pytestmark = pytest.mark.unit


def _outsider(pod_id: UUID) -> Context:
    # No session: every decision below is answered from the resource reference,
    # which carries its visibility, so nothing is hydrated from the database.
    return build_anonymous_context(
        session=None,  # type: ignore[arg-type]
        pod_id=pod_id,
        organization_id=uuid4(),
        actor_id="outsider:test",
    )


def _folder(pod_id: UUID | None, visibility: ResourceVisibility) -> ResourceRef:
    return ResourceRef(
        resource_type=ResourceType.FOLDER,
        resource_id=uuid4(),
        pod_id=pod_id,
        visibility=visibility,
    )


def test_the_context_is_anonymous_and_holds_nothing():
    pod_id = uuid4()
    ctx = _outsider(pod_id)

    assert ctx.actor_type is ActorType.ANONYMOUS
    assert ctx.user_id is None
    assert ctx.pod_id == pod_id
    assert not ctx.principal_refs
    assert not ctx.permission_ids


async def test_it_reads_what_its_own_pod_marked_public():
    pod_id = uuid4()
    ctx = _outsider(pod_id)

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(pod_id, ResourceVisibility.PUBLIC)
    )

    assert decision.allowed


async def test_public_in_another_pod_is_not_public_here():
    ctx = _outsider(uuid4())

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(uuid4(), ResourceVisibility.PUBLIC)
    )

    assert not decision.allowed


async def test_a_public_thing_with_no_pod_is_not_this_pods_to_show():
    ctx = _outsider(uuid4())

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(None, ResourceVisibility.PUBLIC)
    )

    assert not decision.allowed


@pytest.mark.parametrize(
    "visibility",
    [
        ResourceVisibility.POD,
        ResourceVisibility.PERSONAL,
        ResourceVisibility.RESTRICTED,
    ],
)
async def test_nothing_short_of_public_is_readable(visibility):
    pod_id = uuid4()
    ctx = _outsider(pod_id)

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(pod_id, visibility)
    )

    assert not decision.allowed


async def test_it_can_never_write_even_what_is_public():
    pod_id = uuid4()
    ctx = _outsider(pod_id)

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_WRITE, _folder(pod_id, ResourceVisibility.PUBLIC)
    )

    assert not decision.allowed


def _rows() -> Table:
    return Table(
        "resources",
        MetaData(),
        Column("id", PGUUID(as_uuid=True)),
        Column("pod_id", PGUUID(as_uuid=True)),
        Column("visibility", String),
    )


def _projected_sql(ctx: Context, *, with_pod_column: bool) -> str:
    rows = _rows()
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


def test_the_row_projection_pins_to_the_same_pod():
    pod_id = uuid4()

    sql = _projected_sql(_outsider(pod_id), with_pod_column=True)

    assert "resources.visibility = 'PUBLIC'" in sql
    assert "resources.pod_id" in sql
    assert pod_id.hex in sql.replace("-", "")


def test_a_row_whose_pod_cannot_be_told_projects_nothing():
    """No pod column means no way to keep the pin, so nothing is readable."""
    sql = _projected_sql(_outsider(uuid4()), with_pod_column=False)

    assert "PUBLIC" not in sql


# -- a contact: the same reach, with a name -----------------------------------


def _contact(pod_id: UUID) -> Context:
    return build_outsider_context(
        session=None,  # type: ignore[arg-type]
        pod_id=pod_id,
        organization_id=uuid4(),
        contact_id=uuid4(),
    )


def test_a_contact_is_named_and_holds_nothing():
    ctx = _contact(uuid4())

    assert ctx.actor_type is ActorType.CONTACT
    assert ctx.actor_id == f"contact:{ctx.contact_id}"
    assert ctx.is_outsider and not ctx.is_authenticated
    assert not ctx.principal_refs and not ctx.permission_ids


def test_without_a_contact_the_outsider_is_anonymous_and_must_be_named():
    pod_id = uuid4()

    ctx = build_outsider_context(
        session=None,  # type: ignore[arg-type]
        pod_id=pod_id,
        organization_id=None,
        contact_id=None,
        actor_id="visitor:1",
    )

    assert ctx.actor_type is ActorType.ANONYMOUS and ctx.contact_id is None
    with pytest.raises(ValueError):
        build_outsider_context(
            session=None,  # type: ignore[arg-type]
            pod_id=pod_id,
            organization_id=None,
            contact_id=None,
        )


async def test_a_contact_reads_public_in_its_pod_and_nothing_else():
    pod_id = uuid4()
    ctx = _contact(pod_id)

    public_here = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(pod_id, ResourceVisibility.PUBLIC)
    )
    public_elsewhere = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(uuid4(), ResourceVisibility.PUBLIC)
    )
    pod_only = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, _folder(pod_id, ResourceVisibility.POD)
    )

    assert public_here.allowed
    assert not public_elsewhere.allowed
    assert not pod_only.allowed


@pytest.mark.parametrize("builder", [_outsider, _contact])
def test_an_outsider_is_never_a_member_of_the_pod(builder):
    from app.core.authorization.dependencies import assert_pod_membership
    from app.core.domain.errors import DomainError

    with pytest.raises(DomainError):
        assert_pod_membership(builder(uuid4()))
