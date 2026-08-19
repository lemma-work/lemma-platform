"""Two organizations created at once must not race on seeding auth_permissions.

AuthorizationDataService.seed_permissions() used to be check-then-insert: read
every existing permission id, then session.add() whatever PERMISSION_DEFINITIONS
had that the read didn't. Called from _ensure_system_roles on every org (and
pod) creation, against one global, unscoped auth_permissions table -- so two
organizations created at genuinely the same moment, while a definition is
still missing, can both decide it's missing and both try to insert it. One
loses to `duplicate key value violates unique constraint "auth_permissions_
pkey"` and the request 500s.

Confirmed twice while parallelizing actor creation elsewhere in this e2e suite
(branch lemma/e2e-fast-and-green): a temporary asyncio.gather()'d two-org
repro, and separately for real on the `surfaces` shard while parallelizing a
two-org test helper. Fixed with a bulk `INSERT ... ON CONFLICT DO NOTHING`,
the same pattern already used a few lines below for role_permissions.
"""

from __future__ import annotations

import asyncio

from fastapi import status
from sqlalchemy import delete

from app.core.authorization.models import AuthPermissionModel
from app.core.authorization.permissions import Permissions
from app.modules.test_support.e2e_authz import auth_headers, signup_user

import pytest

pytestmark = [pytest.mark.e2e]


async def _create_org(client, headers, name: str):
    return await client.post("/organizations", json={"name": name}, headers=headers)


async def test_two_organizations_created_at_once_do_not_race_on_permission_seeding(
    async_client, db_session
):
    # A missing row is the precondition the race needs. Deleting one here is
    # safe: e2e tests inside one xdist worker's database run one at a time --
    # nothing else touches this table concurrently with this test -- and the
    # upsert this proves puts it straight back before the test returns.
    await db_session.execute(
        delete(AuthPermissionModel).where(AuthPermissionModel.id == Permissions.ORG_READ)
    )
    await db_session.commit()

    owner_a = await signup_user(async_client, "org-race-a")
    owner_b = await signup_user(async_client, "org-race-b")

    responses = await asyncio.gather(
        _create_org(async_client, auth_headers(owner_a), "Race Org A"),
        _create_org(async_client, auth_headers(owner_b), "Race Org B"),
    )

    for response in responses:
        assert response.status_code == status.HTTP_201_CREATED, response.text

    # The definition this test deleted really did come back -- not just that
    # both requests happened to 201 for an unrelated reason.
    restored = await db_session.get(AuthPermissionModel, Permissions.ORG_READ)
    assert restored is not None
