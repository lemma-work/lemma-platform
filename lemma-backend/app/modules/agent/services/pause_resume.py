"""The primitives every pause shares: find its call, write its return, start its
resume run.

Any tool that raises ``AgentInputRequired`` -- ``ask_user``,
``request_approval``, ``browser_sign_in``, ``wait_for`` -- ends its run and
leaves a dangling call. It resumes by having that call's return written and
replayed by a fresh run. The use cases that do so (``application/pauses``,
``application/turns/stop``, ``application/turns/follow_up``) differ in *when*;
these are the steps they share, and each takes the repositories it reads rather
than building its own, so a use case's fakes reach them.

Transactions are exactly the use cases' that call them: ``append_pause_tool_return``
and ``start_resume_run`` commit, as the steps they were carved from always did.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.pausing_tools import (
    PAUSING_TOOL_NAMES,
    PausedCall,
    PausingKind,
)
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.run_state import RunPrivacy, RunState
from app.modules.agent.domain.value_objects import MessageDraft, RunTrigger
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.conversation_access import resolve_agent
from app.modules.agent.services.realtime import (
    error_payload,
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.runtime_availability import turn_runtime
from app.modules.agent.services.runtime_owned_uow import RuntimeOwnedUnitOfWork
from app.modules.agent.services.serialization import message_to_payload
from app.modules.agent.services.usage_preflight import assert_usage_preflight_allowed
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.execution import UsageService

logger = get_logger(__name__)

#: What a run that resumes a pause hands back to whoever publishes for it: the
#: run's id, and the queued messages it claimed, whose frames are owed.
ResumeStarted = tuple[UUID, list[Message]]


async def paused_call(
    pausing_calls: Pauses, *, conversation_id: UUID, approval_id: str
) -> PausedCall | None:
    """The pausing tool call for an approval, regardless of decision state.

    Addressed directly by ``tool_call_id`` (not a message-window scan) so a
    long conversation can't hide the original call during resume
    reconciliation.
    """
    message = await pausing_calls.get_tool_call(
        conversation_id=conversation_id,
        tool_call_id=approval_id,
    )
    if (
        message is None
        or message.tool_name is None
        or message.tool_name not in PAUSING_TOOL_NAMES
        or message.agent_run_id is None
    ):
        return None
    tool_args = message.tool_args if isinstance(message.tool_args, dict) else {}
    return PausedCall(
        agent_run_id=message.agent_run_id,
        kind=PausingKind(message.tool_name),
        tool_args=tool_args,
    )


def publish_after_commit(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, frames: list[dict[str, object]]
) -> None:
    """Send live UI frames once this transaction commits, never inside it.

    Registered before the commit that carries them: inside a work item the
    commit is the runtime's (``RuntimeOwnedUnitOfWork``), and a Redis round
    trip inside a FAST handler's transaction is the one thing it may not do.
    """
    if not frames:
        return

    async def _publish() -> None:
        for frame in frames:
            await publish_conversation_event(conversation_id, frame)

    uow.after_commit(_publish)


async def append_pause_tool_return(
    uow: SqlAlchemyUnitOfWork,
    *,
    pausing_calls: Pauses,
    messages: Messages,
    conversation: Conversation,
    paused_run_id: UUID,
    tool_call_id: str,
    tool_name: str,
    tool_result: object,
) -> bool:
    """Persist the return the resumed run will replay. Idempotent; commits.

    Returns True if it wrote one, False if a return already existed — the
    caller uses that to avoid re-running side effects (an approved tool must
    execute at most once). Persisted under the *paused* run, since history is
    reconstructed per conversation and ``_build_tool_batch`` pairs a return
    with its call regardless of which run each lives in.
    """
    existing = await pausing_calls.get_tool_return(
        conversation_id=conversation.id,
        tool_call_id=tool_call_id,
    )
    if existing is not None:
        return False
    saved_return = await messages.append_message(
        conversation_id=conversation.id,
        agent_run_id=paused_run_id,
        draft=MessageDraft.of_tool_return(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_result=tool_result,
        ),
    )
    # A live UI frame, sent once the return has committed: after the
    # request's commit, or -- inside a work item -- after the runtime's.
    publish_after_commit(
        uow,
        conversation.id,
        [message_payload(paused_run_id, message_to_payload(saved_return))],
    )
    await uow.commit()
    return True


async def start_resume_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    runs: Runs,
    queued_messages: QueuedMessages,
    agent_repository: AgentRepository,
    usage_service: UsageService | None,
    conversation: Conversation,
    paused_run_id: UUID,
    resumed_tool_call_id: str,
    user_id: UUID,
    agent_name: str | None,
    trigger: RunTrigger,
    publish_claimed: bool = False,
) -> ResumeStarted | None:
    """Create the resume run and claim what was said while it was paused.

    The caller holds the conversation lock and has established that nothing
    is running. Commits. None when the account is out of allowance: the
    answer stays recorded and the conversation stays WAITING, so the next
    message picks it up once there is headroom, and the person is told now.
    """
    agent = await resolve_agent(
        conversation,
        user_id=user_id,
        agent_repository=agent_repository,
    )
    selected_agent_runtime = await turn_runtime(uow, conversation, agent)
    try:
        # Resuming is a run like any other: skipping this made answering a
        # question a way past an exhausted limit. Inside a work item the
        # check is the turn's own reservation instead: a usage lookup has
        # no place in a FAST handler's transaction, and a turn refused
        # there fails with the same limit message.
        if not isinstance(uow, RuntimeOwnedUnitOfWork):
            await assert_usage_preflight_allowed(
                usage_service,
                organization_id=conversation.organization_id,
                user_id=user_id,
                agent_runtime=selected_agent_runtime,
                outside_audience=Audience.of(conversation).answers_outsiders,
            )
    except UsageLimitExceededError as refused:
        _report_refused_resume(
            uow, conversation, paused_run_id=paused_run_id, refused=refused
        )
        await uow.commit()
        return None
    paused_run = await runs.get_agent_run(paused_run_id)
    privacy = paused_run.state.privacy if paused_run else RunPrivacy()
    notes = privacy.private_note
    # A resume answers one kind, like a follow-up turn: the other kind
    # waits for the turn after it, which has to know where to look.
    leftovers = await queued_messages.count_queued_user_messages(
        paused_run_id, notes=not notes
    )
    resume_run = await runs.create_agent_run(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        agent_runtime=selected_agent_runtime,
        state=RunState(
            trigger=trigger,
            resumed_tool_call_id=resumed_tool_call_id,
            # A note that paused for an approval finishes where it began.
            **privacy.model_dump(),
            queued_leftovers_behind=[paused_run_id] if leftovers else [],
        ),
    )
    # What the person said while the pause was open is this run's to
    # answer: claimed into it, so a host carries it in the prompt and the
    # follow-up after this run does not answer it a second time.
    claimed = await queued_messages.claim_queued_user_messages(
        paused_run_id, into_run_id=resume_run.id, notes=notes
    )
    await stage_agent_turn(
        uow,
        turn_for(
            conversation,
            run_id=resume_run.id,
            user_id=user_id,
            agent_name=agent_name,
        ),
    )
    if publish_claimed:
        publish_after_commit(
            uow,
            conversation.id,
            [
                message_payload(message.agent_run_id, message_to_payload(message))
                for message in claimed
            ],
        )
    await uow.commit()
    return resume_run.id, claimed


def _report_refused_resume(
    uow: SqlAlchemyUnitOfWork,
    conversation: Conversation,
    *,
    paused_run_id: UUID,
    refused: UsageLimitExceededError,
) -> None:
    logger.warning(
        "agent.pause_resume.resume_refused_by_usage_limit.degraded",
        conversation_id=str(conversation.id),
        agent_run_id=str(paused_run_id),
    )
    publish_after_commit(
        uow,
        conversation.id,
        [
            error_payload(
                paused_run_id,
                refused.message,
                code=refused.code,
                reason=refused.reason,
            )
        ],
    )
