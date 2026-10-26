"""Applying one streamed Agent Host event: its side effects, then its messages.

Split from the harness, whose loop is about *when* -- deadlines, leases,
credentials, steering. This is *what* one entry does on its way to the
normalizer: rich content saved as pod files, the provider session remembered,
a steer's result recorded, the recorded final answer adopted at the end, and a
turn that ended because it was suspended reported as WAITING.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostEventType,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pauses import PERSON_KINDS
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.infrastructure.agent_host.event_stream import StreamedEvent
from app.modules.agent.infrastructure.agent_host.final_answer import (
    read_final_answer,
)
from app.modules.agent.infrastructure.agent_host.session_memory import (
    remember_provider_session,
)
from app.modules.agent.infrastructure.harnesses.agent_host.artifacts import (
    AgentHostArtifactWriter,
)
from app.modules.agent.infrastructure.harnesses.agent_host.events import (
    AgentHostEventEnvelope,
    AgentHostEventNormalizer,
    event_text,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_config import (
    resolve_pod_cwd,
)
from app.modules.agent.infrastructure.harnesses.agent_host.steering import (
    record_steer_result,
)
from app.modules.agent.infrastructure.db.pauses import Awaiting, Pauses
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel
from app.modules.agent.services.run_suspension import (
    run_suspended_on,
    run_suspended_while_parked,
)

_RICH_CONTENT_TYPES = frozenset(
    {
        AgentHostEventType.AGENT_MESSAGE_CHUNK.value,
        AgentHostEventType.AGENT_THOUGHT_CHUNK.value,
    }
)
# The terminals a suspended turn can come back as; see `_as_suspension`.
_ENDED_CLEANLY = frozenset({AgentEventType.COMPLETED, AgentEventType.STOPPED})

logger = get_logger(__name__)


class RunEventApplier:
    """Everything one stream entry does besides becoming messages."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        artifact_writer: AgentHostArtifactWriter | None,
    ) -> None:
        self.uow_factory = uow_factory
        self.artifact_writer = artifact_writer

    async def events_for(
        self,
        normalizer: AgentHostEventNormalizer,
        entry: StreamedEvent,
        *,
        ctx: ToolContext,
        conversation: Conversation,
        agent_run_id: UUID,
    ) -> list[AgentEvent]:
        envelope = AgentHostEventEnvelope(
            sequence=entry.sequence,
            type=entry.type,
            object_id=entry.object_id,
            payload=entry.payload,
        )
        events: list[AgentEvent] = []
        payload_override: JsonObject | None = None
        # Only a text chunk carries rich content blocks
        # (docs/architecture/agent-host-events.md#text); every other event is
        # already normalized, so there is nothing in it to save as a pod file.
        if self.artifact_writer is not None and entry.type in _RICH_CONTENT_TYPES:
            payload_override = await self._materialize(
                self.artifact_writer,
                normalizer,
                entry,
                ctx=ctx,
                conversation=conversation,
                agent_run_id=agent_run_id,
                warnings=events,
            )
        if entry.type == AgentHostEventType.RUN_STATE.value:
            await self._remember_session(entry, agent_run_id=agent_run_id)
        if entry.type == AgentHostEventType.STEER_RESULT.value:
            await record_steer_result(
                self.uow_factory,
                agent_run_id=agent_run_id,
                conversation_id=conversation.id,
                message_id=entry.object_id,
                payload=entry.payload,
            )
        if entry.type == AgentHostEventType.TERMINAL.value:
            await adopt_recorded_final_answer(
                self.uow_factory, normalizer, agent_run_id=agent_run_id
            )
        events.extend(normalizer.normalize(envelope, payload_override=payload_override))
        if entry.type == AgentHostEventType.TERMINAL.value:
            events = await self._as_suspension(
                events, agent_run_id=agent_run_id, conversation=conversation
            )
        return events

    async def _materialize(
        self,
        writer: AgentHostArtifactWriter,
        normalizer: AgentHostEventNormalizer,
        entry: StreamedEvent,
        *,
        ctx: ToolContext,
        conversation: Conversation,
        agent_run_id: UUID,
        warnings: list[AgentEvent],
    ) -> JsonObject | None:
        """Save a chunk's rich content as pod files; the text that links them."""
        materialized = await writer.materialize_event(
            payload=entry.payload,
            pod_id=conversation.pod_id,
            user_context=ctx,
            directory_path=f"{resolve_pod_cwd(conversation)}/agent-output",
            agent_run_id=agent_run_id,
            event_sequence=entry.sequence,
            harness_key=normalizer.harness_key,
        )
        for warning in materialized.warnings:
            warnings.append(
                AgentEvent(
                    type=AgentEventType.STATUS,
                    data={
                        "status": "agent_host.artifact_warning",
                        "detail": warning,
                        "agent_host_sequence": entry.sequence,
                    },
                    agent_run_id=agent_run_id,
                    sequence=entry.sequence,
                )
            )
        if not materialized.markdown:
            return None
        payload_override = dict(entry.payload)
        existing = event_text(payload_override)
        payload_override["text"] = (
            f"{existing}\n\n{materialized.markdown}"
            if existing
            else materialized.markdown
        )
        return payload_override

    async def _remember_session(
        self, entry: StreamedEvent, *, agent_run_id: UUID
    ) -> None:
        session_id = entry.payload.get("provider_session_id")
        state = entry.payload.get("state")
        if not (isinstance(session_id, str) and session_id and isinstance(state, str)):
            return
        # Session establishment precedes the prompt in the ordered event
        # stream. Save it before consuming any answer events.
        async with self.uow_factory() as uow:
            lease = await uow.session.get(AgentHostRunLeaseModel, agent_run_id)
            if lease is not None:
                await remember_provider_session(
                    uow,
                    AgentHostRunCheckpoint(
                        run_id=agent_run_id,
                        lease_epoch=lease.lease_epoch,
                        state=AgentHostRunState(state),
                        detail=entry.payload,
                    ),
                )
                await uow.commit()

    async def _as_suspension(
        self,
        events: list[AgentEvent],
        *,
        agent_run_id: UUID,
        conversation: Conversation,
    ) -> list[AgentEvent]:
        """Re-read a turn that ended as one that was suspended, if it was.

        ``wait_for`` on a remote harness cannot end the turn from inside its own
        tool call, so Lemma asks the host to stop it. The host has no idea why
        and reports what it saw: ``CANCELLED`` when the stop arrived first,
        ``SUCCEEDED`` when the agent finished talking before it did. Neither is
        what happened. The durable wait row is, and it says the agent is asleep
        — which is ``WAITING``, exactly as the in-process harness reports the
        same tool.

        Failures are left alone. A run that hit its context ceiling or lost its
        host really did fail, and a wait row does not make that untrue; the
        timer still wakes the conversation either way.

        A turn Lemma ended because it sat parked on a person past its window
        (``parked_window``) is read the same way, from the mark that window
        left on the run. Its pause stays open: the return the normalizer
        synthesizes for a call the turn left unanswered is dropped for a
        pausing call, which the person's decision answers later instead.
        """
        if not any(event.type in _ENDED_CLEANLY for event in events):
            return events
        still_open: set[str] = set()
        async with self.uow_factory() as uow:
            wait = await run_suspended_on(uow, agent_run_id=agent_run_id)
            parked = (
                await run_suspended_while_parked(uow, agent_run_id=agent_run_id)
                if wait is None
                else None
            )
            if parked is not None:
                still_open = {
                    pause.tool_call_id
                    for pause in await Pauses(uow).pending(
                        run_id=agent_run_id,
                        kinds=PERSON_KINDS,
                        awaiting=Awaiting.EITHER,
                    )
                }
        if wait is not None:
            pause_id, kind = wait.tool_call_id, wait.tool_name
        elif parked is not None:
            pause_id, kind = parked.tool_call_id, parked.tool_name
            events = [
                event for event in events if not _closes_a_pause(event, still_open)
            ]
        else:
            return events
        return [
            AgentEvent(
                type=AgentEventType.WAITING,
                # The shape the in-process pause yields, so that everything
                # downstream of a paused turn reads one event, not two.
                data={
                    "tool_call_id": pause_id,
                    "kind": kind,
                    "conversation_id": str(conversation.id),
                },
                agent_run_id=agent_run_id,
                sequence=event.sequence,
            )
            if event.type in _ENDED_CLEANLY
            else event
            for event in events
        ]


def _closes_a_pause(event: AgentEvent, still_open: set[str]) -> bool:
    """A return made up at the turn's end for a call a person still has to answer."""
    draft = event.data
    return (
        event.type is AgentEventType.MESSAGE
        and isinstance(draft, MessageDraft)
        and draft.kind is MessageKind.TOOL_RETURN
        and draft.tool_call_id in still_open
        and draft.state.synthetic_tool_return
    )


async def adopt_recorded_final_answer(
    uow_factory: UnitOfWorkFactory,
    normalizer: AgentHostEventNormalizer,
    *,
    agent_run_id: UUID,
) -> None:
    """Let a run prefer the answer the tool recorded over what it inferred.

    Call this as a run goes terminal. Deliberately *not* on the hard-failure
    paths (stream unavailable, deadline): those abandon the run, and reporting a
    COMPLETED structured answer for a run we gave up on would be a lie.
    """
    if not normalizer.structured_expected:
        return
    try:
        record = await read_final_answer(uow_factory, agent_run_id=agent_run_id)
    except SQLAlchemyError as exc:
        raise_if_database_unavailable(exc)
        # The stream-inferred answer, if any, still stands.
        logger.warning(
            "agent.agent_host.final_answer_read_failed.degraded", exc_info=True
        )
        return
    normalizer.adopt_final_answer(record)


__all__ = ["RunEventApplier", "adopt_recorded_final_answer"]
