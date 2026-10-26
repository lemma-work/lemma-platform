"""The waiting toolset: one way for an agent to stop and be woken later.

Reuses the pause `ask_user` and `request_approval` already have — the run ends
cleanly, the conversation goes WAITING, and resolving the pending tool call
starts a fresh run that replays the synthesized return from history. The only
new thing is *who* resolves it: a timer, a process exiting, or a child run
finishing, with no person involved.

Three targets, one tool, because the agent's situation is identical in all
three: it has nothing to do until something else happens. Splitting them would
mean three docstrings drifting apart on the one thing that is genuinely
conditional — whether the sandbox is still there on the other side. It is, for
as long as something is running in it (`SandboxSweeper._is_busy` skips a busy
sandbox), and it may not be across a plain timer, when nothing is holding it.

Ending the run is the one step that is not the same everywhere. The in-process
harness suspends from the inside, by catching ``AgentInputRequired``. A remote
harness owns its own session, so nothing raised inside an MCP tool call reaches
it — there, Lemma asks the host to stop the turn instead. Everything before that
point, and everything after it, is identical; see ``services/run_suspension``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkWriter
from app.core.log.log import get_logger
from app.modules.agent.domain.wait import (
    AgentWaitType,
    WaitSpec,
)
from app.modules.agent.infrastructure.agent_host.channels import poke_host
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.run_suspension import suspend_remote_run
from app.modules.agent.services.wait_timers import arm_wait
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import (
    AgentInputRequired,
    ToolGroup,
    ToolPolicy,
    tool,
)
from app.modules.agent.domain.pauses import CALL_NAMES, Pause
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.infrastructure.tools.waiting.models import (
    DEFAULT_TARGET_DEADLINE_SECONDS,
    MAX_WAIT_SECONDS,
    MIN_WAIT_SECONDS,
    WaitForRequest,
    WaitForResponse,
    next_poll_delay,
)

logger = get_logger(__name__)


def _target_of(request: WaitForRequest) -> tuple[AgentWaitType, str | None] | None:
    """The one target this call names, or None when it does not name exactly one."""
    named = [
        (AgentWaitType.TIME, None) if request.seconds is not None else None,
        (AgentWaitType.PROCESS, request.process_id) if request.process_id else None,
        (AgentWaitType.SUBAGENT, request.subagent_run_id)
        if request.subagent_run_id
        else None,
    ]
    chosen = [target for target in named if target is not None]
    return chosen[0] if len(chosen) == 1 else None


async def wait_for(ctx: ToolContext, request: WaitForRequest) -> WaitForResponse:
    """Stop here and pick this turn up when there is a reason to.

    Name exactly one thing to wait for:
    - `seconds` — a plain gap, when there is nothing to watch.
    - `process_id` — a command from `exec_command` that is still running. You
      wake when it exits, with its exit code.
    - `subagent_run_id` — a child from `spawn_subagent`. You wake with its result.

    You come back in the same conversation with the same history. Three things
    that bite:
    - **Waking proves nothing succeeded.** A process that ended may have ended
      badly, and a timer elapsing says nothing at all. Check.
    - **Every wake replays the whole conversation**, so one real wait beats a
      loop of short ones. Never call this repeatedly to poll something.
    - **Across a plain `seconds` wait the sandbox may be reclaimed** —
      your files, background processes and your shell cwd can be gone. Waiting
      on a `process_id` holds it, because the process is what holds it. Write
      anything you need to keep to the pod first.

    Don't use it to wait for a person: `ask_user` pauses for the one you are
    talking to, and `channel_post` ends your turn and gets you a new one when
    the answers arrive.
    """
    deps = ctx

    if deps.agent_run_id is None:
        return WaitForResponse(
            success=False, error="wait_for requires an active agent run."
        )
    if deps.pod_id is None:
        return WaitForResponse(
            success=False, error="wait_for is only available inside a pod."
        )
    if not ctx.tool_call_id:
        return WaitForResponse(
            success=False, error="wait_for requires a durable tool call id."
        )

    target = _target_of(request)
    if target is None:
        return WaitForResponse(
            success=False,
            error=(
                "Name exactly one of `seconds`, `process_id` or "
                "`subagent_run_id` — waiting on several things at once is not "
                "supported, so wait for the one that gates your next step."
            ),
        )
    wait_type, external_ref = target

    if wait_type is AgentWaitType.TIME and request.seconds < MIN_WAIT_SECONDS:
        # Rejected rather than clamped: asking for a few seconds means the model
        # has mistaken this for a sleep() rather than a durable suspend, and a
        # silent clamp would teach it nothing.
        return WaitForResponse(
            success=False,
            error=(
                f"Minimum wait is {MIN_WAIT_SECONDS}s — waking replays this "
                "whole conversation, so anything shorter costs more than it "
                "saves. Do the work now instead."
            ),
        )

    if request.max_seconds is not None and request.max_seconds < MIN_WAIT_SECONDS:
        # `max_seconds=0` used to fall through to the default and silently mean
        # the whole default ceiling -- the opposite of what was asked.
        return WaitForResponse(
            success=False,
            error=(
                f"`max_seconds` must be at least {MIN_WAIT_SECONDS}s. Leave it out "
                f"for the default of {DEFAULT_TARGET_DEADLINE_SECONDS // 60} "
                "minutes, or set it to how long you are prepared to wait."
            ),
        )

    now = datetime.now(timezone.utc)
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        existing = await Pauses(uow).pending(
            run_id=deps.agent_run_id, kinds=(PauseKind.WAIT,)
        )
    if existing:
        # One active wait per conversation is a database constraint, so without
        # this a second call inside one turn raises an IntegrityError from
        # inside a tool body rather than telling the model what it did.
        return WaitForResponse(
            success=False,
            error=(
                "This run is already waiting on something. Wait for one thing "
                "at a time, and let this wait resolve before starting another."
            ),
        )

    if wait_type is AgentWaitType.TIME:
        seconds = min(request.seconds, MAX_WAIT_SECONDS)
        scheduled_at = now + timedelta(seconds=seconds)
        deadline_at = scheduled_at
    else:
        # Checked before suspending: a target that has already finished should
        # answer now rather than cost a suspend, a wake and a full history
        # replay to learn something that was true before the call was made.
        settled = await _already_settled(deps, wait_type, external_ref)
        if settled is not None:
            return settled
        ceiling = min(
            request.max_seconds or DEFAULT_TARGET_DEADLINE_SECONDS,
            MAX_WAIT_SECONDS,
        )
        deadline_at = now + timedelta(seconds=ceiling)
        # A pushed wait sleeps until its ceiling and is woken early by the
        # event; a polled one wakes itself to look, and re-arms until it knows.
        scheduled_at = (
            min(deadline_at, now + timedelta(seconds=next_poll_delay(0)))
            if wait_type is AgentWaitType.PROCESS
            else deadline_at
        )

    # The per-wait token the resolution is matched back to. Minted here rather
    # than defaulted on the row because it has to exist before the row is
    # written, and it is what keeps two sequential waits in one conversation
    # from resolving each other.
    wait_ref = uuid4()

    wait = Pause(
        conversation_id=deps.conversation_id,
        run_id=deps.agent_run_id,
        pod_id=deps.pod_id,
        tool_call_id=ctx.tool_call_id,
        tool_name=CALL_NAMES[PauseKind.WAIT],
        kind=PauseKind.WAIT,
        wait_type=wait_type,
        external_ref=str(wait_ref),
        wake_at=scheduled_at,
        wait_spec=WaitSpec(
            reason=request.reason,
            note_to_self=request.note_to_self,
            started_at=now,
            deadline_at=deadline_at,
            target_ref=external_ref,
            user_id=deps.user_id,
            requested_seconds=request.seconds,
            requested_max_seconds=request.max_seconds,
        ),
    )
    suspends_itself = bool(getattr(deps, "supports_pause_signal", False))
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        # Written by the tool rather than with its call, like no other pause:
        # a wait exists only once its timer is armed beside it.
        await Pauses(uow).open(wait)
        # The wait's timer is a work item committed with the row: there is no
        # window in which a wait exists that nothing will fire.
        await arm_wait(WorkWriter.of(uow), wait, scheduled_at)
        # One transaction, because a run asked to stop with no wait row to wake
        # it is an agent that waits forever.
        host_to_poke = (
            None
            if suspends_itself
            else await suspend_remote_run(uow, agent_run_id=deps.agent_run_id)
        )
        await uow.commit()
    if host_to_poke is not None:
        await poke_host(host_to_poke)

    logger.debug(
        "agent.wait.suspended",
        conversation_id=str(deps.conversation_id),
        wait_type=wait_type.value,
    )

    if suspends_itself:
        # Ends the run cleanly (conversation -> WAITING). The resolution path
        # synthesizes this call's return and starts a fresh run that replays it.
        raise AgentInputRequired(ctx.tool_call_id, PauseKind.WAIT)

    # A remote harness reads this and then stops, mid-sentence if that is where
    # the cancel catches it. Written for a model that may get no further turn to
    # act on it: it says the wait is already arranged, so there is nothing left
    # to do and nothing to confirm.
    return WaitForResponse(
        success=True,
        note_to_self=request.note_to_self,
        message=(
            "Waiting. Your turn ends here — stop now, and do not call another "
            "tool. You wake in this same conversation with a fresh prompt "
            "saying why."
        ),
    )


async def _already_settled(
    deps: ToolContext, wait_type: AgentWaitType, external_ref: str | None
) -> WaitForResponse | None:
    """An immediate answer when the target has already reached a terminal state."""
    from app.modules.agent.services.wait_targets import read_target

    outcome = await read_target(
        wait_type=wait_type,
        target_ref=external_ref,
        user_id=deps.user_id,
        pod_id=deps.pod_id,
        conversation_id=deps.conversation_id,
    )
    if outcome.unreadable:
        # The wait still goes ahead -- the resolver gives an unreadable target a
        # grace period -- but it starts blind, and the probe failing is the only
        # trace of why a wait that should have answered at once did not.
        logger.warning(
            "agent.wait.target_unreadable_at_suspend.degraded",
            conversation_id=str(deps.conversation_id),
            wait_type=wait_type.value,
        )
    if outcome.reason is None:
        return None
    return WaitForResponse(
        success=True,
        woke_because=outcome.reason.value,
        waited_seconds=0,
        exit_code=outcome.exit_code,
        message=outcome.message,
    )


SPECS = (
    tool(
        wait_for,
        ToolGroup.WAIT,
        ToolPolicy(pauses=PauseKind.WAIT, deferrable=True),
    ),
)
