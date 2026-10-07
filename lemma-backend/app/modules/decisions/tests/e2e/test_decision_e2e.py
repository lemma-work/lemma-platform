"""`POST /pods/{pod_id}/decisions` through the real app: auth, metering, failure.

The provider here is the e2e stand-in model, which answers every question with
its first allowed value. What these tests are about is everything around it --
who may ask, from where, what is metered, and what a caller sees when the
provider does not answer.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from pydantic import SecretStr
from sqlalchemy import select

from app.modules.decisions.config import decisions_settings
from app.modules.pod.tests.e2e.workload_permissions.harness import (
    FUNCTION,
    create_function,
    create_pod,
    mint_workload_client,
)
from app.modules.test_support.e2e_authz import (
    auth_headers,
    invite_org_member,
    signup_user,
)
from app.modules.usage.infrastructure.models import UsageRecord

pytestmark = pytest.mark.e2e

BODY = {
    "instruction": "Triage this support email.",
    "evidence": {"subject": "Charged twice", "body": "Please refund one."},
    "schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "oneOf": [
                    {"const": "billing", "description": "Money"},
                    {"const": "bug", "description": "Something broken"},
                ],
                "description": "What is it about?",
            },
            "urgent": {"type": "boolean", "description": "Reply today?"},
            "severity": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "How bad?",
            },
        },
    },
}


async def test_a_member_gets_one_typed_answer_per_question(
    authenticated_client, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(f"/pods/{pod_id}/decisions", json=BODY)

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert set(body["answers"]) == {"category", "urgent", "severity"}
    assert body["answers"]["category"]["value"] in {"billing", "bug"}
    assert body["answers"]["severity"]["value"] in {1, 2, 3}
    assert body["provider"] == "model"


async def test_a_decision_is_metered_to_the_person_and_pod(
    authenticated_client, fixed_test_org, fixed_test_user, db_session
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(f"/pods/{pod_id}/decisions", json=BODY)
    assert response.status_code == status.HTTP_200_OK, response.text

    records = (
        await db_session.scalars(
            select(UsageRecord).where(
                UsageRecord.pod_id == UUID(pod_id),
                UsageRecord.source_type == "decision",
            )
        )
    ).all()
    assert records, "a decision on the system model left no usage record"
    assert {record.user_id for record in records} == {UUID(fixed_test_user["id"])}


async def test_a_function_decides_in_its_own_pod_and_no_other(
    authenticated_client, fixed_test_org, fixed_test_user, test_app
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    other_pod_id = await create_pod(authenticated_client, fixed_test_org)
    name = f"triage_{uuid4().hex[:8]}"
    function = await create_function(authenticated_client, pod_id, name)
    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=FUNCTION,
        workload_id=function["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    try:
        own = await client.post(f"/pods/{pod_id}/decisions", json=BODY)
        other = await client.post(f"/pods/{other_pod_id}/decisions", json=BODY)
    finally:
        await client.aclose()

    assert own.status_code == status.HTTP_200_OK, own.text
    assert other.status_code == status.HTTP_403_FORBIDDEN, other.text


async def test_a_colleague_outside_the_pod_is_refused(
    authenticated_client, fixed_test_org, async_client
):
    """In the organization, not in the pod: a decision is the pod's to ask."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    colleague = await signup_user(async_client, "decisions-colleague")
    await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=colleague,
    )

    response = await async_client.post(
        f"/pods/{pod_id}/decisions", json=BODY, headers=auth_headers(colleague)
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "POD_MEMBERSHIP_REQUIRED"


async def test_questions_outside_the_subset_are_a_422_listing_each_problem(
    authenticated_client, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    body = {
        **BODY,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Summarise it"},
                "urgent": {"type": "boolean"},
            },
        },
    }

    response = await authenticated_client.post(f"/pods/{pod_id}/decisions", json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, response.text
    paths = {problem["path"] for problem in response.json()["details"]}
    assert paths == {
        "schema.properties.summary",
        "schema.properties.urgent.description",
    }


async def test_oversized_evidence_is_refused_not_cut(
    authenticated_client, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/decisions", json={**BODY, "evidence": "x" * 70_000}
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["code"] == "DECISION_INPUT_TOO_LARGE"


async def test_an_unreachable_provider_is_a_503_not_an_answer(
    authenticated_client, fixed_test_org, monkeypatch
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    monkeypatch.setattr(decisions_settings, "decision_provider", "typesafe")
    monkeypatch.setattr(decisions_settings, "typesafe_api_key", SecretStr("sk-e2e"))
    # Nothing listens on the discard port, so the connection is refused at once.
    monkeypatch.setattr(decisions_settings, "typesafe_base_url", "http://127.0.0.1:9")

    response = await authenticated_client.post(f"/pods/{pod_id}/decisions", json=BODY)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, response.text
    assert response.json()["code"] == "DECISION_PROVIDER_UNAVAILABLE"
    assert response.json()["details"] == {"reason": "transport"}


async def test_over_the_organization_rate_a_429_says_when_to_retry(
    authenticated_client, fixed_test_org, monkeypatch
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    monkeypatch.setattr(decisions_settings, "decision_rate_limit_per_minute", 1)

    first = await authenticated_client.post(f"/pods/{pod_id}/decisions", json=BODY)
    second = await authenticated_client.post(f"/pods/{pod_id}/decisions", json=BODY)

    assert first.status_code == status.HTTP_200_OK, first.text
    assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS, second.text
    assert 1 <= int(second.headers["retry-after"]) <= 60
    assert second.json()["code"] == "DECISION_RATE_LIMITED"
