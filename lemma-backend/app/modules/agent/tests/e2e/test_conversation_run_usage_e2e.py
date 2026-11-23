"""Reading one run of a conversation back, with the model tokens it used.

A plugin that runs an agent once per item meters that item by the run's tokens.
The ledger rows are written directly, the way the metered model and an Agent
Host's report leave them, so the route's sum is checked against rows whose
numbers are known.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    running_run,
    seeded_conversation,
)
from app.modules.test_support.e2e_authz import auth_headers
from app.modules.usage.infrastructure.models import UsageRecord

pytestmark = [pytest.mark.e2e]


def _call(
    seeded: Seeded,
    run_id: UUID,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached: int | None = None,
    source: str = "agent_run",
) -> UsageRecord:
    return UsageRecord(
        organization_id=None,
        pod_id=seeded.pod_id,
        user_id=seeded.user_id,
        agent_id=seeded.agent_id,
        conversation_id=seeded.conversation_id,
        agent_run_id=run_id,
        source_type=source,
        source_id=str(run_id),
        profile_id="system:lemma",
        profile_scope="SYSTEM",
        model_name=model,
        usage_kind="llm",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        units=0.0,
        cost_usd=0.5,
        status="COMPLETED",
        record_metadata={},
        occurred_at=datetime.now(timezone.utc),
    )


async def test_a_run_reads_back_the_tokens_its_model_calls_used(scenario):
    seeded = await seeded_conversation(scenario)
    run_id = await running_run(seeded)
    other_run = await running_run(
        await seeded_conversation(scenario)
    )  # another conversation's run
    async with async_session_maker() as session:
        session.add_all(
            [
                _call(
                    seeded,
                    run_id,
                    model="model-a",
                    input_tokens=100,
                    output_tokens=20,
                    cached=60,
                ),
                _call(
                    seeded,
                    run_id,
                    model="model-a",
                    input_tokens=150,
                    output_tokens=30,
                    cached=120,
                ),
                _call(
                    seeded, run_id, model="model-b", input_tokens=10, output_tokens=5
                ),
                # Kept after the run ended: not part of its answer.
                _call(
                    seeded,
                    run_id,
                    model="model-a",
                    input_tokens=9000,
                    output_tokens=900,
                    source="agent_memory",
                ),
            ]
        )
        await session.commit()
    base = f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}"

    response = await scenario.owner_client.get(f"{base}/runs/{run_id}")

    assert response.status_code == status.HTTP_200_OK, response.text
    run = response.json()
    assert run["id"] == str(run_id)
    assert run["conversation_id"] == str(seeded.conversation_id)
    assert run["status"] == "RUNNING"
    assert run["usage"] == {
        "input_tokens": 260,
        "output_tokens": 55,
        "cached_input_tokens": 180,
        "cache_write_tokens": 0,
        "models": ["model-a", "model-b"],
    }
    # Tokens, never money.
    assert "cost" not in response.text

    conversation = await scenario.owner_client.get(base)
    assert conversation.status_code == status.HTTP_200_OK, conversation.text
    assert conversation.json()["last_run_id"] == str(run_id)

    elsewhere = await scenario.owner_client.get(f"{base}/runs/{other_run}")
    assert elsewhere.status_code == status.HTTP_404_NOT_FOUND, elsewhere.text
    missing = await scenario.owner_client.get(f"{base}/runs/{uuid4()}")
    assert missing.status_code == status.HTTP_404_NOT_FOUND, missing.text


async def test_a_run_with_nothing_recorded_has_no_usage(scenario):
    seeded = await seeded_conversation(scenario)
    run_id = await running_run(seeded)

    response = await scenario.owner_client.get(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/runs/{run_id}"
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["usage"] is None


async def test_another_member_cannot_read_somebody_elses_run(scenario):
    seeded = await seeded_conversation(scenario)
    run_id = await running_run(seeded)
    member = await scenario.create_user("run-reader")
    await scenario.add_user_to_pod(user=member, role="POD_EDITOR")

    response = await scenario.async_client.get(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/runs/{run_id}",
        headers=auth_headers(member),
    )

    assert response.status_code in {
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    }, response.text
