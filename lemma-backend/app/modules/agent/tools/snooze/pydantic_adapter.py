"""Snooze toolset: let an agent suspend its own turn and wake later.

Reuses the pause `ask_user` and `request_approval` already have — the run ends
cleanly, the conversation goes WAITING, and resolving the pending tool call
starts a fresh run that replays the synthesized return from history. The only
new thing here is *who resolves it*: a scheduler timer rather than a person.

Ending the run is the one step that is not the same everywhere. The in-process
harness suspends from the inside, by catching ``AgentInputRequired``. A remote
harness owns its own session, so nothing raised inside an MCP tool call reaches
it — there, Lemma asks the host to stop the turn instead. Everything before that
point, and everything after it, is identical; see ``services/run_suspension``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.composition.agent_snooze_scheduler import schedule_snooze_wake
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.pausing_tools import SNOOZE_TOOL_NAME
from app.modules.agent.domain.wait import AgentConversationWaitEntity, AgentWaitType
from app.modules.agent.infrastructure.agent_host_channels import poke_host
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.run_suspension import suspend_remote_run
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.snooze.models import (
    MAX_SNOOZE_SECONDS,
    MIN_SNOOZE_SECONDS,
    SnoozeRequest,
    SnoozeResponse,
)
from app.modules.agent.tools.tool_errors import AgentInputRequired

logger = get_logger(__name__)


async def snooze(
    ctx: RunContext[BaseAgentContext], request: SnoozeRequest
) -> SnoozeResponse:
    """Suspend this turn and pick it up later, where you left off.

    Use it when the work has a real gap — a build to check back on, something
    that needs time to settle. You wake in the same conversation with the same
    history.

    Three things that bite:
    - **Your sandbox does not survive.** `/workspace`, background processes, and
      your shell cwd are gone on wake. Write anything you need to the pod first.
    - **Waking proves nothing happened.** `TIMER` means your time elapsed and
      nothing more — check the thing you were waiting for.
    - **Every wake replays the whole conversation**, so one longer sleep beats a
      poll loop.

    Don't use it to wait on the person you are *talking to* — ask with
    `ask_user`, or end your turn; their reply starts a fresh run either way.
    Waiting on somebody you reached with `message_user` is the exception, and
    the one case this is for: nothing resumes you there, so a single sleep sized
    to how long a person actually takes is the only way to be around when they
    answer.
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
    suspends_itself = bool(getattr(deps, "supports_pause_signal", False))
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await AgentConversationWaitRepository(uow).create(wait)
        # One transaction, because a run asked to stop with no wait row to wake
        # it is an agent that goes to sleep forever.
        host_to_poke = (
            None
            if suspends_itself
            else await suspend_remote_run(uow, agent_run_id=deps.agent_run_id)
        )
        await uow.commit()
    if host_to_poke is not None:
        await poke_host(host_to_poke)

    logger.debug(
        "agent.snooze.suspended",
        conversation_id=str(deps.conversation_id),
        wait_type=wait.wait_type.value,
    )

    if suspends_itself:
        # Ends the run cleanly (conversation -> WAITING). The wake path
        # synthesizes this call's return and starts a fresh run that replays it.
        raise AgentInputRequired(ctx.tool_call_id, SNOOZE_TOOL_NAME)

    # A remote harness reads this and then stops, mid-sentence if that is where
    # the cancel catches it. Written for a model that may get no further turn to
    # act on it: it says the sleep is already arranged, so there is nothing left
    # to do and nothing to confirm.
    return SnoozeResponse(
        success=True,
        note_to_self=request.note_to_self,
        message=(
            f"Asleep for {seconds}s. Your turn ends here — stop now, and do not "
            "call another tool. You wake in this same conversation with a fresh "
            "prompt telling you the time elapsed."
        ),
    )


snooze_toolset = FunctionToolset[BaseAgentContext](tools=[snooze])
