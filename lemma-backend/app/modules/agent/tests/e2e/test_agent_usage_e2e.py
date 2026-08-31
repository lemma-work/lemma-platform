from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.runtime_models import AgentRuntimeProfileModel
from app.modules.agent.services.runtime_system_profiles import _load_runtime_env
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_env_overlay,
    system_lemma_api_key,
    system_lemma_available,
    system_lemma_default_model,
    system_lemma_model_names,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.usage.infrastructure.models import UsageRecord

pytestmark = [pytest.mark.e2e, pytest.mark.provider]

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
    async def probe() -> dict | None:
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
        return next(
            (
                event
                for event in response.json()["items"]
                if event["agent_run_id"] == agent_run_id
            ),
            None,
        )

    return await eventually(
        label=f"usage event for run {agent_run_id} to be recorded",
        probe=probe,
        done=lambda event: event is not None,
        timeout_seconds=30.0,
        interval_seconds=0.15,
    )


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
        params={
            "pod_id": pod_id,
            "agent_id": agent["id"],
            "user_id": user_id,
            "days": 1,
        },
    )
    assert summary.status_code == 200, summary.text
    summary_payload = summary.json()
    assert summary_payload["total_tokens"] >= usage_event["total_tokens"]
    assert summary_payload["system_cost_usd"] >= usage_event["cost_usd"]
    assert (
        summary_payload["total_by_model"][SYSTEM_LEMMA_DEFAULT_MODEL]["total_tokens"]
        >= usage_event["total_tokens"]
    )
    assert (
        summary_payload["total_by_kind"]["llm"]["total_tokens"]
        >= usage_event["total_tokens"]
    )

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

    limits = await authenticated_client.get(f"/usage/organizations/{org_id}/limits")
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
        json={
            "agent_name": "alt_model_usage_agent",
            "title": "Alt model usage tracking",
        },
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


# Two questions of the same size, one answered briefly and one at length. The
# prompt is what is being checked, and it barely differs between them -- so the
# prompt cost should barely differ either.
_SHORT_ANSWER_PROMPT = "Reply with exactly: ok"
_LONG_ANSWER_PROMPT = (
    "Count from 1 to 60. Put each number on its own line, and write nothing else."
)

# What re-summing looks like from the outside. Counting the prompt once per
# streamed chunk instead of once ties the prompt cost to the length of the
# answer, so the long run's prompt inflates by roughly the ratio of the two
# chunk counts while the short run stays cheap.
#
# Asserted as a ratio between two runs rather than a fixed ceiling: a ceiling
# encodes today's system-prompt size, goes stale the moment an instruction is
# added, and fails for a reason that has nothing to do with billing.
_PROMPT_COST_GROWTH_LIMIT = 2.0

# Enough chunks for the multiplier above to be unmistakable if it were applied.
_A_LONG_ANSWER = 40

# A price per token outside this band is not a rate, it is a units error -- a
# catalog entry priced per million as though it were per token, or a missing
# entry quietly costing nothing.
_SANE_COST_PER_TOKEN = (1e-9, 1e-3)


async def _run_once_and_read_usage(
    authenticated_client,
    *,
    org_id: str,
    user_id: str,
    pod_id: str,
    agent: dict,
    model_name: str,
    prompt: str,
    title: str,
) -> dict:
    """One agent turn in its own conversation, and the usage it recorded.

    A fresh conversation each time so the two runs are comparable: replying into
    the first one would carry its history into the second prompt and inflate the
    thing being measured.
    """
    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent["name"], "title": title},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = create_conversation.json()["id"]

    # Fails here when the request carries a field the endpoint does not accept:
    # the provider rejects it outright and the run never reaches COMPLETED.
    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": prompt},
    )
    assert events[-1]["type"] == "completed", events
    assert events[-1]["data"]["status"] == AgentRunStatus.COMPLETED.value, events

    return await _wait_for_usage_event(
        authenticated_client,
        org_id=org_id,
        pod_id=pod_id,
        agent_id=agent["id"],
        user_id=user_id,
        agent_run_id=events[-1]["agent_run_id"],
        model_name=model_name,
    )


def _assert_billed_coherently(usage_event: dict, model_name: str) -> None:
    assert usage_event["input_tokens"] > 0, usage_event
    assert usage_event["output_tokens"] > 0, usage_event
    assert usage_event["total_tokens"] == (
        usage_event["input_tokens"] + usage_event["output_tokens"]
    )
    assert usage_event["cost_usd"] > 0, usage_event

    cost_per_token = usage_event["cost_usd"] / usage_event["total_tokens"]
    low, high = _SANE_COST_PER_TOKEN
    assert low < cost_per_token < high, (
        f"{model_name} priced at {cost_per_token} per token, outside the band a "
        f"real rate falls in."
    )


async def _assert_model_streams_and_bills(
    authenticated_client,
    *,
    org_id: str,
    user_id: str,
    pod_id: str,
    model_name: str,
) -> None:
    """Ask one model two questions of the same size and check what it charged."""
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"Billing Agent {uuid4().hex[:8]}",
            "instruction": "Follow the request exactly. Add no commentary.",
            "agent_runtime": {
                "profile_id": "system:lemma",
                "model_name": model_name,
            },
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent = create_agent.json()

    short_run = await _run_once_and_read_usage(
        authenticated_client,
        org_id=org_id,
        user_id=user_id,
        pod_id=pod_id,
        agent=agent,
        model_name=model_name,
        prompt=_SHORT_ANSWER_PROMPT,
        title=f"Short answer {model_name}",
    )
    long_run = await _run_once_and_read_usage(
        authenticated_client,
        org_id=org_id,
        user_id=user_id,
        pod_id=pod_id,
        agent=agent,
        model_name=model_name,
        prompt=_LONG_ANSWER_PROMPT,
        title=f"Long answer {model_name}",
    )

    _assert_billed_coherently(short_run, model_name)
    _assert_billed_coherently(long_run, model_name)

    # The two runs really did stream at different lengths, which is what makes
    # the comparison below meaningful rather than incidental.
    assert long_run["output_tokens"] > _A_LONG_ANSWER, (model_name, long_run)
    assert long_run["output_tokens"] > short_run["output_tokens"], (
        model_name,
        short_run,
        long_run,
    )

    growth = long_run["input_tokens"] / short_run["input_tokens"]
    assert growth < _PROMPT_COST_GROWTH_LIMIT, (
        f"{model_name} charged {growth:.1f}x the prompt tokens for the longer "
        f"answer to a question of the same size "
        f"({short_run['input_tokens']} -> {long_run['input_tokens']}). The "
        f"prompt is being counted once per streamed chunk rather than once."
    )


@pytest.mark.real_llm
# Two real runs for every model in the catalog, and a catalog may hold a
# reasoning model that spends minutes on one of them. This lane is local and
# manual -- it is not in front of the merge button, so the per-test budget that
# applies there does not, and waiting on a slow model is not a reason to leave
# it uncovered.
@pytest.mark.timeout(1800)
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_every_configured_model_streams_and_bills_what_it_used(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    worker,
    monkeypatch,
):
    """Every model in the catalog must survive a streamed run and bill for it.

    Covers the configured catalog rather than named models, because both
    failures below are per-model and neither is reachable from the default one.

    A model whose endpoint validates its input rejects a request carrying a
    non-standard `stream_options` field, and the run never completes -- so the
    first half of this test is simply that it does, for every model shipped.

    A model that repeats an already-cumulative usage total on every chunk gets
    its prompt counted once per chunk if the streaming handler adds instead of
    replaces. That fails nothing and raises no error; it just bills a number
    nobody can explain. So the second half asks each model two questions of the
    same size, one answered briefly and one at length, and holds the prompt cost
    to the question rather than to the answer.

    One test looping the catalog, not one parametrised per model: the catalog is
    read from the environment, so parametrising makes the collected test count
    differ between a developer's machine and CI -- and the census gate that
    exists to notice a suite quietly dropping out cannot tell that apart from a
    suite quietly dropping out.
    """
    _ = worker
    models = system_lemma_model_names()
    if not models:
        pytest.skip("No models configured in LEMMA_OPENAI_MODEL_NAMES.")

    real_api_key = system_lemma_api_key()
    monkeypatch.setenv("LEMMA_OPENAI_API_KEY", real_api_key)
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    org_id = fixed_test_org["id"]
    user_id = fixed_test_user["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    for model_name in models:
        await _assert_model_streams_and_bills(
            authenticated_client,
            org_id=org_id,
            user_id=user_id,
            pod_id=pod_id,
            model_name=model_name,
        )


@pytest.mark.skipif(
    os.getenv("LEMMA_RUN_PROVIDER_E2E") != "1",
    reason="Set LEMMA_RUN_PROVIDER_E2E=1 to run real provider-backed e2e tests.",
)
async def test_agent_run_uses_user_added_openai_compatible_profile(
    authenticated_client,
    fixed_test_org,
    worker,
    db_session,
):
    _ = worker
    _load_runtime_env()
    provider_env = system_lemma_env_overlay()
    api_key = system_lemma_api_key()
    if not api_key:
        pytest.skip(SYSTEM_LEMMA_SKIP_REASON)
    base_url = provider_env.get("LEMMA_OPENAI_BASE_URL")
    if not base_url:
        pytest.skip("LEMMA_OPENAI_BASE_URL is required for provider profile e2e.")
    model_names = system_lemma_model_names()
    if not model_names:
        pytest.skip("LEMMA_OPENAI_MODEL_NAMES is required for provider profile e2e.")
    default_model = system_lemma_default_model()
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    create_profile = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Custom System Lemma Compatible {uuid4().hex[:8]}",
            "base_url": base_url,
            "api_key": api_key,
            "default_model_name": default_model,
            "model_names": model_names,
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
    assert api_key not in str(stored_profile.credentials)

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Custom Provider Agent",
            "instruction": "Answer briefly and directly.",
            "agent_runtime": {"profile_id": profile_id},
        },
    )
    assert create_agent.status_code == 201, create_agent.text

    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "custom_provider_agent", "title": "Custom Provider"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = create_conversation.json()["id"]

    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Say only: custom provider profile works."},
    )

    assert events[-1]["type"] == "completed", events
    assert events[-1]["data"]["status"] == AgentRunStatus.COMPLETED.value, events
