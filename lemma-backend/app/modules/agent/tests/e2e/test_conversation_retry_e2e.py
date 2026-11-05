"""Retrying a failed run, through the endpoint, against real Postgres.

Retry replays a run from the history it left behind, so the one thing it must
never do is replay a run that already did something: a reply, a tool call, a
note. That guard is a single aggregate over the run's message roles, and a
stand-in repository answers it with whatever boolean the test hands it -- which
is how a filter that never matched could make every failed run "safe to replay"
with the unit suite still green.

Each case here builds the failed run as the product would have left it, one
committed step at a time, and asks the endpoint.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import Response
from starlette import status

from pydantic_ai.messages import ThinkingPart

from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    append,
    finish,
    messages,
    running_run,
    runs,
    seeded_conversation,
)
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e]


async def _failed_after(seeded: Seeded, *drafts: MessageDraft) -> UUID:
    """A run that received the person's message, wrote ``drafts``, and failed."""
    failed = await running_run(seeded)
    await append(
        seeded, failed, MessageDraft.of_text("Summarise it.", role=MessageRole.USER)
    )
    for draft in drafts:
        await append(seeded, failed, draft)
    await finish(failed, status=AgentRunStatus.FAILED)
    return failed


async def _retry(scenario: E2EScenario, seeded: Seeded) -> Response:
    return await scenario.owner_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/retry"
    )


async def _retryable(scenario: E2EScenario, seeded: Seeded) -> bool:
    detail = await scenario.owner_client.get(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}"
    )
    assert detail.status_code == status.HTTP_200_OK, detail.text
    return detail.json()["last_run_retryable"]


async def test_a_failed_run_that_said_nothing_is_run_again_without_a_second_message(
    scenario,
) -> None:
    seeded = await seeded_conversation(scenario)
    failed = await _failed_after(seeded)
    assert await _retryable(scenario, seeded) is True

    response = await _retry(scenario, seeded)

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["started_new_run"] is True
    retry_id = UUID(body["agent_run_id"])
    assert retry_id != failed
    (_, retried) = await runs(seeded.conversation_id)
    assert retried.id == retry_id
    assert retried.status == AgentRunStatus.RUNNING.value
    assert retried.run_metadata["source"] == "manual_retry"
    assert retried.run_metadata["retried_agent_run_id"] == str(failed)
    said = [
        m for m in await messages(seeded.conversation_id) if m.role is MessageRole.USER
    ]
    assert [m.text for m in said] == ["Summarise it."], (
        "the retry replays the person's message rather than sending it again"
    )


async def test_a_failed_run_that_only_thought_is_run_again_from_the_users_turn(
    scenario,
) -> None:
    """Reasoning has no effect to repeat, so it does not take the retry away.

    The retry starts from the person's message, not from the reasoning the
    failed run left after it: that reasoning never reaches the model again.
    """
    seeded = await seeded_conversation(scenario)
    failed = await _failed_after(
        seeded,
        # Signed, so it is a thought the model could be sent again.
        MessageDraft.of_thinking(
            "The document is long; start with the outline.",
            metadata={"thinking_signature": "signed"},
        ),
        MessageDraft.of_thinking(
            "Then the conclusions.", metadata={"thinking_signature": "signed"}
        ),
    )
    assert await _retryable(scenario, seeded) is True

    response = await _retry(scenario, seeded)

    assert response.status_code == status.HTTP_200_OK, response.text
    retry_id = UUID(response.json()["agent_run_id"])
    assert retry_id != failed
    history, prompt = history_and_prompt(
        await messages(seeded.conversation_id),
        protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
        current_run_id=retry_id,
    )
    assert prompt is not None and prompt.endswith("Summarise it.")
    assert not any(
        isinstance(part, ThinkingPart) for message in history for part in message.parts
    )


async def test_retrying_twice_while_the_retry_runs_returns_the_same_run(
    scenario,
) -> None:
    """A double-click, or a client retrying a request that timed out."""
    seeded = await seeded_conversation(scenario)
    await _failed_after(seeded)

    first = await _retry(scenario, seeded)
    again = await _retry(scenario, seeded)

    assert first.status_code == again.status_code == status.HTTP_200_OK, again.text
    assert again.json()["agent_run_id"] == first.json()["agent_run_id"]
    assert again.json()["started_new_run"] is False
    assert len(await runs(seeded.conversation_id)) == 2


async def test_a_retry_that_failed_too_can_be_retried_again(scenario) -> None:
    """The retry owns no message -- the person's turn stays on the run it
    retried -- so it must not read as "nothing to replay" once it fails.

    The usual sequence: the runtime is broken, the person retries before fixing
    it, the retry fails the same way, they fix it and retry again.
    """
    seeded = await seeded_conversation(scenario)
    await _failed_after(seeded)
    first = await _retry(scenario, seeded)
    assert first.status_code == status.HTTP_200_OK, first.text
    first_retry = UUID(first.json()["agent_run_id"])
    await finish(first_retry, status=AgentRunStatus.FAILED)
    assert await _retryable(scenario, seeded) is True

    second = await _retry(scenario, seeded)
    again = await _retry(scenario, seeded)

    assert second.status_code == status.HTTP_200_OK, second.text
    assert second.json()["started_new_run"] is True
    second_retry = UUID(second.json()["agent_run_id"])
    assert second_retry != first_retry
    assert again.status_code == status.HTTP_200_OK, again.text
    assert again.json()["agent_run_id"] == str(second_retry)
    assert again.json()["started_new_run"] is False
    (_, _, newest) = await runs(seeded.conversation_id)
    assert newest.run_metadata["retried_agent_run_id"] == str(first_retry)
    _, prompt = history_and_prompt(
        await messages(seeded.conversation_id), current_run_id=second_retry
    )
    assert prompt is not None and prompt.endswith("Summarise it."), (
        "the second retry replays the person's turn from the original run"
    )


async def test_a_retry_that_replied_before_failing_is_not_retried_again(
    scenario,
) -> None:
    seeded = await seeded_conversation(scenario)
    await _failed_after(seeded)
    first = await _retry(scenario, seeded)
    first_retry = UUID(first.json()["agent_run_id"])
    await append(seeded, first_retry, MessageDraft.of_text("Here is half a summary"))
    await finish(first_retry, status=AgentRunStatus.FAILED)
    assert await _retryable(scenario, seeded) is False

    response = await _retry(scenario, seeded)

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert len(await runs(seeded.conversation_id)) == 2


@pytest.mark.parametrize(
    "work",
    [
        pytest.param(
            MessageDraft.of_text("Here is half a summary"), id="it-had-replied"
        ),
        pytest.param(
            MessageDraft.of_tool_call(
                tool_name="pod_write_record",
                tool_call_id="call-already-ran",
                tool_args={"action": "create", "table_name": "notes"},
            ),
            id="it-had-called-a-tool",
        ),
    ],
)
@pytest.mark.parametrize(
    "thought_first", [False, True], ids=["at-once", "after-thinking"]
)
async def test_a_failed_run_that_already_did_something_is_not_replayed(
    scenario, work: MessageDraft, thought_first: bool
) -> None:
    """Replaying it would say the reply twice, or run the tool twice.

    Reasoning before the work does not make it replayable: the work is done.
    """
    seeded = await seeded_conversation(scenario)
    drafts = [MessageDraft.of_thinking("First, the outline."), work]
    await _failed_after(seeded, *(drafts if thought_first else drafts[1:]))
    assert await _retryable(scenario, seeded) is False

    response = await _retry(scenario, seeded)

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert len(await runs(seeded.conversation_id)) == 1


async def test_a_failed_run_with_no_message_to_replay_is_not_retried(scenario) -> None:
    """Nothing the person said belongs to it, so a retry would run the agent
    on a turn nobody asked for."""
    seeded = await seeded_conversation(scenario)
    empty = await running_run(seeded)
    await finish(empty, status=AgentRunStatus.FAILED)
    assert await _retryable(scenario, seeded) is False

    response = await _retry(scenario, seeded)

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert len(await runs(seeded.conversation_id)) == 1


async def test_a_run_that_did_not_fail_is_not_retried(scenario) -> None:
    seeded = await seeded_conversation(scenario)
    done = await running_run(seeded)
    await finish(done, status=AgentRunStatus.COMPLETED)

    response = await _retry(scenario, seeded)

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert len(await runs(seeded.conversation_id)) == 1


async def test_a_conversation_with_a_run_going_is_not_retried(scenario) -> None:
    """An active run that is not itself a retry: replaying the failed run
    under it would be a second answer to the same question."""
    seeded = await seeded_conversation(scenario)
    await _failed_after(seeded)
    working = await running_run(seeded)

    response = await _retry(scenario, seeded)

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert [run.id for run in await runs(seeded.conversation_id)][-1] == working


async def test_someone_else_s_failed_run_cannot_be_retried(scenario) -> None:
    """A retry runs the agent as the conversation's owner, so another member of
    the pod gets the answer a stranger would: there is nothing here."""
    seeded = await seeded_conversation(scenario)
    await _failed_after(seeded)
    member = await scenario.create_user("retry-member")
    await scenario.add_user_to_pod(user=member, role="POD_EDITOR")

    response = await scenario.async_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/retry",
        headers=auth_headers(member),
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text
    assert len(await runs(seeded.conversation_id)) == 1
