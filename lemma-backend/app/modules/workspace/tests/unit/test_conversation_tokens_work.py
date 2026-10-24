"""A conversation that ends takes its sandbox tokens with it."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.infrastructure.work import facts
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.workspace.events.conversation_tokens_work import (
    WORKSPACE_END_CONVERSATION_TOKENS,
    ConversationEndedPayload,
    conversation_ended,
    conversation_tokens_handler,
)

pytestmark = pytest.mark.unit


def _fact(conversation_status: str) -> AgentRunFinished:
    return AgentRunFinished(
        run_id=uuid4(),
        conversation_id=uuid4(),
        pod_id=uuid4(),
        status="COMPLETED",
        conversation_status=conversation_status,  # type: ignore[arg-type]
        origin="chat",
    )


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "STOPPED"])
def test_a_conversation_that_ends_ends_its_tokens(status: str) -> None:
    assert conversation_ended(_fact(status))


def test_a_run_that_pauses_keeps_them() -> None:
    """An approval or an answer resumes it, and its commands are still running."""
    assert not conversation_ended(_fact("WAITING"))


def test_it_is_subscribed_to_every_finished_run() -> None:
    kinds = {subscription.kind for subscription in facts.subscribers(AgentRunFinished)}
    assert WORKSPACE_END_CONVERSATION_TOKENS in kinds


@pytest.mark.asyncio
async def test_the_end_is_when_the_run_finished_not_when_the_item_ran() -> None:
    """A retry recording "now" would also refuse a run that has started since."""
    ended: list[dict[str, object]] = []

    async def end(**kwargs: object) -> None:
        ended.append(kwargs)

    written = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    conversation_id = uuid4()

    await conversation_tokens_handler(end)(
        SimpleNamespace(item=SimpleNamespace(created_at=written)),  # type: ignore[arg-type]
        ConversationEndedPayload(
            run_id=uuid4(),
            conversation_id=conversation_id,
            conversation_status="COMPLETED",
        ),
    )

    assert ended == [
        {"session_id": str(conversation_id), "ended_at": written.timestamp()}
    ]
