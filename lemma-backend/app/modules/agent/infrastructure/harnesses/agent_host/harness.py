"""Remote harness adapter for the Agent Host control plane.

The run worker consumes one ordered per-run Redis Stream with a blocking read.
The previous design interleaved two sources: a one-second PostgreSQL poll for
durable events and an eagerly drained queue fed by a pub/sub forwarder task for
cosmetic chunks. A single blocking read replaces the poll, the queue, the
forwarder task, and the interleaving, and delivers lower latency than the poll
it replaces.

PostgreSQL is still consulted, but only for lease state - acceptance, expiry,
and recovery - on a slow cadence, because none of that arrives as an event.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_host import AgentHostRunState
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    HarnessKind,
)
from app.modules.agent.infrastructure.agent_host.channels import poke_host
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host.event_stream import (
    AgentHostEventStream,
    StreamBatch,
    agent_host_event_stream,
)
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.harnesses.agent_host.artifacts import (
    AgentHostArtifactWriter,
)
from app.modules.agent.infrastructure.harnesses.agent_host.events import (
    AgentHostEventNormalizer,
    error_event,
    is_terminal_event,
)
from app.modules.agent.infrastructure.harnesses.agent_host.harness_events import (
    RunEventApplier,
    adopt_recorded_final_answer,
)
from app.modules.agent.infrastructure.harnesses.agent_host.dispatch import (
    enqueue_run,
    refresh_credential,
)
from app.modules.agent.infrastructure.harnesses.agent_host.parked_window import (
    DEFAULT_PARKED_CHECK_SECONDS,
    ParkedWindow,
)
from app.modules.agent.infrastructure.harnesses.agent_host.slot_wait import SlotWait
from app.modules.agent.infrastructure.harnesses.agent_host.run_config import (
    AgentHostRunConfig,
    agent_host_run_config,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_window import (
    CREDENTIAL_DEADLINE_MESSAGE,
    DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
    DispatchedRun,
    LeaseOutcome,
    credential_exhausted,
    credential_refresh_due,
    expiry_message,
    failure_events,
    RunLease,
    lease_terminal_detail,
    terminal_checkpoint_state,
)
from app.modules.agent.infrastructure.harnesses.agent_host.steering import (
    SteerSchedule,
    forward_queued_messages,
)
from app.modules.agent.infrastructure.harnesses.agent_host.stream_reader import (
    StreamReader,
    StreamUnavailable,
)
from app.modules.agent.infrastructure.harnesses.agent_host.stream_resume import (
    ConsumedCursor,
    restore_normalizer,
)
from app.modules.agent.infrastructure.tools.final_answer.tools import (
    agent_output_schema,
    final_answer_expected,
)

logger = get_logger(__name__)

# How long a blocking stream read waits before the loop rechecks its deadlines.
DEFAULT_STREAM_BLOCK_MS = 1_000
# Lease state does not arrive as an event, so it is polled - but slowly, since
# it only matters for acceptance, expiry, and recovery.
DEFAULT_LEASE_CHECK_SECONDS = 5.0
DEFAULT_TERMINAL_EVENT_GRACE_SECONDS = 5.0
# How often a steerable run looks for messages the person sent since. Tighter
# than the lease check because it is what a person waits on, and cheap because
# it is one indexed UPDATE that nearly always matches nothing.
DEFAULT_STEER_CHECK_SECONDS = 1.0


@dataclass(slots=True)
class _ConsumeState:
    """The consume loop's clocks and flags, between one batch and the next."""

    deadline: float
    deadline_message: str
    accept_deadline: float
    slot_wait: SlotWait
    lease_check_due_at: float
    steering: SteerSchedule
    parked: ParkedWindow
    credential_expires_at: datetime | None
    terminal_checkpoint_seen_at: float | None = None
    stop_sent: bool = False
    #: Events a check wants shown without ending the run.
    notices: list[AgentEvent] = field(default_factory=list)


class RemoteHarness:
    """Dispatch one run through the Agent Host protocol."""

    kind = HarnessKind.HARNESS

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        artifact_writer: AgentHostArtifactWriter | None = None,
        event_stream: AgentHostEventStream | None = None,
        event_timeout_seconds: float = DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
        lease_check_seconds: float = DEFAULT_LEASE_CHECK_SECONDS,
        stream_block_ms: int = DEFAULT_STREAM_BLOCK_MS,
        terminal_event_grace_seconds: float = DEFAULT_TERMINAL_EVENT_GRACE_SECONDS,
        steer_check_seconds: float = DEFAULT_STEER_CHECK_SECONDS,
        parked_window_seconds: float | None = None,
        parked_check_seconds: float = DEFAULT_PARKED_CHECK_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.artifact_writer = artifact_writer
        self._applier = RunEventApplier(uow_factory, artifact_writer)
        self.events = event_stream or agent_host_event_stream()
        self.event_timeout_seconds = event_timeout_seconds
        self.lease_check_seconds = lease_check_seconds
        self.stream_block_ms = stream_block_ms
        self.terminal_event_grace_seconds = terminal_event_grace_seconds
        self.steer_check_seconds = steer_check_seconds
        self.parked_window_seconds = (
            agent_settings.agent_host_parked_window_seconds
            if parked_window_seconds is None
            else parked_window_seconds
        )
        self.parked_check_seconds = parked_check_seconds
        # Only the credential window is measured against a wall clock, and it
        # is an hour wide. Injecting it is what lets a test prove a run
        # outliving its original token without waiting out the real hour.
        self._now = clock or (lambda: datetime.now(timezone.utc))

    async def run[DepsT: ToolContext](
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: DepsT,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        try:
            run_config = agent_host_run_config(options)
        except (KeyError, TypeError, ValueError) as exc:
            yield error_event(
                agent_run_id, f"Invalid Agent Host runtime profile: {exc}"
            )
            return

        try:
            dispatch = await enqueue_run(
                uow_factory=self.uow_factory,
                event_timeout_seconds=self.event_timeout_seconds,
                agent=agent,
                conversation=conversation,
                messages=messages,
                ctx=ctx,
                options=options,
                agent_run_id=agent_run_id,
                run_config=run_config,
            )
        except (AgentHostRepositoryError, RuntimeError, ValueError) as exc:
            yield error_event(agent_run_id, str(exc))
            return

        # The stream is dropped only once the run has genuinely finished. This
        # generator is also closed on cancellation and on a consumer that stops
        # early, and deleting there would throw away events the host is still
        # appending - and would do it with an await inside a finally during
        # GeneratorExit, which is not reliably allowed to complete. The stream's
        # own TTL is the backstop for the abandoned case.
        finished = False
        try:
            async for event in self._consume(
                agent_run_id=agent_run_id,
                agent=agent,
                ctx=ctx,
                conversation=conversation,
                options=options,
                run_config=run_config,
                dispatch=dispatch,
            ):
                yield event
            finished = True
        finally:
            if finished:
                await self.events.delete(run_id=agent_run_id)

    async def _consume[DepsT: ToolContext](
        self,
        *,
        agent_run_id: UUID,
        agent: Agent,
        ctx: DepsT,
        conversation: Conversation,
        options: HarnessOptions,
        run_config: AgentHostRunConfig,
        dispatch: DispatchedRun,
    ) -> AsyncIterator[AgentEvent]:
        """Drive one dispatched run to a terminal event.

        Returns only when the run is over, one way or another; every exit
        yields something terminal first. A run another worker dispatched
        (``dispatch.resumed``) is picked up at the cursor that worker recorded,
        with the tool calls it put on the record restored (``stream_resume``).
        """
        normalizer = AgentHostEventNormalizer(
            agent_run_id=agent_run_id,
            model_name=options.model_name,
            harness_key=dispatch.harness_key,
            structured_expected=final_answer_expected(
                agent=agent, conversation=conversation
            ),
            output_schema=agent_output_schema(agent),
        )
        if dispatch.resumed:
            await restore_normalizer(
                self.uow_factory, normalizer, agent_run_id=agent_run_id
            )
        loop = asyncio.get_running_loop()
        state = _ConsumeState(
            deadline=loop.time() + dispatch.event_timeout_seconds,
            deadline_message=dispatch.deadline_message,
            accept_deadline=loop.time() + run_config.wait_timeout_seconds,
            slot_wait=SlotWait(wait_seconds=run_config.wait_timeout_seconds),
            lease_check_due_at=loop.time(),
            steering=SteerSchedule(
                enabled=dispatch.steerable,
                interval=self.steer_check_seconds,
                now=loop.time(),
            ),
            parked=ParkedWindow(
                window_seconds=self.parked_window_seconds,
                interval=self.parked_check_seconds,
                # Nothing can have been parked for minutes the moment a run is
                # dispatched; a reclaimed one waits one interval at most.
                next_check_at=loop.time() + self.parked_check_seconds,
            ),
            # Advances as refreshes land, so the run is bounded by the
            # credential it currently holds rather than the one it began with.
            credential_expires_at=dispatch.credential_expires_at,
        )
        reader = StreamReader(
            stream=self.events,
            run_id=agent_run_id,
            block_ms=self.stream_block_ms,
            cursor=dispatch.stream_cursor,
        )
        cursor = ConsumedCursor(
            self.uow_factory, self.events, agent_run_id, saved=dispatch.stream_cursor
        )

        while True:
            try:
                batch = await reader.next_batch()
            except StreamUnavailable as exc:
                # Our outage, not the host's: stop its run before giving up so
                # the provider is not left executing against the pod.
                await self._enqueue_host_cancel(agent_run_id)
                for event in failure_events(normalizer, agent_run_id, str(exc)):
                    yield event
                return

            applied = self._apply_batch(
                batch,
                normalizer=normalizer,
                cursor=cursor,
                ctx=ctx,
                conversation=conversation,
                agent_run_id=agent_run_id,
            )
            async with aclosing(applied):
                async for event in applied:
                    yield event
                    if is_terminal_event(event):
                        return

            ending = await self._between_batches(
                state,
                normalizer=normalizer,
                cursor=cursor,
                agent_run_id=agent_run_id,
                agent=agent,
                ctx=ctx,
                conversation=conversation,
                options=options,
            )
            while state.notices:
                yield state.notices.pop(0)
            if ending is not None:
                for event in ending:
                    yield event
                return

    async def _apply_batch(
        self,
        batch: StreamBatch,
        *,
        normalizer: AgentHostEventNormalizer,
        cursor: ConsumedCursor,
        ctx: ToolContext,
        conversation: Conversation,
        agent_run_id: UUID,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Each entry's events, then -- once all are handed on -- its place.

        Code after a ``yield`` runs only when the consumer asks for the next
        event, by which time it has persisted the last one; that is what lets
        the cursor say an entry's messages are on the record.
        """
        for entry in batch:
            events = await self._applier.events_for(
                normalizer,
                entry,
                ctx=ctx,
                conversation=conversation,
                agent_run_id=agent_run_id,
            )
            for event in events:
                yield event
            if cursor.passed(
                entry.stream_id,
                quiescent=normalizer.quiescent,
                persisted=any(event.type is AgentEventType.MESSAGE for event in events),
            ):
                await cursor.save()

    async def _between_batches[DepsT: ToolContext](
        self,
        state: _ConsumeState,
        *,
        normalizer: AgentHostEventNormalizer,
        cursor: ConsumedCursor,
        agent_run_id: UUID,
        agent: Agent,
        ctx: DepsT,
        conversation: Conversation,
        options: HarnessOptions,
    ) -> list[AgentEvent] | None:
        """What the loop checks while no event arrives; the last events if one
        of those checks ends the run."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now >= state.deadline:
            # The deadline we advertised has passed, so the host is either
            # gone or still working on a run we can no longer report. Cancel
            # it: without this the ACP agent keeps burning tokens and running
            # tools for a turn Lemma has already failed.
            await self._enqueue_host_cancel(agent_run_id)
            return failure_events(normalizer, agent_run_id, state.deadline_message)

        await self._steer(state.steering, now=now, agent_run_id=agent_run_id)
        # A turn parked on a person past its window is ended here, and comes
        # back as a terminal read as WAITING; see `parked_window`.
        await state.parked.check(
            self.uow_factory,
            now=now,
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
        )

        if now < state.lease_check_due_at:
            return None
        state.lease_check_due_at = now + self.lease_check_seconds
        await cursor.save()

        if await self._credential_spent(
            state,
            agent_run_id=agent_run_id,
            agent=agent,
            ctx=ctx,
            conversation=conversation,
            options=options,
        ):
            # Every refresh failed. Ending the run here is what keeps this
            # from becoming a run whose tools silently 401 for the rest of
            # its life without anything being reported to anyone.
            await self._enqueue_host_cancel(agent_run_id)
            return failure_events(normalizer, agent_run_id, CREDENTIAL_DEADLINE_MESSAGE)

        state.stop_sent = await self._cancel_if_requested(
            agent_run_id=agent_run_id,
            options=options,
            stop_sent=state.stop_sent,
        )
        outcome = await self._check_lease(
            agent_run_id=agent_run_id,
            seen_at=state.terminal_checkpoint_seen_at,
            state=state,
            now=loop.time(),
        )
        state.terminal_checkpoint_seen_at = outcome.seen_at

        if outcome.terminal_state is not None:
            # The run reached a terminal state without a terminal event, so
            # this is the only chance to pick up a final answer the tool
            # recorded before the stream went quiet.
            await adopt_recorded_final_answer(
                self.uow_factory, normalizer, agent_run_id=agent_run_id
            )
            return normalizer.finish_without_terminal(
                state=outcome.terminal_state,
                detail=outcome.terminal_detail,
            )
        if outcome.expired_state is not None:
            return [error_event(agent_run_id, expiry_message(outcome.expired_state))]
        return None

    async def _credential_spent[DepsT: ToolContext](
        self,
        state: _ConsumeState,
        *,
        agent_run_id: UUID,
        agent: Agent,
        ctx: DepsT,
        conversation: Conversation,
        options: HarnessOptions,
    ) -> bool:
        """Renew the run's credential when due; True once it cannot continue."""
        wall_clock = self._now()
        if credential_refresh_due(
            expires_at=state.credential_expires_at, now=wall_clock
        ):
            refreshed = await refresh_credential(
                uow_factory=self.uow_factory,
                agent_run_id=agent_run_id,
                ctx=ctx,
                options=options,
                agent=agent,
                conversation=conversation,
            )
            if refreshed is not None:
                state.credential_expires_at = refreshed
        return credential_exhausted(
            expires_at=state.credential_expires_at, now=wall_clock
        )

    async def _steer(
        self, steering: SteerSchedule, *, now: float, agent_run_id: UUID
    ) -> None:
        """Hand the host whatever the person has said since, when it is time."""
        if steering.due(now):
            await forward_queued_messages(self.uow_factory, agent_run_id=agent_run_id)

    async def _check_lease(
        self,
        *,
        agent_run_id: UUID,
        seen_at: float | None,
        state: _ConsumeState,
        now: float,
    ) -> LeaseOutcome:
        """Reconcile lease state, which never arrives as an event.

        Kept out of the consume loop so that loop stays about events.
        """
        async with self.uow_factory() as uow:
            repository = AgentHostDispatchRepository(uow)
            await repository.reconcile_expired_run(run_id=agent_run_id)
            lease = RunLease.of(await repository.get_run_lease(run_id=agent_run_id))
            await uow.commit()

        seen_at, terminal_state = terminal_checkpoint_state(
            lease=lease,
            seen_at=seen_at,
            now=now,
            grace_seconds=self.terminal_event_grace_seconds,
        )
        if terminal_state is not None:
            return LeaseOutcome(
                seen_at=seen_at,
                terminal_state=terminal_state,
                terminal_detail=lease_terminal_detail(lease),
            )

        # Not once a stop is sent: a stopped run that kept its place would be
        # handed to the computer when a slot freed, after the cancel that was
        # meant for it, and start then.
        if (
            not state.stop_sent
            and lease is not None
            and lease.state is AgentHostRunState.QUEUED_FOR_HOST
        ):
            held, notice = await state.slot_wait.check(
                self.uow_factory,
                AgentHostDispatchRepository,
                agent_run_id=agent_run_id,
            )
            if held:
                wait = state.slot_wait.wait_seconds
                state.accept_deadline = max(state.accept_deadline, now + wait)
            if notice is not None:
                state.notices.append(notice)
        expired_state = await self._expire_if_unaccepted(
            lease=lease,
            now=now,
            accept_deadline=state.accept_deadline,
            agent_run_id=agent_run_id,
        )
        return LeaseOutcome(seen_at=seen_at, expired_state=expired_state)

    async def _cancel_if_requested[DepsT](
        self,
        *,
        agent_run_id: UUID,
        options: HarnessOptions,
        stop_sent: bool,
    ) -> bool:
        if stop_sent or options.should_stop is None:
            return stop_sent
        if not await options.should_stop():
            return False
        await self._enqueue_host_cancel(agent_run_id)
        return True

    async def _enqueue_host_cancel(self, agent_run_id: UUID) -> None:
        """Ask the host to stop this run, and wake it up to hear that.

        A no-op once the lease is terminal, so it is safe to call from every
        path that gives up on a run.
        """
        async with self.uow_factory() as uow:
            command = await AgentHostDispatchRepository(uow).enqueue_cancel(
                run_id=agent_run_id
            )
            await uow.commit()
        if command is not None:
            await poke_host(command.host_id)

    async def _expire_if_unaccepted(
        self,
        *,
        lease: RunLease | None,
        now: float,
        accept_deadline: float,
        agent_run_id: UUID,
    ) -> AgentHostRunState | None:
        if lease is None or lease.accepted_at is not None or now < accept_deadline:
            return None
        async with self.uow_factory() as uow:
            state = await AgentHostDispatchRepository(uow).expire_unaccepted_run(
                run_id=agent_run_id
            )
            await uow.commit()
        return state
