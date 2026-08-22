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


from app.core.log.log import get_logger
from app.modules.agent.infrastructure.transport_errors import (
    is_retryable_stream_error,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import (
    AgentRunUsage,
    AgentRunStatus,
    ConversationStatus,
    JsonObject,
    JsonValue,
)
from app.modules.agent.infrastructure.repositories import (
    ConversationRepository,
)
from app.modules.agent.services.realtime import (
    completed_payload,
    error_payload,
    publish_conversation_event,
)
from app.modules.agent.services.run_identity import RunIdentity
from app.composition.agent_usage import (
    usage_context_from_agent_context,
)
from app.modules.agent.tools.context import ConversationContext

logger = get_logger(__name__)


def is_usage_limit_error(exc: BaseException) -> bool:
    """Whether this run died because the workspace is out of allowance.

    A predicate rather than an exported class because the architecture gate
    allows the `agent` module exactly one import of `usage.domain.errors`, and
    the module that already classifies run failures is the one that should have
    it. Callers ask the question; they do not need the type.
    """
    return isinstance(exc, UsageLimitExceededError)


def run_failure_message(exc: BaseException) -> str:
    """What the user reads when a run dies outside the harness's own handling.

    Distinguishing these matters: a quota exhaustion and a dropped connection
    are both "the run failed", but only one of them is worth investigating, and
    neither is fixed by checking the runtime configuration.
    """
    if isinstance(exc, UsageLimitExceededError):
        return (
            "This run was not started because the workspace has used its "
            "available usage allowance. Usage resets with the billing period, "
            "or the plan limit can be raised."
        )
    if is_retryable_stream_error(exc):
        return (
            "The connection to the model provider kept dropping. Nothing you "
            "sent was lost — send another message, or press Retry to pick up "
            "where it stopped."
        )
    if not isinstance(exc, Exception):
        return "Agent run was interrupted (timeout or shutdown)"
    return "Agent run failed. Please check the agent runtime configuration."


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


class RunFinalizer:
    """Terminal writes for one runner's runs."""

    def __init__(self, uow_factory: Any, usage_recorder: Any) -> None:
        self.uow_factory = uow_factory
        self.usage_recorder = usage_recorder

    async def finish(
        self,
        *,
        run: RunIdentity,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        output_data: JsonValue | None = None,
        usage_data: AgentRunUsage | None = None,
    ) -> None:
        try:
            event: AgentRunCompletedEvent | None = None
            event_data: JsonObject = {}
            async with self.uow_factory() as uow:
                finish_result = await ConversationRepository(uow).finish_agent_run(
                    agent_run_id=run.agent_run_id,
                    status=status,
                    conversation_status=conversation_status,
                    error=error,
                    output_data=output_data,
                )
                if finish_result is not None and finish_result.updated:
                    status = finish_result.status
                    conversation_status = finish_result.conversation_status
                    if status == AgentRunStatus.STOPPED:
                        error = None
                    if error:
                        event_data["error"] = error
                    if output_data is not None:
                        event_data["output_data"] = output_data
                    event_data["conversation_status"] = conversation_status.value
                    event = AgentRunCompletedEvent(
                        conversation_id=run.conversation_id,
                        agent_run_id=run.agent_run_id,
                        status=status,
                        data=event_data or None,
                    )
                    uow.collect_events([event])
            if finish_result is None or not finish_result.updated:
                await self.usage_recorder.release(run.usage_reservation)
                return
            assert event is not None
            if status == AgentRunStatus.FAILED:
                await publish_conversation_event(
                    run.conversation_id,
                    error_payload(run.agent_run_id, error or "Agent run failed"),
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
            logger.debug(
                "agent.agent_runner_service.finalize_agent_run_run_s.propagated",
                agent_run_id=run.agent_run_id,
                exc_info=True,
            )
            try:
                await self.usage_recorder.release(run.usage_reservation)
            except Exception:
                logger.debug(
                    "agent.agent_runner_service.release_usage_reservation_run_s.diagnostic",
                    agent_run_id=run.agent_run_id,
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
            ),
            source_type="agent_run",
            source_id=str(run.agent_run_id),
        )
        await self.usage_recorder.record(
            ctx=context,
            runtime_profile=run.runtime_profile,
            usage_data=usage_data,
            status=status.value,
            reservation=run.usage_reservation,
        )
