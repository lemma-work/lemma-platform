"""Snooze toolset: let an agent suspend its own turn and wake later.

Reuses the pause `ask_user` and `request_approval` already have — raise
``AgentInputRequired``, the harness ends the run cleanly, the conversation goes
WAITING, and resolving the pending tool call starts a fresh run that replays the
synthesized return from history. The only new thing here is *who resolves it*: a
scheduler timer rather than a person.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.composition.agent_snooze_scheduler import schedule_snooze_wake
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.wait import AgentConversationWaitEntity, AgentWaitType
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.snooze.models import (
    MAX_SNOOZE_SECONDS,
    MIN_SNOOZE_SECONDS,
    SnoozeRequest,
    SnoozeResponse,
)
from app.modules.agent.tools.tool_errors import AgentInputRequired

logger = get_logger(__name__)

SNOOZE_TOOL_NAME = "snooze"


async def snooze(
    ctx: RunContext[BaseAgentContext], request: SnoozeRequest
) -> SnoozeResponse:
    """Suspend this turn for a while and pick it up later, where you left off.

    USE WHEN the work genuinely has a gap in the middle — you kicked off a build
    and want to check back in ten minutes, you asked someone for an approval and
    need to look again later, something needs time to settle. You wake up in the
    same conversation, with the same history, as though this tool call simply
    took a long time to return.

    CHOOSE `seconds` FROM WHAT YOU ARE WAITING FOR, not out of habit. A job that
    takes about eight minutes deserves one ~500s check, not eight 60s ones. Don't
    snooze in a tight loop to poll something — each wake replays this entire
    conversation, so a poll loop costs far more than it saves. If you're unsure
    how long something takes, prefer one longer sleep and check when you wake.

    YOUR SANDBOX DOES NOT SURVIVE. The workspace container is reclaimed while you
    sleep: files under /workspace, background processes, and your shell's working
    directory are all gone when you wake. Anything you need on the other side must
    be written to the pod (a table or a pod file) BEFORE you call this.

    WAKING PROVES NOTHING HAPPENED. `woke_because` is `TIMER` — your time
    elapsed, and that is all it means. Check the thing you were waiting for
    before acting as though it is done.

    Do NOT use this to wait on a person — ask them and end your turn, and their
    reply starts a fresh run on its own. Do NOT use it to pad out a reply, or to
    wait for something you could just check right now.
    """
    deps = ctx.deps

    if deps.agent_run_id is None:
        return SnoozeResponse(
            success=False, error="snooze requires an active agent run."
        )
    if deps.pod_id is None:
        return SnoozeResponse(
            success=False, error="snooze is only available inside a pod."
        )
    if not getattr(deps, "supports_pause_signal", False):
        # Remote harnesses (Codex / Claude Code / OpenCode) run tools over MCP and
        # own their session, so the run cannot pause mid tool-call. Same contract
        # as ask_user: tell the model rather than hanging.
        return SnoozeResponse(
            success=False,
            interaction_fallback=True,
            message=(
                "This runtime can't suspend a turn. Do what you can now and end "
                "your turn, telling the user what you're waiting for and asking "
                "them to prompt you again when it happens."
            ),
        )
    if not ctx.tool_call_id:
        return SnoozeResponse(
            success=False, error="snooze requires a durable tool call id."
        )
    if request.seconds < MIN_SNOOZE_SECONDS:
        # Rejected rather than clamped: asking for a few seconds means the model
        # has mistaken this for a sleep() rather than a durable suspend, and a
        # silent clamp would teach it nothing.
        return SnoozeResponse(
            success=False,
            error=(
                f"Minimum snooze is {MIN_SNOOZE_SECONDS}s — waking replays this "
                "whole conversation, so anything shorter costs more than it "
                "saves. Do the work now instead."
            ),
        )

    now = datetime.now(timezone.utc)
    seconds = min(request.seconds, MAX_SNOOZE_SECONDS)
    wake_at = now + timedelta(seconds=seconds)

    timer_id = await schedule_snooze_wake(
        conversation_id=deps.conversation_id,
        user_id=deps.user_id,
        wake_at=wake_at,
    )

    wait = AgentConversationWaitEntity(
        conversation_id=deps.conversation_id,
        agent_run_id=deps.agent_run_id,
        pod_id=deps.pod_id,
        tool_call_id=ctx.tool_call_id,
        wait_type=AgentWaitType.TIME,
        external_ref=str(timer_id),
        scheduled_at=wake_at,
        spec={
            "reason": request.reason,
            "note_to_self": request.note_to_self,
            "requested_seconds": request.seconds,
            "started_at": now.isoformat(),
        },
    )
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await AgentConversationWaitRepository(uow).create(wait)
        await uow.commit()

    logger.debug(
        "agent.snooze.suspended",
        conversation_id=str(deps.conversation_id),
        wait_type=wait.wait_type.value,
    )

    # Ends the run cleanly (conversation -> WAITING). The wake path synthesizes
    # this call's return and starts a fresh run that replays it.
    raise AgentInputRequired(ctx.tool_call_id, SNOOZE_TOOL_NAME)


snooze_toolset = FunctionToolset[BaseAgentContext](tools=[snooze])
