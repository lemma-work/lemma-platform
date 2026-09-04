"""Snooze toolset: let an agent suspend its own turn and wake later.

Reuses the pause `ask_user` and `request_approval` already have — the run ends
cleanly, the conversation goes WAITING, and resolving the pending tool call
starts a fresh run that replays the synthesized return from history. The only
new thing here is *who resolves it*: a scheduler timer rather than a person.

That timer is the whole primitive. This tool is a durable sleep, and nothing
here knows or cares what the agent is sleeping on. In particular it is not how
an agent waits for a person: ``message_user`` ends its turn and is given a new
one when the answers arrive, so nothing has to stay asleep for a human.

One thing can still end a sleep early. If a conversation happens to be asleep
when the last of its ``message_user`` asks is answered,
``services/message_reply_service`` resolves that pause rather than posting a
message past it — a message would supersede the pause while leaving its wait row
armed to fire a second time later.

Ending the run is the one step that is not the same everywhere. The in-process
harness suspends from the inside, by catching ``AgentInputRequired``. A remote
harness owns its own session, so nothing raised inside an MCP tool call reaches
it — there, Lemma asks the host to stop the turn instead. Everything before that
point, and everything after it, is identical; see ``services/run_suspension``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.pausing_tools import SNOOZE_TOOL_NAME
from app.modules.agent.domain.wait import AgentConversationWaitEntity, AgentWaitType
from app.modules.agent.infrastructure.agent_host.channels import poke_host
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

    Size the sleep from whatever you are actually waiting on. Don't use it to
    wait for a person, including one you reached with `message_user` — end your
    turn instead, and you get a fresh one when they answer.
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

    # The per-wait token the fired timer is resolved back to. Minted here
    # rather than defaulted on the row because it has to exist before the row
    # is written: `external_ref` is what `find_active_by_external_ref` joins on,
    # and it is what keeps two sequential snoozes in one conversation from
    # resuming each other.
    #
    # There is nothing to arm. The wait row below carries `scheduled_at` and
    # `external_ref`, and the schedule poller claims from those columns -- the
    # row *is* the timer. This used to call a one-shot scheduler through
    # `app/composition/agent_snooze_scheduler.py`, which by the end minted a
    # uuid and did nothing else, and existed only so that `agent` could reach
    # `schedule` without importing it.
    wait_ref = uuid4()

    wait = AgentConversationWaitEntity(
        conversation_id=deps.conversation_id,
        agent_run_id=deps.agent_run_id,
        pod_id=deps.pod_id,
        tool_call_id=ctx.tool_call_id,
        wait_type=AgentWaitType.TIME,
        external_ref=str(wait_ref),
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
            f"Asleep for up to {seconds}s. Your turn ends here — stop now, and "
            "do not call another tool. You wake in this same conversation with a "
            "fresh prompt saying why — the time elapsing, or everyone you "
            "messaged having answered, whichever comes first."
        ),
    )


snooze_toolset = FunctionToolset[BaseAgentContext](tools=[snooze])
