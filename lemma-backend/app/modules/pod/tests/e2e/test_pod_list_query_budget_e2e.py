"""What a pod list actually costs the database.

Latency complaints about this endpoint are hard to argue about with wall-clock
numbers on a laptop, and wall-clock is not what regresses: query *count* is.
These tests pin it, so a future change that adds a per-pod read fails here
instead of being discovered in production.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy import event

from app.core.infrastructure.db.session import get_engine

pytestmark = [pytest.mark.e2e]


@contextmanager
def counted_queries():
    """Record every statement the engine executes inside the block.

    Attached to the engine rather than one session because the request path
    opens more than one: ``verify_auth`` reads the user through a session of its
    own, and that read is exactly the kind of cost worth seeing here.
    """
    statements: list[str] = []
    engine = get_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


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
        first = await authenticated_client.get(f"/pods/organization/{org_id}")
    assert first.status_code == status.HTTP_200_OK, first.text
    assert len(first.json()["items"]) == 2

    await _create_pods(authenticated_client, org_id, 8)
    with counted_queries() as large:
        second = await authenticated_client.get(f"/pods/organization/{org_id}")
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
    """A ceiling, so the per-request overhead cannot creep up unnoticed.

    The endpoint needs the caller's auth state, their organization membership,
    and the pods themselves. Everything beyond that is worth a second look,
    which is what this number is for.
    """
    org_id = fixed_test_org["id"]
    await _create_pods(authenticated_client, org_id, 3)

    with counted_queries() as statements:
        response = await authenticated_client.get(f"/pods/organization/{org_id}")
    assert response.status_code == status.HTTP_200_OK, response.text

    budget = 6
    assert len(statements) <= budget, (
        f"pod list issued {len(statements)} queries, budget is {budget}:\n"
        + "\n".join(f"  {statement[:120]}" for statement in statements)
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
    pods = await measure(f"/pods/organization/{org_id}")

    with capsys.disabled():
        print("\n  route                       p50      p95   queries")
        for label, (p50, p95, queries) in (
            ("/health (no auth, no db)", health),
            ("/organizations (auth)", orgs),
            ("/pods/organization (10 pods)", pods),
        ):
            print(f"  {label:26} {p50:6.1f}ms {p95:6.1f}ms  {queries:3d}")
        print(
            f"  → authentication costs {orgs[0] - health[0]:.1f}ms, "
            f"the pod handler adds {pods[0] - orgs[0]:.1f}ms"
        )
