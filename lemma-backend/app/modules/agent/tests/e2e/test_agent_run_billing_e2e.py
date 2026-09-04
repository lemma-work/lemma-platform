"""A run that ends badly is still a run somebody paid for.

`PS-OPS-003` says usage is recorded "whether it succeeded or failed, because a
failed run still costs". It was not. The finalizer's failure path called
`finish(status=FAILED)` with no usage at all, so every run that died -- a provider
error, a cancelled worker, a SIGTERM mid-answer -- billed zero for tokens the
provider had already sold. The harness had gone to some trouble to make the
numbers available at exactly that moment (it emits USAGE from a `finally` before
re-raising, and carries forward the tokens burned by retried attempts); nothing
read them.

Driven through the real HTTP surface with the deterministic model, because the
bug lived in the wiring between three collaborators and not in any one of them.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.test_support.e2e.scripted_model import (
    script_model_error,
    script_tool_call,
    with_usage,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

_FIRST_TURN_INPUT_TOKENS = 4000
_FIRST_TURN_CACHED_TOKENS = 3000
_FIRST_TURN_OUTPUT_TOKENS = 120


async def _create_pod(authenticated_client, fixed_test_org) -> dict[str, object]:
    response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Billing Pod {uuid4().hex[:8]}",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _send(
    authenticated_client, pod_id, conversation_id, content
) -> list[dict[str, object]]:
    url = f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    async with authenticated_client.stream(
        "POST", url, json={"content": content}, timeout=60
    ) as response:
        assert response.status_code == status.HTTP_200_OK, await response.aread()
        events: list[dict[str, object]] = []
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            events.append(event)
            if event["type"] in {"completed", "stopped", "error"}:
                break
        return events


async def _usage_for_run(
    authenticated_client, *, organization_id, run_id
) -> dict[str, object]:
    async def probe() -> dict[str, object] | None:
        response = await authenticated_client.get(
            f"/usage/organizations/{organization_id}/events",
            params={"days": 1},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        # The run's own row, not merely a row carrying its id. History
        # compaction and the vision delegate meter under the same
        # `agent_run_id` with their own `source_type`, so a script long enough
        # to compact would otherwise bind this assertion to whichever row came
        # back first.
        return next(
            (
                item
                for item in response.json()["items"]
                if item["agent_run_id"] == run_id and item["source_type"] == "AGENT_RUN"
            ),
            None,
        )

    return await eventually(
        label=f"usage for the failed run {run_id}",
        probe=probe,
        done=lambda event: event is not None,
        timeout_seconds=20.0,
        interval_seconds=0.2,
    )


@pytest.mark.asyncio
async def test_a_run_that_fails_partway_still_bills_for_what_it_spent(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    e2e_settings,
    worker,
):
    """The first turn really calls the model; the second one dies.

    Scripting the failure on turn *two* is the point. An error raised before the
    provider answers costs nothing and proves nothing -- what has to survive is
    the spend from the turn that did complete before the run came apart.
    """
    del worker
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"billing_{uuid4().hex[:8]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Use the scripted deterministic model.",
            # The system profile, so the run is priced against this deployment's
            # own rates rather than recorded with a null cost.
            "agent_runtime": {"profile_id": "system:lemma"},
            "toolsets": [],
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "metadata": {
                "mock_llm_script": [
                    with_usage(
                        script_tool_call(
                            "write_todos",
                            {"todos": ["- [ ] Spend some tokens"]},
                            tool_call_id="billing-1",
                        ),
                        input_tokens=_FIRST_TURN_INPUT_TOKENS,
                        output_tokens=_FIRST_TURN_OUTPUT_TOKENS,
                        cache_read_tokens=_FIRST_TURN_CACHED_TOKENS,
                    ),
                    script_model_error("generic", message="scripted-billing-failure"),
                ]
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send(
        authenticated_client, pod_id, conversation_id, "Spend then fail."
    )
    assert events[-1]["type"] == "error", events
    run_id = events[-1]["agent_run_id"]

    usage = await _usage_for_run(
        authenticated_client,
        organization_id=fixed_test_org["id"],
        run_id=run_id,
    )

    assert usage["status"] == "FAILED"
    assert usage["input_tokens"] == _FIRST_TURN_INPUT_TOKENS
    assert usage["output_tokens"] == _FIRST_TURN_OUTPUT_TOKENS
    assert usage["cached_input_tokens"] == _FIRST_TURN_CACHED_TOKENS
    assert usage["uncached_input_tokens"] == 1000
    # Priced, not merely counted: the whole point is that the failure is billable.
    assert usage["cost_usd"] is not None
    assert usage["cost_usd"] > 0
    assert usage["cost_source"] == "REGISTERED"
