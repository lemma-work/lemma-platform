"""Decisions between people and workloads in one pod, end to end.

Whose record a subject finds, whose corrections reach whose prompts, and what a
workload granted one decider may ask -- each checked against Postgres, where
the unique index and the example query decide it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.modules.decisions.tests.e2e.test_decisions_api_e2e import TRIAGE, _define
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

PRIVATE = {
    "from": "counsel@firm.example",
    "subject": "ALICE-PRIVATE settlement terms",
    "labels": ["INBOX"],
}
BOBS = {"from": "billing@acme.example", "subject": "Invoice 42", "labels": ["INBOX"]}


@pytest_asyncio.fixture
async def teammate(authenticated_client, async_client, fixed_test_org, test_pod):
    """A second person in the same pod, who may ask its deciders."""
    bob = await signup_user(async_client, "decisions-teammate")
    member = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=bob
    )
    await add_pod_member(
        authenticated_client,
        pod_id=test_pod["id"],
        organization_member_id=member["id"],
        role="POD_USER",
    )
    return bob


async def _decide(
    client: AsyncClient,
    pod_id: str,
    state: dict,
    *,
    subject: str | None = None,
    visibility: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    body: dict[str, object] = {"decider": "email-triage", "state": state}
    if subject is not None:
        body["subject"] = subject
    if visibility is not None:
        body["visibility"] = visibility
    response = await client.post(
        f"/pods/{pod_id}/decisions", json=body, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_subject_decided_privately_by_two_people_is_two_records(
    authenticated_client, async_client, test_pod, teammate
) -> None:
    pod_id = test_pod["id"]
    bob = auth_headers(teammate)
    await _define(authenticated_client, pod_id)
    alices = await _decide(authenticated_client, pod_id, PRIVATE, subject="record:42")

    bobs = await _decide(async_client, pod_id, BOBS, subject="record:42", headers=bob)

    assert bobs["id"] != alices["id"]
    assert "ALICE-PRIVATE" not in (bobs["evidence"] or "")
    again = await _decide(async_client, pod_id, BOBS, subject="record:42", headers=bob)
    assert again["id"] == bobs["id"]
    hidden = await async_client.get(
        f"/pods/{pod_id}/decisions/{alices['id']}", headers=bob
    )
    assert hidden.status_code == 404


async def test_rows_decided_privately_by_two_people_are_their_own(
    authenticated_client, async_client, test_pod, teammate
) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)

    async def rows(client: AsyncClient, row: dict, headers=None) -> dict:
        response = await client.post(
            f"/pods/{pod_id}/decisions/rows",
            json={
                "decider": "email-triage",
                "rows": [{"id": "7", **row}],
                "id_field": "id",
                "subject_prefix": "inbox",
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return response.json()["rows"][0]

    alices = await rows(authenticated_client, PRIVATE)
    bobs = await rows(async_client, BOBS, auth_headers(teammate))

    assert bobs["decision_id"] != alices["decision_id"]


async def test_a_shared_subject_is_decided_once_for_the_pod(
    authenticated_client, async_client, test_pod, teammate
) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)
    first = await _decide(
        authenticated_client, pod_id, BOBS, subject="thread:9", visibility="POD"
    )

    second = await _decide(
        async_client,
        pod_id,
        BOBS,
        subject="thread:9",
        visibility="POD",
        headers=auth_headers(teammate),
    )

    assert second["id"] == first["id"]


async def test_a_private_correction_teaches_only_the_person_who_made_it(
    authenticated_client, test_pod, teammate, fixed_test_user
) -> None:
    from app.core.infrastructure.db import session as db_session_module
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.decisions.infrastructure.repositories import SqlExampleStore

    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)
    private = await _decide(authenticated_client, pod_id, PRIVATE)
    shared = await _decide(authenticated_client, pod_id, BOBS, visibility="POD")
    for decision, answer in ((private, "ask"), (shared, "ignore")):
        corrected = await authenticated_client.post(
            f"/pods/{pod_id}/decisions/{decision['id']}/answer",
            json={"answers": {"action": answer}},
        )
        assert corrected.status_code == 200, corrected.text

    store = SqlExampleStore(
        SessionUnitOfWorkFactory(db_session_module.async_session_maker)
    )

    async def examples_for(viewer: str) -> list[tuple[str, str]]:
        found = await store.recent(
            pod_id=UUID(pod_id),
            decider_key="email-triage",
            question_keys=["action"],
            per_question=10,
            viewer_id=UUID(viewer),
        )
        return sorted((str(e.value), e.evidence) for e in found.get("action", []))

    for_bob = await examples_for(teammate["id"])
    for_alice = await examples_for(fixed_test_user["id"])

    # Bob's prompts carry the pod's correction and nothing of Alice's own.
    assert [value for value, _evidence in for_bob] == ["ignore"]
    assert all("ALICE-PRIVATE" not in evidence for _value, evidence in for_bob)
    assert [value for value, _evidence in for_alice] == ["ask", "ignore"]


async def test_a_workload_granted_one_decider_may_ask_it_and_nothing_else(
    test_app, authenticated_client, test_pod, fixed_test_user
) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id, "email-triage")
    await _define(authenticated_client, pod_id, "legal-triage")
    created = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": "sorter", "description": "Sorts the inbox."},
    )
    assert created.status_code == 201, created.text
    granted = await authenticated_client.put(
        f"/pods/{pod_id}/functions/sorter/permissions",
        json={
            "grants": [
                {
                    "resource_type": "decider",
                    "resource_name": "email-triage",
                    "permission_ids": ["decider.execute"],
                }
            ]
        },
    )
    assert granted.status_code == 200, granted.text
    token = await get_user_token(
        UUID(fixed_test_user["id"]),
        delegation_claims=build_delegation_claims(
            workload_type="function",
            workload_id=UUID(created.json()["id"]),
            pod_id=UUID(pod_id),
            session_id=uuid4().hex,
            invoked_by_user_id=UUID(fixed_test_user["id"]),
            workload_name="sorter",
            scope=None,
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as sorter:
        asked = await sorter.post(
            f"/pods/{pod_id}/decisions",
            json={"decider": "email-triage", "state": BOBS},
        )
        rows = await sorter.post(
            f"/pods/{pod_id}/decisions/rows",
            json={"decider": "email-triage", "rows": [BOBS]},
        )
        tested = await sorter.post(
            f"/pods/{pod_id}/deciders/test",
            json={"decider": "email-triage", "rows": [{"state": BOBS}]},
        )
        other = await sorter.post(
            f"/pods/{pod_id}/decisions",
            json={"decider": "legal-triage", "state": BOBS},
        )
        inline = await sorter.post(
            f"/pods/{pod_id}/decisions",
            json={"definition": TRIAGE, "state": BOBS},
        )

    assert asked.status_code == 200, asked.text
    assert rows.status_code == 200, rows.text
    assert tested.status_code == 200, tested.text
    assert other.status_code == 403, other.text
    assert inline.status_code == 403, inline.text
