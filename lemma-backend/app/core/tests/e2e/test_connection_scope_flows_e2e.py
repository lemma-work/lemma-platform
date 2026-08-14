"""What `make test-connection-scope` actually gates.

The detector had proof it worked (``test_connection_scope_e2e``) and proof the
static checker agreed with it (``scripts/check_session_scope.py``), and between
them they covered no application code at all: the two files carrying the marker
existed to show the detector was not blind. So the required CI gate was green
and empty, and the property it claims to protect -- a pooled connection is never
held across non-database work -- was enforced only by review.

These are request paths, run through the real app, with the monitor armed around
the call under test and nothing else. Setup runs unguarded on purpose: creating
the org, pod and agent goes through the same API, and the e2e harness holds
sessions across blocking fixture work deliberately. Arming across all of it
would report the scaffolding.

Each one fails if someone puts slow work back inside a session on that path.
That is the whole point; there is no assertion here about query counts or
latency, only about what the connection was doing while it was checked out.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.test_support import connection_scope as connection_scope_support
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e, pytest.mark.connection_scope]

# The full request stack, because the point is to watch real routes.
test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
redis_container = e2e_fixtures.redis_container
supertokens_container = e2e_fixtures.supertokens_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
sandbox_reachable_backend = e2e_fixtures.sandbox_reachable_backend
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org

scoped_connection_guard = connection_scope_support.scoped_connection_guard


async def _create_pod(client, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"scope-{uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": org_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_creating_a_pod_holds_no_connection_across_slow_work(
    authenticated_client, fixed_test_org, scoped_connection_guard
) -> None:
    """The authorization spine, which every other route inherits.

    Pod creation writes the pod, seeds membership and builds the caller's role
    snapshot -- the path that produced 37 of the 108 baselined violations
    through one Redis round-trip inside `build_user_context`.
    """
    async with scoped_connection_guard():
        response = await authenticated_client.post(
            "/pods",
            json={
                "name": f"scope-{uuid4().hex[:8]}",
                "type": "ASSISTANT",
                "organization_id": fixed_test_org["id"],
            },
        )
    assert response.status_code == 201, response.text


async def test_listing_pods_holds_no_connection_across_slow_work(
    authenticated_client, fixed_test_org, scoped_connection_guard
) -> None:
    """A read path with authorization filtering, on the warm-cache route."""
    await _create_pod(authenticated_client, fixed_test_org["id"])
    org_id = fixed_test_org["id"]
    async with scoped_connection_guard():
        response = await authenticated_client.get(f"/pods/organization/{org_id}")
    assert response.status_code == 200, response.text


async def test_creating_a_schedule_holds_no_connection_across_slow_work(
    authenticated_client, fixed_test_org, scoped_connection_guard
) -> None:
    """Creating a TIME schedule now arms the poller cursor in the same write.

    Worth guarding because the arming moved *into* the repository write: if
    computing the next fire time ever grows an I/O call -- a timezone lookup, a
    provider round-trip -- it lands inside the transaction that holds the row.
    """
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": f"scope-agent-{uuid4().hex[:8]}", "instruction": "hi"},
    )
    assert agent.status_code == 201, agent.text

    async with scoped_connection_guard():
        response = await authenticated_client.post(
            f"/pods/{pod_id}/schedules",
            json={
                "name": f"scope-sched-{uuid4().hex[:8]}",
                "schedule_type": "TIME",
                "agent_name": agent.json()["name"],
                "config": {"cron": "0 9 * * *"},
            },
        )
    assert response.status_code == 201, response.text


async def test_listing_schedules_holds_no_connection_across_slow_work(
    authenticated_client, fixed_test_org, scoped_connection_guard
) -> None:
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    async with scoped_connection_guard():
        response = await authenticated_client.get(f"/pods/{pod_id}/schedules")
    assert response.status_code == 200, response.text


async def test_the_guard_itself_still_catches_a_hold(
    db_manager, scoped_connection_guard
) -> None:
    """The tests above are only worth their runtime if this fails.

    Every gate needs one case that proves it can go red, or a wiring change
    turns the whole file green-and-empty exactly like the suite it replaces.
    """
    import asyncio

    from sqlalchemy import text

    from app.core.infrastructure.db.session import async_session_maker

    # The application's session maker, not `db_manager`'s. The monitor attaches
    # to the engine the app uses, so a hold on the harness's own engine is
    # invisible to it -- and a self-test that cannot fail is the exact thing
    # this test exists to prevent.
    # `pytest.fail` raises `Failed`, which derives from BaseException rather
    # than Exception -- so `pytest.raises(Exception)` sails straight past it and
    # the self-test reports DID NOT RAISE while the guard is working perfectly.
    with pytest.raises(pytest.fail.Exception) as excinfo:
        async with scoped_connection_guard(idle_hold_seconds=0.05):
            async with async_session_maker() as session:
                await session.execute(text("SELECT 1"))
                await asyncio.sleep(0.3)  # the hold, with no query in flight
                await session.commit()

    assert "held across non-database work" in str(excinfo.value)


async def test_a_commit_actually_returns_the_connection_to_the_pool(
    db_manager, scoped_connection_guard
) -> None:
    """The single assumption the whole release strategy rests on.

    `_release_after_authorization` commits and calls the connection released.
    So does `connection_released`, and so does `safe_to_release` by implication.
    Three separate mechanisms in this codebase are built on "commit hands the
    pooled connection back", and until now nothing asserted it — which is a lot
    of weight on a fact about SQLAlchemy that was inferred rather than checked.

    The two halves are asserted together on purpose. Without the second, the
    first would pass against an implementation where commit released nothing
    and the monitor simply never fired.
    """
    import asyncio

    from sqlalchemy import text

    from app.core.infrastructure.db.session import async_session_maker

    # Held: a statement, then idle, then commit. Must be reported.
    with pytest.raises(pytest.fail.Exception):
        async with scoped_connection_guard(idle_hold_seconds=0.05):
            async with async_session_maker() as session:
                await session.execute(text("SELECT 1"))
                await asyncio.sleep(0.3)
                await session.commit()

    # Released: the same statement, committed *first*, then idle. Must be clean.
    # If this ever starts failing, every `_release_after_authorization` call in
    # the tree is decorative and the pool is being held through every response.
    async with scoped_connection_guard(idle_hold_seconds=0.05):
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
            await session.commit()
            await asyncio.sleep(0.3)
