"""What a pod list actually costs the database.

Latency complaints about this endpoint are hard to argue about with wall-clock
numbers on a laptop, and wall-clock is not what regresses: query *count* is.
These tests pin it, so a future change that adds a per-pod read fails here
instead of being discovered in production.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.test_support.query_counting import counted_queries

pytestmark = [pytest.mark.e2e]


async def _create_pods(client, org_id: str, count: int) -> None:
    for index in range(count):
        response = await client.post(
            "/pods",
            json={
                "name": f"budget-{uuid4().hex[:8]}-{index}",
                "type": "ASSISTANT",
                "organization_id": org_id,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text


async def test_pod_list_query_count_does_not_grow_with_pod_count(
    authenticated_client, fixed_test_org
):
    """The whole point of a list endpoint: one page costs the same either way.

    Compared as a delta between two sizes rather than as an absolute, so the
    assertion survives an extra unrelated read being added to the request path
    but still catches anything that runs *per pod*.
    """
    org_id = fixed_test_org["id"]

    await _create_pods(authenticated_client, org_id, 2)
    with counted_queries() as small:
        first = await authenticated_client.get(f"/organizations/{org_id}/pods")
    assert first.status_code == status.HTTP_200_OK, first.text
    assert len(first.json()["items"]) == 2

    await _create_pods(authenticated_client, org_id, 8)
    with counted_queries() as large:
        second = await authenticated_client.get(f"/organizations/{org_id}/pods")
    assert second.status_code == status.HTTP_200_OK, second.text
    assert len(second.json()["items"]) == 10

    assert len(large) == len(small), (
        f"listing 10 pods issued {len(large)} queries where 2 pods issued "
        f"{len(small)} — something runs per pod:\n"
        + "\n".join(f"  {statement[:120]}" for statement in large)
    )


async def test_pod_list_stays_within_its_query_budget(
    authenticated_client, fixed_test_org
):
    """A ceiling, so *fixed* per-request overhead cannot creep up unnoticed.

    Distinct from the differential test above, which catches work that scales
    with the number of pods but not a new constant query added to every
    request. This is the one that catches that.

    The number has to sit below what a regression would produce or it asserts
    nothing. It was 6 against an observed 2, and a per-pod read introduced
    deliberately to check took the count to 5 — under budget, test green. Four
    leaves one slot of headroom above the two queries this endpoint genuinely
    needs (the caller's organization membership, then the pods) while still
    landing below the 5 that a per-pod regression produces at this fixture
    size.

    Changing it is meant to be a deliberate act: if a legitimate read is added
    here, raise it and say why in the same commit.
    """
    org_id = fixed_test_org["id"]
    pod_count = 3
    await _create_pods(authenticated_client, org_id, pod_count)

    with counted_queries() as statements:
        response = await authenticated_client.get(f"/organizations/{org_id}/pods")
    assert response.status_code == status.HTTP_200_OK, response.text

    budget = 4
    assert len(statements) <= budget, (
        f"pod list issued {len(statements)} queries, budget is {budget}:\n"
        + "\n".join(f"  {statement[:120]}" for statement in statements)
    )
    assert budget < len(statements) + pod_count, (
        f"the budget ({budget}) is loose enough to absorb a read per pod at "
        f"this fixture size ({pod_count} pods, {len(statements)} queries), so "
        "it cannot fail for the reason it exists"
    )


@pytest.mark.slow
async def test_request_cost_decomposition_is_reported(
    authenticated_client, fixed_test_org, capsys
):
    """Attribute the per-request cost across the stack, rather than guess at it.

    ``/health`` is auth-excluded and touches no database, so it is the ASGI and
    middleware baseline. ``/organizations`` adds authentication. The pod list
    adds the handler. Subtracting neighbours says which layer owns the latency,
    which is the only way to fix the right one.

    Deliberately not an assertion: wall clock on a developer laptop is not a
    contract, and a flaky timing gate teaches people to ignore it. The query
    budget above is the gate; this is the evidence.
    """
    org_id = fixed_test_org["id"]
    await _create_pods(authenticated_client, org_id, 10)

    async def measure(path: str) -> tuple[float, float, int]:
        for _ in range(3):  # warm caches and connections
            await authenticated_client.get(path)
        with counted_queries() as statements:
            response = await authenticated_client.get(path)
            assert response.status_code == status.HTTP_200_OK, response.text
        queries = len(statements)
        samples = []
        for _ in range(20):
            started = time.perf_counter()
            await authenticated_client.get(path)
            samples.append(time.perf_counter() - started)
        samples.sort()
        return (
            samples[len(samples) // 2] * 1000,
            samples[int(len(samples) * 0.95) - 1] * 1000,
            queries,
        )

    health = await measure("/health")
    orgs = await measure("/organizations")
    pods = await measure(f"/organizations/{org_id}/pods")

    with capsys.disabled():
        print("\n  route                       p50      p95   queries")
        for label, (p50, p95, queries) in (
            ("/health (no auth, no db)", health),
            ("/organizations (auth)", orgs),
            ("/organizations/{id}/pods (10 pods)", pods),
        ):
            print(f"  {label:26} {p50:6.1f}ms {p95:6.1f}ms  {queries:3d}")
        print(
            f"  → authentication costs {orgs[0] - health[0]:.1f}ms, "
            f"the pod handler adds {pods[0] - orgs[0]:.1f}ms"
        )
