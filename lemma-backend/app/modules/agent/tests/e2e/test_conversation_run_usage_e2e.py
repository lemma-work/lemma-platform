"""Reading one run of a conversation back, with the model tokens it used, and
what the whole conversation cost its owner.

A plugin that runs an agent once per item meters that item by the run's tokens.
The ledger rows are written directly, the way the metered model and an Agent
Host's report leave them, so the route's sum is checked against rows whose
numbers are known.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.models import AgentRunModel
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


# --- what the whole conversation cost ---


async def _finish(run_id: UUID) -> None:
    """End a seeded run, so the conversation can start its next one."""
    async with async_session_maker() as session:
        await session.execute(
            update(AgentRunModel)
            .where(AgentRunModel.id == run_id)
            .values(
                status=AgentRunStatus.COMPLETED.value,
                finished_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


def _priced(
    seeded: Seeded,
    run_id: UUID,
    *,
    cost: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    kind: str = "llm",
    user_id: UUID | None = None,
    source: str = "agent_run",
) -> UsageRecord:
    row = _call(
        seeded,
        run_id,
        model="model-a",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        source=source,
    )
    row.cost_usd = cost
    row.usage_kind = kind
    if user_id is not None:
        row.user_id = user_id
    return row


async def test_a_conversation_reads_back_what_its_runs_cost(scenario):
    seeded = await seeded_conversation(scenario)
    answered = await running_run(seeded)
    await _finish(answered)
    silent = await running_run(seeded)  # a harness that reports no usage
    elsewhere = await running_run(await seeded_conversation(scenario))
    async with async_session_maker() as session:
        session.add_all(
            [
                _priced(
                    seeded, answered, cost=0.25, input_tokens=100, output_tokens=20
                ),
                _priced(seeded, answered, cost=0.5, input_tokens=300, output_tokens=40),
                # The title, written after the run: part of what it cost.
                _priced(
                    seeded,
                    answered,
                    cost=0.01,
                    input_tokens=50,
                    output_tokens=5,
                    source="conversation_title",
                ),
                # Machine time: cost, but no tokens and no model call.
                _priced(seeded, answered, cost=0.2, input_tokens=7, kind="compute"),
                # Recorded against somebody else: never read here.
                _priced(seeded, answered, cost=9.0, user_id=uuid4()),
                # Another conversation's run.
                _priced(seeded, elsewhere, cost=4.0, input_tokens=999),
            ]
        )
        await session.commit()
    path = f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/usage"

    response = await scenario.owner_client.get(path)

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["conversation_id"] == str(seeded.conversation_id)
    assert body["totals"]["cost_usd"] == pytest.approx(0.96)
    assert body["totals"]["input_tokens"] == 450
    assert body["totals"]["output_tokens"] == 65
    assert body["totals"]["model_calls"] == 3
    assert body["run_count"] == 2
    assert body["unreported_run_count"] == 1
    assert body["has_more_runs"] is False
    runs = {run["agent_run_id"]: run for run in body["runs"]}
    assert set(runs) == {str(answered), str(silent)}
    assert runs[str(answered)]["usage"]["cost_usd"] == pytest.approx(0.96)
    assert runs[str(answered)]["status"] == "COMPLETED"
    assert runs[str(silent)]["usage"] is None
    assert body["runs"][0]["agent_run_id"] == str(silent), "newest first"


async def test_another_member_cannot_read_what_somebody_elses_conversation_cost(
    scenario,
):
    seeded = await seeded_conversation(scenario)
    member = await scenario.create_user("usage-reader")
    await scenario.add_user_to_pod(user=member, role="POD_EDITOR")
    outsider = await scenario.create_user("usage-outsider")
    path = f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/usage"

    as_member = await scenario.async_client.get(path, headers=auth_headers(member))
    as_outsider = await scenario.async_client.get(path, headers=auth_headers(outsider))

    assert as_member.status_code == status.HTTP_404_NOT_FOUND, as_member.text
    assert as_outsider.status_code in {
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    }, as_outsider.text
    assert "cost_usd" not in as_member.text + as_outsider.text
