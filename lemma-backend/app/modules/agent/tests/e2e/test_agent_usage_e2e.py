from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.models import AgentRuntimeProfileModel
from app.modules.agent.services.runtime_profile_service import _load_runtime_env
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_api_key,
    system_lemma_available,
    system_lemma_default_model,
    system_lemma_model_names,
)
from app.modules.usage.infrastructure.models import UsageRecord

pytestmark = pytest.mark.e2e

# Resolved at import time from backend/.env or environment — never hardcoded.
SYSTEM_LEMMA_DEFAULT_MODEL = system_lemma_default_model()


async def _create_test_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Usage Agent Pod {uuid4().hex[:8]}",
            "description": "Agent usage E2E pod",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _usage_record(
    *,
    org_id: str,
    user_id: str,
    cost_usd: float,
    occurred_at: datetime | None = None,
) -> UsageRecord:
    occurred_at = occurred_at or datetime.now(timezone.utc)
    return UsageRecord(
        organization_id=UUID(org_id),
        pod_id=uuid4(),
        user_id=UUID(user_id),
        agent_id=uuid4(),
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        source_type="agent_run",
        source_id=str(uuid4()),
        profile_id="system:lemma",
        profile_scope="SYSTEM",
        model_name=SYSTEM_LEMMA_DEFAULT_MODEL,
        usage_kind="LLM",
        input_tokens=10,
        output_tokens=10,
        units=0.0,
        cost_usd=cost_usd,
        status=AgentRunStatus.COMPLETED.value,
        record_metadata={},
        occurred_at=occurred_at,
    )


async def _collect_sse_lines(line_iterator) -> list[dict]:
    events: list[dict] = []
    async with asyncio.timeout(180):
        async for line in line_iterator:
            if not line.startswith("data: "):
                continue
            payload = json.loads(line.removeprefix("data: "))
            events.append(payload)
            if payload["type"] in {"completed", "stopped", "error"}:
                break
    return events


async def _post_sse(client, url: str, payload: dict) -> list[dict]:
    async with client.stream("POST", url, json=payload, timeout=180) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise AssertionError(body.decode())
        return await _collect_sse_lines(response.aiter_lines())


async def _wait_for_usage_event(
    authenticated_client,
    *,
    org_id: str,
    pod_id: str,
    agent_id: str,
    user_id: str,
    agent_run_id: str,
    model_name: str = SYSTEM_LEMMA_DEFAULT_MODEL,
) -> dict:
    for _ in range(60):
        response = await authenticated_client.get(
            f"/usage/organizations/{org_id}/events",
            params={
                "pod_id": pod_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "model_name": model_name,
                "usage_kind": "LLM",
                "days": 1,
            },
        )
        assert response.status_code == 200, response.text
        events = response.json()["items"]
        for event in events:
            if event["agent_run_id"] == agent_run_id:
                return event
        await asyncio.sleep(0.5)
    raise AssertionError(f"Usage event for run {agent_run_id} was not recorded")


@pytest.mark.real_llm
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_agent_run_records_usage_and_usage_apis_filter_it(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    worker,
    monkeypatch,
):
    _ = worker
    real_api_key = system_lemma_api_key()
    monkeypatch.setenv("LEMMA_OPENAI_API_KEY", real_api_key)
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    org_id = fixed_test_org["id"]
    user_id = fixed_test_user["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Usage Agent",
            "instruction": "Answer briefly and directly.",
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent = create_agent.json()

    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "usage_agent", "title": "Usage tracking"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = create_conversation.json()["id"]

    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Say only: usage tracking works."},
    )
    assert events[-1]["type"] == "completed", events
    assert events[-1]["data"]["status"] == AgentRunStatus.COMPLETED.value, events
    agent_run_id = events[-1]["agent_run_id"]

    usage_event = await _wait_for_usage_event(
        authenticated_client,
        org_id=org_id,
        pod_id=pod_id,
        agent_id=agent["id"],
        user_id=user_id,
        agent_run_id=agent_run_id,
    )
    assert usage_event["organization_id"] == org_id
    assert usage_event["pod_id"] == pod_id
    assert usage_event["user_id"] == user_id
    assert usage_event["agent_id"] == agent["id"]
    assert usage_event["conversation_id"] == conversation_id
    assert usage_event["model_name"] == SYSTEM_LEMMA_DEFAULT_MODEL
    assert usage_event["usage_kind"] == "llm"
    assert usage_event["status"] == AgentRunStatus.COMPLETED.value
    assert usage_event["input_tokens"] > 0
    assert usage_event["output_tokens"] > 0
    assert usage_event["total_tokens"] == (
        usage_event["input_tokens"] + usage_event["output_tokens"]
    )
    assert usage_event["cost_usd"] > 0

    summary = await authenticated_client.get(
        f"/usage/organizations/{org_id}/summary",
        params={"pod_id": pod_id, "agent_id": agent["id"], "user_id": user_id, "days": 1},
    )
    assert summary.status_code == 200, summary.text
    summary_payload = summary.json()
    assert summary_payload["total_tokens"] >= usage_event["total_tokens"]
    assert summary_payload["system_cost_usd"] >= usage_event["cost_usd"]
    assert summary_payload["total_by_model"][
        SYSTEM_LEMMA_DEFAULT_MODEL
    ]["total_tokens"] >= usage_event["total_tokens"]
    assert summary_payload["total_by_kind"]["llm"]["total_tokens"] >= usage_event[
        "total_tokens"
    ]

    stats = await authenticated_client.get(
        f"/usage/organizations/{org_id}/stats",
        params={
            "pod_id": pod_id,
            "agent_id": agent["id"],
            "user_id": user_id,
            "group_by": "model",
            "granularity": "day",
            "days": 1,
        },
    )
    assert stats.status_code == 200, stats.text
    stats_items = stats.json()["items"]
    assert any(
        item["group"] == SYSTEM_LEMMA_DEFAULT_MODEL
        and item["total_tokens"] >= usage_event["total_tokens"]
        for item in stats_items
    )

    limits = await authenticated_client.get(
        f"/usage/organizations/{org_id}/limits"
    )
    assert limits.status_code == 200, limits.text
    limits_payload = limits.json()
    assert limits_payload["organization_id"] == org_id
    assert limits_payload["user_id"] == user_id
    assert limits_payload["allowed"] is True
    assert limits_payload["org_monthly"]["used_usd"] >= usage_event["cost_usd"]


@pytest.mark.real_llm
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_non_default_model_run_records_nonzero_cost(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    worker,
    monkeypatch,
):
    # Regression for the metering breakaway: a system:lemma run on a non-default
    # model must persist a usage record with nonzero cost and count toward limits.
    # Before the fix, some models had no pricing entry so recording raised and
    # dropped the record entirely (the model could be used indefinitely past limits).
    models = system_lemma_model_names()
    default = system_lemma_default_model()
    non_default = next((m for m in models if m != default), None)
    if non_default is None:
        pytest.skip(
            "Only one model configured in LEMMA_OPENAI_MODEL_NAMES — "
            "cannot test non-default model cost tracking."
        )

    _ = worker
    real_api_key = system_lemma_api_key()
    monkeypatch.setenv("LEMMA_OPENAI_API_KEY", real_api_key)
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    org_id = fixed_test_org["id"]
    user_id = fixed_test_user["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Alt Model Usage Agent",
            "instruction": "Answer briefly and directly.",
            "agent_runtime": {"profile_id": "system:lemma", "model_name": non_default},
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent = create_agent.json()

    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "alt_model_usage_agent", "title": "Alt model usage tracking"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = create_conversation.json()["id"]

    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Say only: usage tracking works."},
    )
    assert events[-1]["type"] == "completed", events
    assert events[-1]["data"]["status"] == AgentRunStatus.COMPLETED.value, events
    agent_run_id = events[-1]["agent_run_id"]

    usage_event = await _wait_for_usage_event(
        authenticated_client,
        org_id=org_id,
        pod_id=pod_id,
        agent_id=agent["id"],
        user_id=user_id,
        agent_run_id=agent_run_id,
        model_name=non_default,
    )
    assert usage_event["model_name"] == non_default
    assert usage_event["input_tokens"] > 0
    assert usage_event["cost_usd"] > 0

    limits = await authenticated_client.get(f"/usage/organizations/{org_id}/limits")
    assert limits.status_code == 200, limits.text
    assert limits.json()["org_monthly"]["used_usd"] >= usage_event["cost_usd"]


@pytest.mark.skipif(
    os.getenv("LEMMA_RUN_PROVIDER_E2E") != "1",
    reason="Set LEMMA_RUN_PROVIDER_E2E=1 to run real provider-backed e2e tests.",
)
async def test_agent_run_uses_user_added_fireworks_openai_compatible_profile(
    authenticated_client,
    fixed_test_org,
    worker,
    db_session,
    monkeypatch,
):
    _ = worker
    _load_runtime_env()
    fireworks_api_key = os.getenv("FIREWORKS_API_KEY")
    if not fireworks_api_key:
        pytest.skip("FIREWORKS_API_KEY is required for real Fireworks profile e2e.")
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    create_profile = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Fireworks Custom {uuid4().hex[:8]}",
            "base_url": "https://api.fireworks.ai/inference/v1",
            "api_key": fireworks_api_key,
            "default_model_name": "accounts/fireworks/models/kimi-k2p6",
            "model_names": ["accounts/fireworks/models/kimi-k2p6"],
        },
    )
    assert create_profile.status_code == 201, create_profile.text
    profile_payload = create_profile.json()
    assert profile_payload["has_credentials"] is True
    assert "credentials" not in profile_payload
    profile_id = profile_payload["id"]

    stored_profile = await db_session.scalar(
        select(AgentRuntimeProfileModel).where(
            AgentRuntimeProfileModel.id == UUID(profile_id)
        )
    )
    assert stored_profile is not None
    assert stored_profile.credentials["_encrypted"] == "lemma-secret-v2"
    assert fireworks_api_key not in str(stored_profile.credentials)

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Custom Fireworks Agent",
            "instruction": "Answer briefly and directly.",
            "agent_runtime": {"profile_id": profile_id},
        },
    )
    assert create_agent.status_code == 201, create_agent.text

    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "custom_fireworks_agent", "title": "Custom Fireworks"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = create_conversation.json()["id"]

    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Say only: custom fireworks profile works."},
    )

    assert events[-1]["type"] == "completed", events
    assert events[-1]["data"]["status"] == AgentRunStatus.COMPLETED.value, events
