"""Writing a run's terminal state, come what may.

Every path out of a run ends here: the harness reported a terminal event, or it
raised, or the worker was cancelled mid-answer. All three have to leave the same
durable record, because a run whose row still says RUNNING is a run nobody can
retry and nobody can bill.

Separated from the runner because "decide what happened" and "record what
happened" fail differently. The recording half is the one that must survive
cancellation, and the shielding that lets it do so — see `finalize_safely` and
its caller — is subtle enough to deserve its own file rather than being the tail
of a 350-line method.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable
from uuid import UUID


from app.core.authorization.delegation import is_pod_default_agent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.infrastructure.harnesses.run_failure_classification import (
    classify_run_failure,
    is_usage_limit_error as is_usage_limit_error,
    run_failure_reason as run_failure_reason,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import (
    AgentRunUsage,
    AgentRunStatus,
    ConversationStatus,
    JsonObject,
    JsonValue,
)
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.infrastructure.repositories import (
    ConversationRepository,
)
from app.modules.agent.infrastructure.repositories.run_last_words import (
    mark_final_answer,
)
from app.modules.agent.services.realtime import (
    completed_payload,
    error_payload,
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.run_followups import stage_run_followups
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.serialization import message_to_payload
from app.modules.usage.contracts.execution import usage_context_from_agent_context
from app.modules.agent.tools.context import ConversationContext

logger = get_logger(__name__)


def run_failure_message(exc: BaseException) -> str:
    """What the user reads when a run dies outside the harness's own handling.

    The same sentence the harness writes for the same exception: both read
    `classify_run_failure`, so a failure does not change its explanation
    depending on which layer happened to catch it.
    """
    return classify_run_failure(exc).message


def run_failure_code(exc: BaseException) -> str | None:
    return classify_run_failure(exc).code


async def finalize_safely(coro: Awaitable[None], *, agent_run_id: UUID) -> None:
    """Await a finalization coroutine, swallowing all errors.

    Used inside ``asyncio.shield()`` so the coroutine completes even when the
    caller is cancelled. Any exception (DB error, cancellation) is logged and
    suppressed — finalization must never crash the worker.
    """
    try:
        await coro
    except asyncio.CancelledError:
        logger.debug(
            "agent.agent_runner_service.agent_run_finalization_cancelled_run.diagnostic",
            agent_run_id=agent_run_id,
        )
    except Exception:
        logger.error(
            "agent.agent_runner_service.agent_run_finalization_run_s.failed",
            agent_run_id=agent_run_id,
            exc_info=True,
        )


def rejected_run_error_message(data: object) -> str:
    """Build a user-facing message for a pre-dispatch harness rejection."""
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return "The Agent Host rejected this run before dispatch. Try again."


async def waiting_on_work(uow: Any, conversation_id: UUID) -> bool:
    """Whether a WAITING conversation waits on the agent's own work -- a
    sub-agent, a process, a timer -- rather than on a person.

    Said on the ``completed`` frame so a client can tell the two apart without
    reading back: only this one has a run coming that it should follow.
    """
    return await AgentConversationWaitRepository(uow).has_active(conversation_id)


async def _marked_as_the_answer(
    uow: SqlAlchemyUnitOfWork, answer: Message | None
) -> Message | None:
    if answer is None:
        return None
    return await mark_final_answer(uow.session, message_id=answer.id)


async def _redraw(run: RunIdentity, message: Message | None) -> None:
    if message is not None:
        await publish_conversation_event(
            run.conversation_id,
            message_payload(run.agent_run_id, message_to_payload(message)),
        )


class RunFinalizer:
    """Terminal writes for one runner's runs."""

    def __init__(
        self,
        uow_factory: Any,
        usage_recorder: Any,
        *,
        terminal_units: UnitOfWorkFactory | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.usage_recorder = usage_recorder
        # The transaction the terminal write commits in. A turn on the work
        # runtime passes its fenced completion here (`events/turn_work.py`), so
        # the run's status, its follow-ups and the item's deletion commit
        # together -- and a worker whose lease was taken over rolls its
        # finalize back instead of finishing the run a second time.
        self.terminal_units = terminal_units or uow_factory
        #: Writes the follow-ups of a run this finalize ended, in its transaction.
        self.stage_followups = stage_run_followups

    async def finish(
        self,
        *,
        run: RunIdentity,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        error_code: str | None = None,
        error_reason: str | None = None,
        output_data: JsonValue | None = None,
        usage_data: AgentRunUsage | None = None,
        answer: Message | None = None,
    ) -> None:
        """Record the run's end, once.

        ``answer`` is a message already on the record that ``output_data`` was
        read from but that was not written as the answer -- a run's last words
        (``RunEventPump._answer_from_last_words``). It is marked as the answer
        in this transaction, so no reader sees the run's status, its output
        and its transcript disagree.
        """
        try:
            event: AgentRunCompletedEvent | None = None
            event_data: JsonObject = {}
            answered: Message | None = None
            async with self.terminal_units() as uow:
                finish_result = await ConversationRepository(uow).finish_agent_run(
                    agent_run_id=run.agent_run_id,
                    status=status,
                    conversation_status=conversation_status,
                    error=error,
                    error_code=error_code,
                    error_reason=error_reason,
                    output_data=output_data,
                )
                if finish_result is not None and finish_result.updated:
                    answered = await _marked_as_the_answer(uow, answer)
                    status = finish_result.status
                    conversation_status = finish_result.conversation_status
                    if status == AgentRunStatus.STOPPED:
                        error = None
                    if error:
                        event_data["error"] = error
                    if output_data is not None:
                        event_data["output_data"] = output_data
                    event_data["conversation_status"] = conversation_status.value
                    if conversation_status is ConversationStatus.WAITING:
                        event_data["waiting_on_work"] = await waiting_on_work(
                            uow, run.conversation_id
                        )
                    event = AgentRunCompletedEvent(
                        conversation_id=run.conversation_id,
                        agent_run_id=run.agent_run_id,
                        status=status,
                        data=event_data or None,
                        # Copied off the identity this finalizer already holds.
                        # A consumer that wants to say which pod a run belonged
                        # to would otherwise have to load the conversation back.
                        pod_id=run.pod_id,
                        organization_id=run.organization_id,
                        agent_id=run.agent_id,
                        user_id=run.user_id,
                        started_at=run.started_at,
                    )
                    # Analytics only now; every follow-up is staged below.
                    uow.collect_events([event])
                if finish_result is not None:
                    await self.stage_followups(
                        uow,
                        run_id=run.agent_run_id,
                        conversation_id=run.conversation_id,
                        finish=finish_result,
                        started_at=run.started_at,
                        keep_memory_for=(
                            run.user_id if run.memory_after_turn else None
                        ),
                    )
            if finish_result is not None:
                # A retry must repair receipts even when the run is terminal.
                # The stored outcome wins over a retry's fallback failure status.
                await self.usage_recorder.finalize_metered(
                    agent_run_id=run.agent_run_id,
                    runtime_profile=run.runtime_profile,
                    status=finish_result.status.value,
                )
            if finish_result is None or not finish_result.updated:
                await self.usage_recorder.release(run.usage_reservation)
                return
            assert event is not None
            # Before ``completed``: a client that drew the words as a step
            # redraws them as the answer before it hears the run is over.
            await _redraw(run, answered)
            if status == AgentRunStatus.FAILED:
                await publish_conversation_event(
                    run.conversation_id,
                    error_payload(
                        run.agent_run_id,
                        error or "Agent run failed",
                        code=error_code,
                        reason=error_reason,
                    ),
                )
            await publish_conversation_event(
                run.conversation_id,
                completed_payload(
                    conversation_id=run.conversation_id,
                    agent_run_id=run.agent_run_id,
                    status=status.value,
                    data=event_data or None,
                ),
            )
            await self.publish_usage(
                status=status,
                usage_data=usage_data,
                run=run,
            )
        except Exception:
            # Warning, not debug: a finalize that did not commit leaves the run
            # RUNNING for its item to retry, and at debug that was invisible in
            # every deployed environment.
            logger.warning(
                "agent.run_finalizer.finalize_failed.degraded",
                agent_run_id=run.agent_run_id,
                exc_info=True,
            )
            try:
                await self.usage_recorder.release(run.usage_reservation)
            except Exception:
                # A reservation that is never released permanently overstates
                # usage against the customer's quota, and nothing self-heals it.
                # The finalization error is re-raised below regardless; this is
                # its own failure and needs its own record.
                logger.error(
                    "agent.run_finalizer.usage_reservation_release.failed",
                    agent_run_id=run.agent_run_id,
                    exc_info=True,
                )
            raise

    async def publish_usage(
        self,
        *,
        run: RunIdentity,
        status: AgentRunStatus,
        usage_data: AgentRunUsage | None,
    ) -> None:
        if usage_data is None or run.pod_id is None or run.user_id is None:
            await self.usage_recorder.release(run.usage_reservation)
            return
        if (
            usage_data.input_tokens <= 0
            and usage_data.output_tokens <= 0
            and usage_data.units <= 0
        ):
            await self.usage_recorder.release(run.usage_reservation)
            return
        context = usage_context_from_agent_context(
            ConversationContext(
                user_id=run.user_id,
                org_id=run.organization_id,
                pod_id=run.pod_id,
                conversation_id=run.conversation_id,
                agent_run_id=run.agent_run_id,
                workload_type="agent",
                workload_id=run.agent_id,
                is_pod_default_agent=is_pod_default_agent(
                    run.agent_id, pod_id=run.pod_id
                ),
            ),
            source_type=run.usage_source,
            source_id=str(run.agent_run_id),
        )
        await self.usage_recorder.record(
            ctx=context,
            runtime_profile=run.runtime_profile,
            usage_data=usage_data,
            status=status.value,
            reservation=run.usage_reservation,
        )
