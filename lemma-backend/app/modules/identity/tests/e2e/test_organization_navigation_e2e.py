"""The two navigation endpoints, and the query budgets that are their point.

Both exist to replace a request waterfall, so correctness alone is not enough:
if either grows a query per pod or per organization it has stopped doing its
job, and that is what the budget tests pin.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy import event

from app.core.infrastructure.db.session import get_engine
from app.modules.test_support.e2e_authz import signup_user

pytestmark = [pytest.mark.e2e]


@contextmanager
def counted_queries():
    statements: list[str] = []
    engine = get_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


async def _create_org(client, name: str) -> str:
    response = await client.post("/organizations", json={"name": name})
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_pod(client, org_id: str, name: str) -> str:
    response = await client.post(
        "/pods", json={"name": name, "type": "ASSISTANT", "organization_id": org_id}
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_agent(client, pod_id: str, name: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Be brief.", "description": f"{name} desc"},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _create_app(client, pod_id: str, name: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": name,
            "public_slug": f"{name}-{uuid4().hex[:6]}",
            "description": f"{name} desc",
        },
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def test_navigation_returns_every_organization_with_its_pods(
    authenticated_client, fixed_test_org
):
    first_org = fixed_test_org["id"]
    second_org = await _create_org(authenticated_client, f"Nav Org {uuid4().hex[:8]}")
    await _create_pod(authenticated_client, first_org, f"alpha-{uuid4().hex[:6]}")
    await _create_pod(authenticated_client, second_org, f"beta-{uuid4().hex[:6]}")

    response = await authenticated_client.get("/organizations/navigation")
    assert response.status_code == status.HTTP_200_OK, response.text

    by_id = {item["id"]: item for item in response.json()["items"]}
    assert first_org in by_id and second_org in by_id
    assert len(by_id[first_org]["pods"]) == 1
    assert len(by_id[second_org]["pods"]) == 1
    assert by_id[first_org]["role"] == "ORG_OWNER"


async def test_navigation_carries_the_columns_a_pod_list_renders(
    authenticated_client, fixed_test_org
):
    """The home screen labels and sorts on these, so they have to be here.

    They are the pod's own columns, so they cost nothing beyond the query that
    already found the pod — unlike apps or agents, which is where the line is.
    """
    org_id = fixed_test_org["id"]
    pod_name = f"labelled-{uuid4().hex[:6]}"
    created = await authenticated_client.post(
        "/pods",
        json={
            "name": pod_name,
            "type": "ASSISTANT",
            "organization_id": org_id,
            "description": "what this pod is for",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text

    response = await authenticated_client.get("/organizations/navigation")
    assert response.status_code == status.HTTP_200_OK, response.text

    organization = next(
        item for item in response.json()["items"] if item["id"] == org_id
    )
    pod = next(item for item in organization["pods"] if item["name"] == pod_name)
    assert pod["description"] == "what this pod is for"
    assert pod["updated_at"]
    # Still no per-pod contents: that is the detail endpoint's job.
    assert "apps" not in pod
    assert "agents" not in pod


async def test_navigation_query_count_is_flat_across_organizations(
    authenticated_client, fixed_test_org
):
    """Two organizations or six, the sidebar costs the same — the whole point."""
    await _create_pod(authenticated_client, fixed_test_org["id"], f"p-{uuid4().hex[:6]}")
    with counted_queries() as small:
        first = await authenticated_client.get("/organizations/navigation")
    assert first.status_code == status.HTTP_200_OK

    for index in range(5):
        org_id = await _create_org(authenticated_client, f"Extra {uuid4().hex[:8]}")
        await _create_pod(authenticated_client, org_id, f"p{index}-{uuid4().hex[:6]}")

    with counted_queries() as large:
        second = await authenticated_client.get("/organizations/navigation")
    assert second.status_code == status.HTTP_200_OK
    assert len(second.json()["items"]) >= 6

    assert len(large) == len(small), (
        f"6 organizations issued {len(large)} queries where 1 issued {len(small)}:\n"
        + "\n".join(f"  {statement[:120]}" for statement in large)
    )


async def test_home_returns_pods_with_their_apps_agents_and_roles(
    authenticated_client, fixed_test_org
):
    org_id = fixed_test_org["id"]
    pod_name = f"home-{uuid4().hex[:6]}"
    pod_id = await _create_pod(authenticated_client, org_id, pod_name)
    await _create_agent(authenticated_client, pod_id, f"agent{uuid4().hex[:6]}")
    await _create_app(authenticated_client, pod_id, f"app{uuid4().hex[:6]}")

    response = await authenticated_client.get(f"/organizations/{org_id}/home")
    assert response.status_code == status.HTTP_200_OK, response.text

    body = response.json()
    assert body["organization_id"] == org_id
    assert body["role"] == "ORG_OWNER"
    pod = next(item for item in body["pods"] if item["id"] == pod_id)
    assert pod["name"] == pod_name
    assert len(pod["agents"]) == 1
    assert pod["agents"][0]["description"].endswith("desc")
    assert len(pod["apps"]) == 1
    # The URL is the app's real serving address, not just its slug.
    assert pod["apps"][0]["url"].startswith(("http://", "https://"))
    assert "." in pod["apps"][0]["url"]


async def test_home_query_count_does_not_grow_with_pods(
    authenticated_client, fixed_test_org
):
    """The endpoint that replaces per-pod fetching must not do per-pod fetching."""
    org_id = fixed_test_org["id"]
    first_pod = await _create_pod(authenticated_client, org_id, f"one-{uuid4().hex[:6]}")
    await _create_agent(authenticated_client, first_pod, f"a{uuid4().hex[:6]}")

    # A cache hit would measure nothing, so both samples must miss: caching is
    # keyed per organization, and each half of this test uses a fresh one.
    with counted_queries() as small:
        first = await authenticated_client.get(f"/organizations/{org_id}/home")
    assert first.status_code == status.HTTP_200_OK

    bigger_org = await _create_org(authenticated_client, f"Big {uuid4().hex[:8]}")
    for index in range(5):
        pod_id = await _create_pod(
            authenticated_client, bigger_org, f"many{index}-{uuid4().hex[:6]}"
        )
        await _create_agent(authenticated_client, pod_id, f"a{index}{uuid4().hex[:6]}")

    with counted_queries() as large:
        second = await authenticated_client.get(f"/organizations/{bigger_org}/home")
    assert second.status_code == status.HTTP_200_OK
    assert len(second.json()["pods"]) == 5

    assert len(large) == len(small), (
        f"5 pods issued {len(large)} queries where 1 issued {len(small)}:\n"
        + "\n".join(f"  {statement[:120]}" for statement in large)
    )


async def test_home_is_cached_so_a_repeat_visit_touches_no_database(
    authenticated_client, fixed_test_org
):
    org_id = fixed_test_org["id"]
    await _create_pod(authenticated_client, org_id, f"cached-{uuid4().hex[:6]}")

    first = await authenticated_client.get(f"/organizations/{org_id}/home")
    assert first.status_code == status.HTTP_200_OK

    with counted_queries() as statements:
        second = await authenticated_client.get(f"/organizations/{org_id}/home")
    assert second.status_code == status.HTTP_200_OK
    assert second.json() == first.json()
    assert statements == [], (
        "a cached home should not query at all, but issued:\n"
        + "\n".join(f"  {statement[:120]}" for statement in statements)
    )


async def _join_org_as_member(authenticated_client, async_client, org_id: str):
    """Sign up a second user and take them into the organization as a member."""
    user = await signup_user(async_client, "org-nav-member")
    email, token = user["email"], user["token"]

    invitation = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": email, "role": "ORG_MEMBER"},
    )
    assert invitation.status_code == status.HTTP_201_CREATED, invitation.text
    accepted = await async_client.post(
        f"/organizations/invitations/{invitation.json()['id']}/accept",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert accepted.status_code == status.HTTP_200_OK, accepted.text

    members = await authenticated_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == status.HTTP_200_OK, members.text
    org_member = next(
        item
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == email
    )
    return token, org_member["id"]


async def test_a_member_sees_only_the_pods_they_joined(
    authenticated_client, async_client, fixed_test_org
):
    """Visibility is membership, not organization — the rule worth getting right.

    The owner sees both pods because they own the organization; the member must
    see only the one they were added to, in both endpoints.
    """
    org_id = fixed_test_org["id"]
    joined = await _create_pod(authenticated_client, org_id, f"joined-{uuid4().hex[:6]}")
    hidden = await _create_pod(authenticated_client, org_id, f"hidden-{uuid4().hex[:6]}")

    token, org_member_id = await _join_org_as_member(
        authenticated_client, async_client, org_id
    )
    added = await authenticated_client.post(
        f"/pods/{joined}/members",
        json={"organization_member_id": org_member_id, "roles": ["POD_EDITOR"]},
    )
    assert added.status_code == status.HTTP_201_CREATED, added.text

    member_auth = {"Authorization": f"Bearer {token}"}

    navigation = await async_client.get("/organizations/navigation", headers=member_auth)
    assert navigation.status_code == status.HTTP_200_OK, navigation.text
    organization = next(
        item for item in navigation.json()["items"] if item["id"] == org_id
    )
    assert [pod["id"] for pod in organization["pods"]] == [joined]
    assert organization["role"] == "ORG_MEMBER"

    home = await async_client.get(f"/organizations/{org_id}/home", headers=member_auth)
    assert home.status_code == status.HTTP_200_OK, home.text
    pods = home.json()["pods"]
    assert [pod["id"] for pod in pods] == [joined]
    assert hidden not in [pod["id"] for pod in pods]
    assert pods[0]["roles"] == ["POD_EDITOR"]

    # The owner still sees both, with the admin role creating a pod confers.
    owner_home = await authenticated_client.get(f"/organizations/{org_id}/home")
    assert owner_home.status_code == status.HTTP_200_OK, owner_home.text
    owner_pods = {pod["id"]: pod for pod in owner_home.json()["pods"]}
    assert {joined, hidden} <= set(owner_pods)
    assert owner_pods[hidden]["roles"] == ["POD_ADMIN"]


async def test_home_refuses_an_organization_the_caller_is_not_in(
    authenticated_client, async_client, fixed_test_org
):
    """Membership is the gate; a stranger must not learn an organization exists."""
    org_id = fixed_test_org["id"]
    outsider = await signup_user(async_client, "org-nav-outsider")

    response = await async_client.get(
        f"/organizations/{org_id}/home",
        headers={"Authorization": f"Bearer {outsider['token']}"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text


@pytest.mark.slow
async def test_a_realistic_multi_org_workspace_stays_fast(
    authenticated_client, fixed_test_org, capsys
):
    """Five organizations, twenty pods, apps and agents — what the change is for.

    The shapes only pay off at the size that hurt: one organization with one pod
    hides a waterfall, five with four each is where a user actually felt it.
    This builds that, then measures what the sidebar costs against what it used
    to cost, so the claim in the pull request is a number and not an argument.

    The ceilings are deliberately loose. Wall clock on a laptop running Docker
    is not a contract, and a tight timing gate here would only teach people to
    rerun it; the query-count tests above are the real guarantee. What this
    catches is an order-of-magnitude regression.
    """
    org_ids = [fixed_test_org["id"]]
    for index in range(4):
        org_ids.append(
            await _create_org(authenticated_client, f"Scale Org {index}-{uuid4().hex[:6]}")
        )

    pods_by_org: dict[str, list[str]] = {}
    for org_id in org_ids:
        pods_by_org[org_id] = [
            await _create_pod(authenticated_client, org_id, f"pod-{uuid4().hex[:8]}")
            for _ in range(4)
        ]

    # Contents on the organization whose landing page gets measured, so `home`
    # is doing real work rather than returning empty lists.
    detail_org = org_ids[0]
    for pod_id in pods_by_org[detail_org]:
        for _ in range(2):
            await _create_app(authenticated_client, pod_id, f"app{uuid4().hex[:8]}")
            await _create_agent(authenticated_client, pod_id, f"agent{uuid4().hex[:8]}")

    async def timed(call, samples: int = 10) -> tuple[float, float, float]:
        """Cold first call, then p50/p95 of the steady state.

        The first call is reported separately because ``home`` caches: measuring
        only the warm path would quote a cache hit as if it were the cost of
        building the page.
        """
        started = time.perf_counter()
        first = await call()
        cold = (time.perf_counter() - started) * 1000
        assert first.status_code == status.HTTP_200_OK, first.text

        timings = []
        for _ in range(samples):
            started = time.perf_counter()
            response = await call()
            timings.append(time.perf_counter() - started)
            assert response.status_code == status.HTTP_200_OK, response.text
        timings.sort()
        return (
            cold,
            timings[len(timings) // 2] * 1000,
            timings[int(len(timings) * 0.95) - 1] * 1000,
        )

    navigation_cold, navigation_p50, navigation_p95 = await timed(
        lambda: authenticated_client.get("/organizations/navigation")
    )
    home_cold, home_p50, home_p95 = await timed(
        lambda: authenticated_client.get(f"/organizations/{detail_org}/home")
    )

    # The shape this replaced: the organization list, then a pod list per
    # organization, each waiting on the one before it.
    async def legacy_fan_out():
        response = await authenticated_client.get("/organizations")
        assert response.status_code == status.HTTP_200_OK, response.text
        for org_id in org_ids:
            response = await authenticated_client.get(f"/pods/organization/{org_id}")
            assert response.status_code == status.HTTP_200_OK, response.text
        return response

    _, legacy_p50, legacy_p95 = await timed(legacy_fan_out, samples=5)

    navigation = await authenticated_client.get("/organizations/navigation")
    payload = navigation.json()["items"]
    assert len(payload) >= len(org_ids)
    assert sum(len(entry["pods"]) for entry in payload) >= 20

    home = await authenticated_client.get(f"/organizations/{detail_org}/home")
    home_pods = home.json()["pods"]
    assert len(home_pods) == 4
    assert all(len(pod["apps"]) == 2 for pod in home_pods)
    assert all(len(pod["agents"]) == 2 for pod in home_pods)

    with capsys.disabled():
        print(
            f"\n  5 organizations, 20 pods, 8 apps and 8 agents on the detail org"
            f"\n  {'route':44} {'cold':>9} {'p50':>9} {'p95':>9}"
            f"\n  {'GET /organizations/navigation':44} {navigation_cold:7.1f}ms {navigation_p50:7.1f}ms {navigation_p95:7.1f}ms"
            f"\n  {'GET /organizations/{id}/home':44} {home_cold:7.1f}ms {home_p50:7.1f}ms {home_p95:7.1f}ms"
            f"\n  {'was: /organizations + 5x /pods/organization':44} {'':9} {legacy_p50:7.1f}ms {legacy_p95:7.1f}ms"
            f"\n  → the sidebar costs {legacy_p50 / navigation_p50:.1f}x less than the fan-out it replaces"
            f"\n  → home's p50 is a cache hit; {home_cold:.1f}ms is what building it costs"
        )

    # An order of magnitude, not a stopwatch: one request cannot reasonably be
    # slower than the six it replaced.
    assert navigation_p50 < legacy_p50, (
        f"navigation ({navigation_p50:.1f}ms) should beat the fan-out "
        f"({legacy_p50:.1f}ms) it replaces"
    )
    assert navigation_p95 < 250, f"navigation p95 {navigation_p95:.1f}ms"
    assert home_p95 < 250, f"home p95 {home_p95:.1f}ms"
