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

from typing import Any
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

SurfaceContext = SurfaceChatContext | SurfaceReplyContext
ScriptTurn = dict[str, Any]  # {"text": str | None, "tool_calls": list[dict]}


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


# ---------------------------------------------------------------------------
# Script-entry DSL builders
# ---------------------------------------------------------------------------


def script_text(text: str) -> ScriptTurn:
    """A turn with only assistant text (a final reply, or narration)."""
    return {"text": text, "tool_calls": []}


def script_tool_call(
    tool_name: str,
    args: dict[str, Any],
    *,
    tool_call_id: str | None = None,
    text: str | None = None,
) -> ScriptTurn:
    """Generic one-tool-call turn — the primitive the builders below wrap.

    Always pass an explicit ``tool_call_id`` for ask_user/request_approval so
    the test can reference it later (native-submission callback ids embed it).
    """
    call: dict[str, Any] = {"tool_name": tool_name, "args": args}
    if tool_call_id is not None:
        call["tool_call_id"] = tool_call_id
    return {"text": text, "tool_calls": [call]}


def script_ask_user(
    questions: list[dict[str, Any]],
    *,
    tool_call_id: str = "tool-ask-1",
    text: str | None = None,
) -> ScriptTurn:
    """``questions``: list of ``{"question", "header", "options": [{"label", ...}], "multi_select"?}``.

    Wraps in ``{"request": {"questions": questions}}`` — the real ``ask_user``
    tool function's single param is named ``request`` (``AskUserRequest``).
    """
    return script_tool_call(
        "ask_user",
        {"request": {"questions": questions}},
        tool_call_id=tool_call_id,
        text=text,
    )


def script_request_approval(
    *,
    tool_name: str,
    args: dict[str, Any],
    title: str,
    reason: str | None = None,
    tool_call_id: str = "tool-approval-1",
    text: str | None = None,
) -> ScriptTurn:
    """FLAT args, matching the real ``request_approval(tool_name, args, title,
    reason=None, payload=None)`` signature — NOT wrapped in a "request" key.

    ``tool_name``/``args`` here are the INNER tool being requested approval
    for (e.g. ``tool_name="say"``); do not confuse with the OUTER scripted
    tool name, which is always ``"request_approval"`` itself.
    """
    call_args: dict[str, Any] = {"tool_name": tool_name, "args": args, "title": title}
    if reason is not None:
        call_args["reason"] = reason
    return script_tool_call(
        "request_approval", call_args, tool_call_id=tool_call_id, text=text
    )


def script_display_resource(
    *,
    type: str,  # noqa: A002 - matches the real field name
    path: str | None = None,
    name: str | None = None,
    tool_call_id: str = "tool-display-1",
    text: str | None = None,
    **extra: Any,
) -> ScriptTurn:
    request: dict[str, Any] = {"type": type}
    if path is not None:
        request["path"] = path
    if name is not None:
        request["name"] = name
    request.update(extra)
    return script_tool_call(
        "display_resource", {"request": request}, tool_call_id=tool_call_id, text=text
    )


def script_say(
    text_to_speak: str,
    *,
    tool_call_id: str = "tool-say-1",
    voice: str | None = None,
    output_file_path: str | None = None,
    text: str | None = None,
) -> ScriptTurn:
    request: dict[str, Any] = {"text": text_to_speak}
    if voice is not None:
        request["voice"] = voice
    if output_file_path is not None:
        request["output_file_path"] = output_file_path
    return script_tool_call(
        "say", {"request": request}, tool_call_id=tool_call_id, text=text
    )


def script_email_reply(
    tool_name: str,  # "gmail_reply_email" | "outlook_reply_email" | "resend_reply_email"
    content: str,
    *,
    content_type: str = "markdown",
    attachment_paths: list[str] | None = None,
    subject: str | None = None,
    tool_call_id: str = "tool-email-reply-1",
    text: str | None = None,
) -> ScriptTurn:
    request: dict[str, Any] = {"content": content, "content_type": content_type}
    if attachment_paths:
        request["attachment_paths"] = attachment_paths
    if subject is not None:
        request["subject"] = subject
    return script_tool_call(
        tool_name, {"request": request}, tool_call_id=tool_call_id, text=text
    )


def script_progress(
    comments: list[str],
    *,
    final_text: str = "All done.",
    tool_name: str,
) -> list[ScriptTurn]:
    """One turn per comment (each its own real tool call, so the observer sees
    N separate TOOL_CALL messages to stream), then a final text turn.

    ``tool_name`` MUST be a real tool actually present in the test agent's
    resolved toolset (e.g. a platform's own read-only tool, such as
    ``slack_get_recent_channel_messages``) — the mock model emits a genuine
    ``ToolCallPart`` that real pydantic-ai toolset resolution must be able to
    dispatch AND validate. Every platform tool takes a single ``request: Model``
    parameter, so ``comment`` is nested under ``request`` — matching both what
    real validation expects (an unrecognized ``comment`` field inside `request`
    is silently ignored; no platform tool model uses ``extra="forbid"``) and
    what the progress observer's ``_find_comment`` reads (it checks top-level
    ``comment`` first, then recurses into ``request``). Pick a tool whose other
    ``request`` fields are all optional (e.g. ``limit``/``include_current_thread``
    on ``SlackRecentChannelMessagesParams``) so the empty-otherwise ``request``
    still validates.
    """
    turns = [
        script_tool_call(
            tool_name, {"request": {"comment": c}}, tool_call_id=f"tool-progress-{i}"
        )
        for i, c in enumerate(comments)
    ]
    turns.append(script_text(final_text))
    return turns
