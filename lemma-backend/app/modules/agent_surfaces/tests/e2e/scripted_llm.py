"""Scripted-LLM mechanism for surface e2e tests.

Replaces the hand-rolled fake ``Harness`` classes that used to live in
``scripted_harnesses.py``. Those yielded ``AgentEvent``s directly, bypassing the
real ``PydanticAIHarness`` — so a scripted "ask_user tool call" never actually
called the real ``ask_user`` tool, never raised the real ``AgentInputRequired``,
and never exercised real toolset resolution.

This module instead scripts the LLM's *token source* only, via the same
mechanism the general (non-surface) agent e2e suite already uses:
``app.modules.agent.infrastructure.harnesses.mock_model.build_mock_model``
reads a flat list of turns off ``conversation.metadata["mock_llm_script"]`` and
returns a deterministic pydantic-ai ``FunctionModel``. The REST of the pipeline
— toolset resolution, real tool execution (including ``ask_user``/
``request_approval`` genuinely raising ``AgentInputRequired``), the real
progress observer, real egress — all run for real, driven by the real
``PydanticAIHarness``. Only the model's next token/tool-call is mocked.

Key mechanism fact (verified against ``mock_model.py``): the turn index is
counted from the *last real user-authored message*, and a resume run's
synthesized tool-return is not a real user message — so a single flat script
set once, before the first run, naturally answers both the initial run and any
resume run(s) it pauses into (ask_user/request_approval). No re-seeding needed
between runs unless a test wants to deliberately diverge.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.harnesses.mock_model import (
    MOCK_SCRIPT_METADATA_KEY,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.conversation_service import suppress_agent_run_enqueue
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
    SurfaceScheduleIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _ensure_e2e_runtime_profile,
    _latest_agent_run,
    E2E_RUNTIME_MODEL_NAME,
)
from app.modules.test_support.e2e.scripted_model import (
    ScriptTurn as ScriptTurn,
    script_ask_user as script_ask_user,
    script_display_resource as script_display_resource,
    script_email_reply as script_email_reply,
    script_progress as script_progress,
    script_request_approval as script_request_approval,
    script_say as script_say,
    script_text as script_text,
    script_tool_call as script_tool_call,
)

SurfaceContext = SurfaceChatContext | SurfaceReplyContext


# ---------------------------------------------------------------------------
# Seeding the script
# ---------------------------------------------------------------------------


async def set_mock_llm_script(
    db_session: AsyncSession,
    *,
    conversation_id: UUID,
    script: list[ScriptTurn],
) -> None:
    """Merge ``mock_llm_script`` onto a conversation's metadata.

    Uses ``jsonb_set`` (via ``ConversationRepository.set_conversation_metadata_key``)
    so sibling keys (``surface_platform``, ``surface_id``, etc.) are untouched.
    Must be called after the conversation exists (i.e. after
    ``prepare_ingress``/``execute_chat``) and before the run is driven.
    """
    uow = SqlAlchemyUnitOfWork(db_session)
    await ConversationRepository(uow).set_conversation_metadata_key(
        conversation_id, MOCK_SCRIPT_METADATA_KEY, script
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Driving the REAL harness against the latest RUNNING run
# ---------------------------------------------------------------------------


async def run_scripted_agent_run(
    db_session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None,
    script: list[ScriptTurn] | None = None,
) -> None:
    """Drive the conversation's latest RUNNING agent run through the real
    ``PydanticAIHarness`` (mock-LLM mode).

    If ``script`` is given, it is (re-)written onto conversation metadata
    before driving the run. Pass it on the first call for a flat script that
    spans an initial run + its resume run(s); omit it on a resume call to keep
    reading the script already persisted from the first call.
    """
    if script is not None:
        await set_mock_llm_script(
            db_session, conversation_id=conversation_id, script=script
        )

    db_session.expire_all()
    run = await _latest_agent_run(db_session, conversation_id)
    assert run is not None
    assert run.status == AgentRunStatus.RUNNING.value
    run_id = run.id
    conversation = await db_session.get(ConversationModel, conversation_id)
    assert conversation is not None
    assert conversation.organization_id is not None
    runtime_profile_id = await _ensure_e2e_runtime_profile(
        db_session, organization_id=conversation.organization_id
    )
    run.agent_runtime = {
        "profile_id": runtime_profile_id,
        "model_name": E2E_RUNTIME_MODEL_NAME,
    }
    await db_session.commit()

    runner = AgentRunnerService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        harness_registry=HarnessRegistry([PydanticAIHarness()]),
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
    # A run that paused on ask_user/request_approval also ends as COMPLETED
    # (the WAITING state lives on the conversation, not the run) — so this
    # assertion holds for both a fully-finished run and a paused one.
    assert completed.status == AgentRunStatus.COMPLETED.value


async def process_ingress_and_run_scripted(
    db_session: AsyncSession,
    request: SurfacePlatformWebhookIngress
    | SurfaceDirectWebhookIngress
    | SurfaceScheduleIngress,
    *,
    script: list[ScriptTurn] | None = None,
) -> SurfaceContext:
    """Process one inbound surface event and drive the real harness.

    Runs ``prepare_ingress`` + ``execute_chat`` (unchanged), then — for a
    ``SurfaceChatContext`` — seeds ``script`` (if given) and drives whichever
    run ``execute_chat`` left RUNNING via ``run_scripted_agent_run``. This
    covers both "a brand new run was started" AND "a pending interaction was
    resolved by this same inbound message and a resume run was created"
    (``request_approval``'s typed "approve"/"deny" reply resumes this way,
    since it's an ordinary text message, not a native form submission).

    Omit ``script`` to get the mock model's built-in unscripted default (a
    short deterministic echo) — zero setup needed for "a run completes" tests.
    """
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    context = await handler.prepare_ingress(request)
    assert context is not None
    await uow.commit()

    with suppress_agent_run_enqueue():
        await handler.execute_chat(context)

    if isinstance(context, SurfaceChatContext):
        await run_scripted_agent_run(
            db_session,
            conversation_id=context.conversation_id,
            user_id=context.user_id,
            pod_id=context.pod_id,
            agent_name=context.agent_name,
            script=script,
        )
    return context


async def resume_latest_scripted_run(
    db_session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None,
) -> None:
    """Drive the resume run a native-interaction submission created.

    Thin wrapper over ``run_scripted_agent_run`` with ``script=None`` — the
    flat script set on the first call is still on conversation metadata and
    keeps being read (see module docstring). Named separately only for
    call-site clarity after ``handler.try_handle_interaction(...)``.
    """
    await run_scripted_agent_run(
        db_session,
        conversation_id=conversation_id,
        user_id=user_id,
        pod_id=pod_id,
        agent_name=agent_name,
        script=None,
    )
