"""What authorization actually costs, in milliseconds and in queries.

Authorization runs on essentially every API request and every agent tool call,
so its cost is multiplied by the busiest number in the system. That makes it
worth measuring rather than reasoning about -- and worth pinning, because the
expensive regressions here are not slow code, they are *extra round trips* that
each look harmless at the call site.

Three regimes are measured separately because they cost very different amounts
and happen at very different rates:

* **cold** -- no cached role snapshot. Derives roles, grants and memberships
  from the database. Paid once per principal per cache TTL.
* **warm** -- role snapshot served from Redis, fresh ``Context`` (what a new
  request gets). This is the regime almost every real request is in.
* **repeat** -- a second check inside the same request, served from the
  in-context decision cache. No I/O at all.

The pod here is created through the API, so the principal has genuine
membership and roles. A decision measured against a pod nobody belongs to
would short-circuit into a denial and report a cost the product never pays.

Wall clock is printed rather than asserted tightly -- it is measured against
containers on whatever hardware CI provides. The assertions are on query
*counts*, which is what actually regresses, plus one absolute bound on the
decision cache.
"""

from __future__ import annotations

import statistics
import time
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.permissions import PERMISSION_BY_ID, Permissions
from app.core.authorization.service import AuthorizationDataService
from app.modules.test_support.query_counting import counted_queries

pytestmark = [pytest.mark.e2e]

# Enough samples for a stable median without making the test slow. Cold is the
# expensive regime, so it gets fewer.
_WARM_SAMPLES = 30
_COLD_SAMPLES = 8
_REPEAT_SAMPLES = 50


def _median_ms(samples: list[float]) -> float:
    return statistics.median(samples) * 1000.0


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000.0


def _format_statements(statements: list[str]) -> str:
    return "\n".join(f"  - {s.strip()[:160]}" for s in statements)


async def _drop_snapshot(user_id: UUID) -> None:
    from app.core.authorization import cache as authz_cache

    await authz_cache.invalidate_role_snapshot_cache(user_id=user_id)


@pytest.fixture
async def real_pod(authenticated_client, fixed_test_org) -> dict:
    """A pod created through the API, with the caller as a real member."""
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"authz-cost-{uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def test_authorization_cost_by_regime(
    db_session, fixed_test_user, real_pod
) -> None:
    """Measure and report cold / warm / repeat authorization cost."""
    service = AuthorizationDataService(db_session)
    user_id = UUID(fixed_test_user["id"])
    pod_id = UUID(real_pod["id"])
    resource = ResourceRef(
        resource_type=ResourceType.POD, resource_id=pod_id, pod_id=pod_id
    )

    # Warm up, so first-call import and statement-compile cost is not
    # attributed to the measurement.
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    assert await ctx.can(Permissions.POD_READ, resource), (
        "the fixture principal cannot read its own pod; every number below "
        "would be measuring a short-circuited denial, not real authorization"
    )

    repeat: list[float] = []
    for _ in range(_REPEAT_SAMPLES):
        start = time.perf_counter()
        await ctx.can(Permissions.POD_READ, resource)
        repeat.append(time.perf_counter() - start)

    warm: list[float] = []
    warm_build_only: list[float] = []
    for _ in range(_WARM_SAMPLES):
        start = time.perf_counter()
        fresh = await service.build_user_context(user_id=user_id, pod_id=pod_id)
        built = time.perf_counter()
        await fresh.can(Permissions.POD_READ, resource)
        end = time.perf_counter()
        warm_build_only.append(built - start)
        warm.append(end - start)

    cold: list[float] = []
    for _ in range(_COLD_SAMPLES):
        await _drop_snapshot(user_id)
        start = time.perf_counter()
        fresh = await service.build_user_context(user_id=user_id, pod_id=pod_id)
        await fresh.can(Permissions.POD_READ, resource)
        cold.append(time.perf_counter() - start)

    print(
        "\n=== authorization cost (median / p95, ms) ===\n"
        f"  cold   (snapshot miss, derives from DB): "
        f"{_median_ms(cold):8.3f} / {_p95_ms(cold):8.3f}\n"
        f"  warm   (Redis snapshot hit, new ctx):    "
        f"{_median_ms(warm):8.3f} / {_p95_ms(warm):8.3f}\n"
        f"    of which build_user_context:           "
        f"{_median_ms(warm_build_only):8.3f}\n"
        f"  repeat (decision cache, same request):   "
        f"{_median_ms(repeat):8.4f} / {_p95_ms(repeat):8.4f}\n"
    )

    # The decision cache must be a pure in-memory lookup. A regression that
    # puts I/O back here is multiplied by every check in a request, so this is
    # bounded absolutely rather than relative to the other regimes.
    assert _median_ms(repeat) < 0.5, (
        f"a repeated in-request check took {_median_ms(repeat):.3f}ms; the "
        "decision cache should make it a dict lookup -- something is doing I/O"
    )

    # Weak on purpose: the point is to catch the snapshot cache silently not
    # being consulted, not to police a few milliseconds of container noise.
    assert _median_ms(warm) < _median_ms(cold), (
        f"warm authorization ({_median_ms(warm):.3f}ms) was not cheaper than "
        f"cold ({_median_ms(cold):.3f}ms) -- the role snapshot cache is not "
        "being used"
    )


async def test_warm_authorization_issues_no_queries(
    db_session, fixed_test_user, real_pod
) -> None:
    """A cached-snapshot pod authorization must not touch the database.

    This is the assertion the millisecond numbers are downstream of, and the
    one worth pinning. The pod-scoped snapshot key omits the organization
    precisely so the lookup needs no row read to build the key; keyed the other
    way, every pod request paid a ``Pod`` read at a 100% cache hit rate. If a
    future change reintroduces a read -- to resolve the org, to hydrate a ref
    that is already hydrated -- it shows up here as a count rather than as
    noise in a latency graph.
    """
    service = AuthorizationDataService(db_session)
    user_id = UUID(fixed_test_user["id"])
    pod_id = UUID(real_pod["id"])
    resource = ResourceRef(
        resource_type=ResourceType.POD, resource_id=pod_id, pod_id=pod_id
    )

    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    assert await ctx.can(Permissions.POD_READ, resource)

    with counted_queries() as statements:
        fresh = await service.build_user_context(user_id=user_id, pod_id=pod_id)
        assert await fresh.can(Permissions.POD_READ, resource)

    assert statements == [], (
        f"a warm pod authorization issued {len(statements)} quer(y/ies) that "
        f"the cached snapshot should have made unnecessary:\n"
        f"{_format_statements(statements)}"
    )


async def test_second_permission_on_same_resource_issues_no_queries(
    db_session, fixed_test_user, real_pod
) -> None:
    """Two permissions on one resource must not read its row twice.

    ``_decision_cache`` keys on (permission, type, id), so a second permission
    is a genuine cache miss -- but the resource row behind it is the same row.
    Hydration is what reads it, and hydration is skipped once the ref carries
    its visibility, so the second check should cost nothing at the database.
    """
    service = AuthorizationDataService(db_session)
    user_id = UUID(fixed_test_user["id"])
    pod_id = UUID(real_pod["id"])
    resource = ResourceRef(
        resource_type=ResourceType.POD, resource_id=pod_id, pod_id=pod_id
    )

    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    assert await ctx.can(Permissions.POD_READ, resource)

    with counted_queries() as statements:
        await ctx.can(Permissions.POD_UPDATE, resource)

    assert statements == [], (
        f"a second permission on an already-authorized resource issued "
        f"{len(statements)} quer(y/ies):\n{_format_statements(statements)}"
    )


async def test_unrelated_pod_is_still_denied(
    db_session, fixed_test_user, real_pod
) -> None:
    """The speed must not come from a cache key that is too coarse.

    Every other assertion in this file rewards making authorization skip work.
    This one is their counterweight: a key loose enough to serve one pod's
    snapshot for another would make all of those numbers better and the system
    wrong. It lives here, beside them, so the trade stays visible.
    """
    service = AuthorizationDataService(db_session)
    user_id = UUID(fixed_test_user["id"])
    pod_id = UUID(real_pod["id"])

    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    assert await ctx.can(
        Permissions.POD_READ,
        ResourceRef(
            resource_type=ResourceType.POD, resource_id=pod_id, pod_id=pod_id
        ),
    )

    stranger = uuid4()
    allowed = await ctx.can(
        Permissions.POD_READ,
        ResourceRef(
            resource_type=ResourceType.POD,
            resource_id=stranger,
            pod_id=stranger,
        ),
    )
    assert not allowed, (
        "authorization allowed a pod the principal has no membership in -- the "
        "snapshot cache key or the decision cache key is too coarse"
    )


async def test_effective_permissions_reads_grants_once_not_once_per_permission(
    db_session, async_client, authenticated_client, fixed_test_org, real_pod
) -> None:
    """`/permissions/me` asks 51 questions; it must not make 51 round trips.

    The regimes above are all measured as an *org owner*, who short-circuits at
    `ORG_OWNER_POD` before any grant lookup — so the expensive regime was
    entirely untested. A POD_VIEWER is the opposite: it holds a handful of the
    51 permissions and falls through to the grant query for each of the rest.

    Both halves matter and are asserted together, because either alone is
    satisfiable by a bug. The count alone would pass if the memo returned the
    wrong rows; the equality alone would pass if nothing had been fixed. So the
    same loop runs twice against the same context — once with the memo cleared
    between permissions, which is exactly the pre-fix behaviour, and once
    letting it stand.
    """
    from app.modules.test_support.e2e_authz import (
        add_pod_member,
        invite_org_member,
        signup_user,
    )

    pod_id = UUID(real_pod["id"])
    viewer = await signup_user(async_client, "authz-cost-viewer")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=viewer,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=str(pod_id),
        organization_member_id=org_member["id"],
        role="POD_VIEWER",
    )

    service = AuthorizationDataService(db_session)
    viewer_id = UUID(viewer["id"])
    resource = ResourceRef(
        resource_type=ResourceType.POD, resource_id=pod_id, pod_id=pod_id
    )
    permission_ids = sorted(PERMISSION_BY_ID)
    assert len(permission_ids) > 40, "the whole point is that this list is long"

    async def _effective_actions(ctx, *, forget_between_permissions: bool) -> list[str]:
        allowed: list[str] = []
        for permission_id in permission_ids:
            if forget_between_permissions:
                ctx.authorizer._grant_rows.clear()
                ctx.authorizer._folder_ids_by_paths.clear()
            if await ctx.can(permission_id, resource):
                allowed.append(permission_id)
        return allowed

    unmemoized_ctx = await service.build_user_context(
        user_id=viewer_id, pod_id=pod_id
    )
    with counted_queries() as before_statements:
        before = await _effective_actions(
            unmemoized_ctx, forget_between_permissions=True
        )

    memoized_ctx = await service.build_user_context(user_id=viewer_id, pod_id=pod_id)
    with counted_queries() as after_statements:
        after = await _effective_actions(
            memoized_ctx, forget_between_permissions=False
        )

    assert after == before, (
        "memoizing the grant lookup changed which permissions a POD_VIEWER "
        f"holds. only in the memoized answer: {sorted(set(after) - set(before))}; "
        f"only in the per-permission answer: {sorted(set(before) - set(after))}"
    )
    assert before, "the viewer holds no permissions at all -- the fixture is wrong"

    grant_reads_after = [s for s in after_statements if "resource_permission_grants" in s]
    grant_reads_before = [
        s for s in before_statements if "resource_permission_grants" in s
    ]
    assert len(grant_reads_before) > 20, (
        "the pre-fix regime issued only "
        f"{len(grant_reads_before)} grant reads -- this fixture is no longer in "
        "the costly regime, so the assertion below proves nothing"
    )
    assert len(grant_reads_after) <= 1, (
        f"{len(permission_ids)} permissions on one resource issued "
        f"{len(grant_reads_after)} grant reads (was {len(grant_reads_before)}):\n"
        f"{_format_statements(grant_reads_after)}"
    )


async def test_a_grant_added_to_a_fresh_context_is_seen(
    db_session, async_client, authenticated_client, fixed_test_org, real_pod
) -> None:
    """The memo must not outlive the context that owns it.

    Counterweight to the test above, which rewards remembering. A memo keyed
    too loosely -- or held for longer than one context -- would make that test
    pass and leave a revoked grant answering yes.
    """
    from app.modules.test_support.e2e_authz import (
        add_pod_member,
        auth_headers,
        invite_org_member,
        signup_user,
    )

    pod_id = UUID(real_pod["id"])
    viewer = await signup_user(async_client, "authz-cost-grantee")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=viewer,
    )
    member = await add_pod_member(
        authenticated_client,
        pod_id=str(pod_id),
        organization_member_id=org_member["id"],
        role="POD_VIEWER",
    )
    resource = ResourceRef(
        resource_type=ResourceType.POD, resource_id=pod_id, pod_id=pod_id
    )
    service = AuthorizationDataService(db_session)
    viewer_id = UUID(viewer["id"])

    before_ctx = await service.build_user_context(user_id=viewer_id, pod_id=pod_id)
    assert await before_ctx.can(Permissions.POD_READ, resource)
    assert not await before_ctx.can(Permissions.POD_UPDATE, resource)

    role = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": f"updaters_{uuid4().hex[:6]}", "permission_ids": ["pod.update"]},
    )
    assert role.status_code == status.HTTP_201_CREATED, role.text
    role_name = role.json()["name"]
    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/members/{member['pod_member_id']}/roles",
        json={"roles": ["POD_VIEWER", role_name]},
    )
    assert updated.status_code == status.HTTP_200_OK, updated.text

    from app.core.authorization import cache as authz_cache

    await authz_cache.invalidate_role_snapshot_cache(user_id=viewer_id)
    after_ctx = await service.build_user_context(user_id=viewer_id, pod_id=pod_id)
    assert await after_ctx.can(Permissions.POD_UPDATE, resource), (
        "a permission granted after the context was built was not visible to a "
        "context built afterwards -- something is cached beyond one request"
    )

    effective = await async_client.get(
        f"/pods/{pod_id}/permissions/me", headers=auth_headers(viewer)
    )
    assert effective.status_code == status.HTTP_200_OK, effective.text
    assert Permissions.POD_UPDATE in effective.json()["actions"], (
        "the endpoint the memo exists to speed up did not report the new grant"
    )
