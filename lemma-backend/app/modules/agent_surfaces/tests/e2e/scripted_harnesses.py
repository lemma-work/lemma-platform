"""Deterministic, scriptable agent harnesses for surface e2e tests.

These let a test drive the real ingress → run → observer → egress pipeline with
no LLM: each harness yields a fixed sequence of agent events (a tool call, a
WAITING pause, progress activity, or a final answer). Paired with the fake
platform servers, they exercise the surface features end-to-end.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunStatus,
    HarnessKind,
    HarnessOptions,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _ensure_e2e_runtime_profile,
    _latest_agent_run,
    _latest_user_text,
)


def _ask_user_return(messages: Sequence[Message], tool_call_id: str):
    """The synthesized ask_user tool_result for a resolved pause, or None."""
    for message in messages:
        if (
            message.kind == MessageKind.TOOL_RETURN
            and message.tool_call_id == tool_call_id
        ):
            return message.tool_result
    return None


class AskUserHarness:
    """Asks one or more questions on the first run; on resume (once the answer
    has been synthesized into history) echoes the received answer as the final
    reply. ``resumed_answer`` captures the answer the agent saw."""

    kind = HarnessKind.LEMMA

    def __init__(self, *, questions: list[dict], tool_call_id: str = "tool-ask-1"):
        self.questions = questions
        self.tool_call_id = tool_call_id
        self.resumed_answer: dict | None = None

    async def run(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        del agent, conversation, options
        answer = _ask_user_return(messages, self.tool_call_id)
        if answer is None:
            yield AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_call(
                    tool_name="ask_user",
                    tool_call_id=self.tool_call_id,
                    tool_args={"request": {"questions": self.questions}},
                ),
                agent_run_id=agent_run_id,
            )
            yield AgentEvent(
                type=AgentEventType.WAITING,
                data={
                    "tool_call_id": self.tool_call_id,
                    "kind": "ask_user",
                    "conversation_id": str(ctx.conversation_id),
                },
                agent_run_id=agent_run_id,
            )
            return
        self.resumed_answer = answer if isinstance(answer, dict) else None
        yield AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_text(
                f"Thanks — recorded: {json.dumps(answer)}",
                metadata={"is_final_answer": True},
            ),
            agent_run_id=agent_run_id,
        )
        yield AgentEvent(
            type=AgentEventType.COMPLETED,
            data={"conversation_id": str(ctx.conversation_id)},
            agent_run_id=agent_run_id,
        )


class StreamingHarness:
    """Emits a sequence of tool-call activity events (each carrying a progress
    ``comment``) to drive live progress, then a final answer."""

    kind = HarnessKind.LEMMA

    def __init__(self, *, comments: list[str], final_text: str = "All done."):
        self.comments = comments
        self.final_text = final_text

    async def run(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        del agent, conversation, messages, options
        for index, comment in enumerate(self.comments):
            yield AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_call(
                    tool_name="web_search",
                    tool_call_id=f"tool-progress-{index}",
                    tool_args={"comment": comment},
                ),
                agent_run_id=agent_run_id,
            )
        yield AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_text(
                self.final_text, metadata={"is_final_answer": True}
            ),
            agent_run_id=agent_run_id,
        )
        yield AgentEvent(
            type=AgentEventType.COMPLETED,
            data={"conversation_id": str(ctx.conversation_id)},
            agent_run_id=agent_run_id,
        )


class RecordPromptHarness:
    """Records the user prompt + latest user-message metadata the agent received
    (after ingress processing) and replies trivially. Used to assert voice
    transcription and group channel-context injection."""

    kind = HarnessKind.LEMMA

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.metadatas: list[dict] = []

    async def run(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        del agent, conversation, options
        self.prompts.append(_latest_user_text(messages))
        latest_user_meta: dict = {}
        for message in reversed(messages):
            if message.role == MessageRole.USER.value:
                latest_user_meta = message.metadata or {}
                break
        self.metadatas.append(latest_user_meta)
        yield AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_text("noted", metadata={"is_final_answer": True}),
            agent_run_id=agent_run_id,
        )
        yield AgentEvent(
            type=AgentEventType.COMPLETED,
            data={"conversation_id": str(ctx.conversation_id)},
            agent_run_id=agent_run_id,
        )


async def run_latest_agent_run(
    db_session,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None,
    harness,
) -> None:
    """Execute the conversation's most recent (RUNNING) agent run with ``harness``.

    Used to drive a resume run that ``resolve_user_approval_internal`` created
    (the FastStream worker isn't run in-process in e2e). Mirrors the runtime-
    profile wiring of ``_run_agent_and_deliver_surface_reply``.
    """
    from app.modules.agent.infrastructure.models import ConversationModel

    db_session.expire_all()
    run = await _latest_agent_run(db_session, conversation_id)
    assert run is not None
    assert run.status == AgentRunStatus.RUNNING.value
    conversation = await db_session.get(ConversationModel, conversation_id)
    assert conversation is not None and conversation.organization_id is not None
    profile_id = await _ensure_e2e_runtime_profile(
        db_session, organization_id=conversation.organization_id
    )
    run.agent_runtime = {"profile_id": profile_id, "model_name": "surface-e2e-model"}
    await db_session.commit()
    run_id = run.id

    runner = AgentRunnerService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        harness_registry=HarnessRegistry([harness]),
    )
    await runner.execute(
        agent_run_id=run_id,
        user_id=user_id,
        pod_id=pod_id,
        agent_name=agent_name,
        observer=SurfaceAgentRunProgressObserver(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            service_factory=build_surface_event_handler,
        ),
    )
    db_session.expire_all()
    completed = await db_session.get(AgentRunModel, run_id)
    assert completed is not None
    assert completed.status == AgentRunStatus.COMPLETED.value
