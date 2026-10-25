"""The per-request check and the SQL listings answer the same question the same way.

Authorization is decided in two places: ``Authorizer.authorize`` for one
request, and ``allowed_actions_expr`` for every row of a listing or search. They
must agree. Where the listing is wider, a row offers an action that 403s, or a
search returns what a read refuses (H11: an agent's search reached a RESTRICTED
folder its person could not open); where it is narrower, a resource the caller
may open vanishes. Each disagreement found by the audit (H11, M4, H12) was a
case one of them handled and the other did not.

So instead of naming cases, this builds random pods -- members with random
roles, people who left, the assistant, named agents acting for any of them,
resources of every visibility and owner, grants to members, roles and agents --
and asserts the two agree on every (actor, action, resource). Seeded, so a
failure prints the seed that reproduces it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import column, select, values
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy import String
from starlette import status

from app.core.authorization.authorizer import Authorizer
from app.core.authorization.context import (
    Assistant,
    Context,
    Person,
    PrincipalRef,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
    Workload,
    WorkloadKind,
)
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import (
    ORG_OWNER_PERMISSIONS,
    POD_ADMIN_PERMISSIONS,
    POD_EDITOR_PERMISSIONS,
    POD_USER_PERMISSIONS,
    POD_VIEWER_PERMISSIONS,
)
from app.core.authorization.sql_actions import (
    actions_for_resource,
    allowed_actions_expr,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

WORLDS = 120
TYPE = ResourceType.DATASTORE_TABLE
ACTIONS = actions_for_resource(TYPE)
ROLES = {
    "none": frozenset(),
    "viewer": POD_VIEWER_PERMISSIONS,
    "user": POD_USER_PERMISSIONS,
    "editor": POD_EDITOR_PERMISSIONS,
    "admin": POD_ADMIN_PERMISSIONS,
    "org-owner": ORG_OWNER_PERMISSIONS,
}
VISIBILITIES = [v.value for v in ResourceVisibility]


@dataclass
class World:
    seed: int
    ctx: Context
    resources: list[ResourceRef]
    grants: list[ResourcePermissionGrantModel]
    describe: str


def _person_half(rng: random.Random, user_id: UUID) -> dict:
    """A person's own principals and permissions: member or not, some role."""
    member = rng.random() < 0.75
    role = rng.choice(list(ROLES))
    if not member and role != "org-owner":
        role = "none"
    refs = {PrincipalRef("ROLE", uuid4())}
    if member:
        refs.add(PrincipalRef("POD_MEMBER", uuid4()))
    return {
        "refs": frozenset(refs),
        "permissions": ROLES[role],
        "label": f"{'member' if member else 'left'}/{role}",
    }


def _world(seed: int, pod_id: UUID, authorizer: Authorizer) -> World:
    rng = random.Random(seed)
    user_id = uuid4()
    other_id = uuid4()
    person = _person_half(rng, user_id)
    kind = rng.choice(["person", "assistant", "agent"])
    workload_refs: frozenset[PrincipalRef] = frozenset()
    if kind == "person":
        actor = Person(user_id=user_id)
    elif kind == "assistant":
        actor = Assistant(user_id=user_id)
    else:
        agent_id = uuid4()
        workload_refs = frozenset({PrincipalRef("AGENT", agent_id)})
        actor = Workload(
            kind=WorkloadKind.AGENT,
            workload_id=agent_id,
            for_user_id=user_id,
            principal_refs=workload_refs,
        )
    ctx = Context(
        actor=actor,
        actor_id=str(user_id),
        authorizer=authorizer,
        user_id=user_id,
        pod_id=pod_id,
        permission_ids=person["permissions"],
        principal_refs=person["refs"] | workload_refs,
        grant_principal_sets=(person["refs"],)
        if kind != "agent"
        else (person["refs"], workload_refs),
        invoker_principal_refs=person["refs"] if kind == "agent" else frozenset(),
    )

    resources: list[ResourceRef] = []
    grants: list[ResourcePermissionGrantModel] = []
    grantees = list(person["refs"]) + list(workload_refs)
    for _ in range(6):
        resource = ResourceRef(
            resource_type=TYPE,
            resource_id=uuid4(),
            pod_id=pod_id,
            owner_user_id=rng.choice([user_id, other_id, None]),
            visibility=ResourceVisibility(rng.choice(VISIBILITIES)),
        )
        resources.append(resource)
        for grantee in grantees:
            for action in ACTIONS:
                if rng.random() < 0.25:
                    grants.append(
                        ResourcePermissionGrantModel(
                            pod_id=pod_id,
                            resource_type=TYPE.value,
                            resource_id=resource.resource_id,
                            grantee_type=grantee.type,
                            grantee_id=grantee.id,
                            permission_id=action,
                        )
                    )
    return World(seed, ctx, resources, grants, f"{kind} ({person['label']})")


async def _projected(session, world: World) -> dict[UUID, set[str]]:
    rows = values(
        column("id", PgUUID),
        column("pod_id", PgUUID),
        column("owner_user_id", PgUUID),
        column("visibility", String),
        name="resources",
    ).data(
        [
            (r.resource_id, r.pod_id, r.owner_user_id, r.visibility.value)
            for r in world.resources
        ]
    )
    expr = allowed_actions_expr(
        ctx=world.ctx,
        resource_type=TYPE,
        resource_id_col=rows.c.id,
        pod_id_col=rows.c.pod_id,
        owner_user_id_col=rows.c.owner_user_id,
        visibility_col=rows.c.visibility,
    )
    result = await session.execute(select(rows.c.id, expr))
    return {row[0]: set(row[1] or ()) for row in result}


async def _decided(authorizer: Authorizer, world: World) -> dict[UUID, set[str]]:
    decided: dict[UUID, set[str]] = {}
    for resource in world.resources:
        allowed = set()
        for action in ACTIONS:
            decision = await authorizer.authorize(world.ctx, action, resource)
            if decision.allowed:
                allowed.add(action)
        decided[resource.resource_id] = allowed
    return decided


async def test_the_check_and_the_listing_agree_on_every_world(
    authenticated_client: AsyncClient, fixed_test_org, db_session
):
    response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Agreement {uuid4().hex[:8]}",
            "description": "ladder and projection agree",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod_id = UUID(response.json()["id"])

    disagreements: list[str] = []
    for seed in range(WORLDS):
        authorizer = Authorizer(db_session)
        world = _world(seed, pod_id, authorizer)
        db_session.add_all(world.grants)
        await db_session.flush()
        try:
            projected = await _projected(db_session, world)
            decided = await _decided(authorizer, world)
        finally:
            for grant in world.grants:
                await db_session.delete(grant)
            await db_session.flush()
        for resource in world.resources:
            rid = resource.resource_id
            if projected[rid] != decided[rid]:
                disagreements.append(
                    f"seed {seed} {world.describe} on {resource.visibility.value} "
                    f"{'own' if resource.owner_user_id == world.ctx.user_id else 'other'}"
                    f": listing {sorted(projected[rid] - decided[rid])} wider, "
                    f"{sorted(decided[rid] - projected[rid])} narrower"
                )
    assert not disagreements, "\n".join(disagreements[:40])
