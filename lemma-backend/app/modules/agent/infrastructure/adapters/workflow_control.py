"""Driving an agent conversation on a workflow's behalf.

Implements `workflow`'s own `AgentPort`. Here rather than in a third package
because every collaborator it uses -- the agent and conversation repositories,
the wait store, the runtime profile default -- belongs to this module, and a
build step that lives elsewhere has to know where inside `agent` each of them
happens to sit. The two that are services rather than repositories -- stopping
a run and the pod's default runtime -- are handed in by the contract that
builds it (`contracts/workflow_control`), so infrastructure does not import the
service layer.

Values crossing the port are `object`, not `Any`: a workflow's node output is
JSON whose shape belongs to the workflow's author, and saying `Any` would let a
caller read a key off it without the type checker asking.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Protocol
from uuid import UUID

from app.modules.pod.contracts.agent_access import pod_organization_id
from app.core.domain.generation import GenerationSettings
from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.contracts.turns import AgentTurnStart, stage_agent_turn
from app.modules.agent.domain.project_repo import parse_project_repo
from app.modules.agent.domain.run_state import RunState, ScheduleFiring
from app.modules.agent.domain.value_objects import (
    AgentRunFinishResult,
    ConversationOrigin,
    ConversationStatus,
    ConversationType,
    MessageDraft,
    MessageRole,
    RunTrigger,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.infrastructure.conversation_idempotency_store import (
    create_conversation_for_id,
)
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.workflow.contracts import AgentPort

#: The run source whose opening message is a wake-up rather than a node's input.
SCHEDULE_SOURCE = "SCHEDULE"

#: Keys of a firing's ``input_data["metadata"]`` that describe the *firing*,
#: not the event, written by `ScheduleStartService._build_trigger`. Rendered as
#: a sentence at the top of the wake message, so they are dropped from the
#: event body below it rather than printed twice -- and recorded on the run as
#: its ``schedule_firing``.
_FIRING_FACTS = frozenset(
    {
        "schedule_id",
        "schedule_name",
        "trigger_type",
        "fired_at",
        "scheduled_for",
        "timezone",
    }
)


def _schedule_firing(
    input_data: dict[str, object],
    conversation_metadata: dict[str, object],
    schedule_type: str | None,
) -> ScheduleFiring | None:
    """The facts about the firing that started this run, as the run keeps them."""
    raw = input_data.get("metadata")
    facts = {
        key: value
        for key, value in (raw if isinstance(raw, dict) else {}).items()
        if key in _FIRING_FACTS
    }
    facts.setdefault("schedule_name", conversation_metadata.get("schedule_name"))
    facts.setdefault("trigger_type", schedule_type)
    return ScheduleFiring.from_json(facts)


class RunStopper(Protocol):
    """Stops one run in the caller's transaction (``services/run_stop``)."""

    def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        repository: Runs,
        *,
        pod_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Awaitable[AgentRunFinishResult | None]: ...


class PodDefaultRuntime(Protocol):
    """This module's default runtime for a pod (``services/runtime_resolution``)."""

    def __call__(
        self, uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
    ) -> Awaitable[AgentRuntimeConfig]: ...


class AgentControlAdapter(AgentPort):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        stop_run: RunStopper,
        default_runtime: PodDefaultRuntime,
    ) -> None:
        self.uow = uow
        self._stop_run = stop_run
        self._default_runtime = default_runtime
        self.agent_repo = AgentRepository(uow)
        self.conversations = Conversations(uow)
        self.messages = Messages(uow)
        self.runs = Runs(uow)
        self.pauses = Pauses(uow)

    async def run_agent(
        self,
        agent_name: str,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, object] | None = None,
        instructions: str | None = None,
        model_name: str | None = None,
        generation: GenerationSettings | None = None,
    ) -> UUID:
        agent = await self.agent_repo.get_by_pod_and_name(
            pod_id=pod_id,
            name=agent_name,
        )
        if agent is None:
            raise AgentNotFoundError("Workflow agent target was not found")
        runtime_override = None
        if model_name is not None or generation is not None:
            base = agent.agent_runtime or await self._default_agent_runtime_for_pod(
                pod_id=pod_id
            )
            runtime_override = base.with_overrides(
                model_name=model_name, settings=generation
            )
        return await self._start_conversation(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_runtime=agent.agent_runtime,
            runtime_override=runtime_override,
            input_data=input_data,
            pod_id=pod_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_run_id=workflow_run_id,
            source=source,
            conversation_metadata=conversation_metadata,
            instructions=instructions,
        )

    async def _start_conversation(
        self,
        *,
        agent_id: UUID | None,
        agent_name: str,
        agent_runtime: AgentRuntimeConfig | None,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None,
        workflow_run_id: UUID | None,
        source: str,
        conversation_metadata: dict[str, object] | None,
        instructions: str | None,
        runtime_override: AgentRuntimeConfig | None = None,
    ) -> UUID:
        started_by_schedule = source == SCHEDULE_SOURCE
        # What the caller says about where the conversation came from. The
        # repository a webhook delivery names is the run's checkout; the rest
        # (which schedule, which kind) is kept as the conversation's own.
        metadata = dict(conversation_metadata or {})
        repo = parse_project_repo(metadata.pop("repo", None))
        raw_type = metadata.pop("schedule_type", None)
        schedule_type = raw_type.upper() if isinstance(raw_type, str) else None
        entity_values: dict[str, object] = {
            "user_id": user_id,
            "pod_id": pod_id,
            "organization_id": await self._get_pod_organization_id(pod_id),
            "agent_id": agent_id,
            # A schedule's runs are told apart by which schedule started them,
            # not by which agent they woke: one agent on four schedules produced
            # four identical "Workflow run: triage-agent" rows, naming a
            # workflow that never existed.
            "title": self._conversation_title(
                agent_name, input_data, started_by_schedule
            ),
            "type": ConversationType.TASK,
            "metadata": metadata,
            "origin": (
                ConversationOrigin.SCHEDULE
                if started_by_schedule
                else ConversationOrigin.WORKFLOW_STEP
            ),
            "workflow_run_id": workflow_run_id,
            "schedule_type": schedule_type,
            "repo": repo,
            # What the trigger asked for, in the author's words. Lands in the
            # system prompt as `# Conversation Instructions`, after the agent's
            # own instruction — so a named agent keeps its identity and gains a
            # task, and the default assistant, whose instruction is empty, gets
            # the only thing telling it why it woke up.
            "instructions": instructions,
            # On the conversation, not only the first run: a step whose agent
            # stops to ask somebody something resumes on a later run, and that
            # run must still be on the step's model and settings.
            "agent_runtime": runtime_override,
        }
        if conversation_id is not None:
            entity_values["id"] = conversation_id
        entity = Conversation(
            **entity_values,
        )
        if conversation_id is not None:
            conversation, created = await create_conversation_for_id(
                self.uow.session,
                entity,
            )
            if not created:
                return conversation.id
        else:
            conversation = await self.conversations.create_conversation(entity)

        runtime = (
            runtime_override
            or agent_runtime
            or await self._default_agent_runtime_for_pod(pod_id=pod_id)
        )
        run = await self.runs.create_agent_run(
            conversation_id=conversation.id,
            agent_id=agent_id,
            agent_runtime=runtime,
            state=RunState(
                trigger=RunTrigger.SCHEDULE_FIRE
                if started_by_schedule
                else RunTrigger.WORKFLOW_START,
                schedule_firing=_schedule_firing(input_data, metadata, schedule_type)
                if started_by_schedule
                else None,
            ),
        )
        await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text(
                self._schedule_wake_prompt(input_data, instructions)
                if started_by_schedule
                else self._workflow_input_prompt(input_data),
                role=MessageRole.USER,
                metadata={"source": source, "content_format": "json"},
            ),
        )
        await stage_agent_turn(
            self.uow,
            AgentTurnStart(
                conversation_id=conversation.id,
                run_id=run.id,
                pod_id=pod_id,
                user_id=user_id,
                agent_name=agent_name,
                origin="schedule" if started_by_schedule else "workflow",
            ),
        )
        return conversation.id

    async def run_agent_by_id(
        self,
        agent_id: UUID,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, object] | None = None,
        instructions: str | None = None,
    ) -> UUID:
        agent = await self.agent_repo.get(agent_id)
        if agent is None or agent.pod_id != pod_id:
            raise AgentNotFoundError("Workflow agent target was not found")
        return await self.run_agent(
            agent_name=agent.name,
            input_data=input_data,
            pod_id=pod_id,
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_run_id=workflow_run_id,
            source=source,
            conversation_metadata=conversation_metadata,
            instructions=instructions,
        )

    async def get_conversation_status(self, conversation_id: UUID) -> dict[str, object]:
        # With its latest run: `last_run_error` below is derived from it, and
        # without it a failed step only ever said that the agent failed.
        conversation = await self.conversations.get_conversation(
            conversation_id, include_runs=True
        )
        if conversation is None or conversation.status is None:
            return {"status": "NOT_FOUND"}
        output = self._normalize_agent_output(conversation.output)
        if conversation.status is ConversationStatus.COMPLETED:
            return {"status": "COMPLETED", "output_data": output}
        if conversation.status is ConversationStatus.WAITING:
            # The expiry policy needs to know *why* a conversation is waiting. An
            # agent blocked on a person is the hang the ceiling exists to catch;
            # a waiting agent will wake itself and is perfectly healthy, so
            # failing its run would be a silent wrong outcome rather than a
            # visible error. See `run_resume_service._expire_overdue_wait`.
            waits = await self.pauses.pending(conversation_id, kinds=(PauseKind.WAIT,))
            wakes_at = waits[0].wake_at if waits else None
            return {
                "status": "WAITING",
                "wait_reason": "WAIT" if waits else "HUMAN",
                "wakes_at": wakes_at.isoformat() if wakes_at else None,
                "output_data": output,
            }
        if conversation.status in {
            ConversationStatus.FAILED,
            ConversationStatus.STOPPED,
        }:
            # Carry the run's own error, not just the status. Without it the
            # workflow records "Agent conversation FAILED" and the reason -- which
            # the agent already wrote to `last_run_error` -- is lost, so the run
            # says an agent failed and nothing about why.
            reason = (conversation.last_run_error or "").strip()
            return {
                "status": "FAILED",
                "error": (
                    f"Agent conversation {conversation.status.value}: {reason}"
                    if reason
                    else f"Agent conversation {conversation.status.value}"
                ),
                "output_data": output,
            }
        return {"status": "RUNNING"}

    async def stop_conversation(self, conversation_id: UUID, user_id: UUID) -> None:
        """Ask the conversation's active run to stop.

        Mirrors ``StopConversation`` minus the access checks —
        the caller is the engine cancelling a run it already authorized, not a
        user reaching in. Nothing to stop is success, not an error: the agent
        may have finished between the cancel and this call.
        """
        active_run = await self.runs.get_active_agent_run_for_update(conversation_id)
        if active_run is None:
            return
        del user_id  # the engine already authorized the cancel
        await self._stop_run(
            self.uow,
            self.runs,
            pod_id=await self._conversation_pod(conversation_id),
            conversation_id=conversation_id,
            run_id=active_run.id,
        )

    async def _conversation_pod(self, conversation_id: UUID) -> UUID:
        conversation = await self.conversations.get_conversation(conversation_id)
        if conversation is None:
            raise AgentNotFoundError("Workflow agent conversation was not found")
        return conversation.pod_id

    async def _default_agent_runtime_for_pod(
        self, *, pod_id: UUID
    ) -> AgentRuntimeConfig:
        # This module's own answer, not a second copy of it. Reading
        # `Pod.config` here directly is how a workflow-started run could
        # resolve a different default model than the same agent started from a
        # conversation.
        return await self._default_runtime(self.uow, pod_id=pod_id)

    async def _get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        return await pod_organization_id(self.uow, pod_id)

    @staticmethod
    def _normalize_agent_output(output: object) -> dict[str, object]:
        if isinstance(output, dict):
            return output
        if output is None or output == "":
            return {}
        return {"answer": output}

    @staticmethod
    def _conversation_title(
        agent_name: str, input_data: dict[str, object], started_by_schedule: bool
    ) -> str:
        if started_by_schedule:
            metadata = input_data.get("metadata")
            name = metadata.get("schedule_name") if isinstance(metadata, dict) else None
            if isinstance(name, str) and name.strip():
                return f"Schedule: {name.strip()}"
            return f"Scheduled run: {agent_name}"
        return f"Workflow run: {agent_name}"

    @staticmethod
    def _workflow_input_prompt(input_data: dict[str, object]) -> str:
        payload = json.dumps(input_data, ensure_ascii=True, indent=2, default=str)
        return f"Workflow input JSON:\n{payload}"

    @staticmethod
    def _firing_sentences(metadata: dict[str, object]) -> list[str]:
        """What fired, and when, as prose rather than as JSON keys."""

        def _text(key: str) -> str | None:
            value = metadata.get(key)
            return value if isinstance(value, str) and value.strip() else None

        name = _text("schedule_name")
        lines = [
            f'The schedule "{name}" started this run.'
            if name
            else "A schedule started this run."
        ]

        fired_at = _text("fired_at")
        scheduled_for = _text("scheduled_for")
        timezone_name = _text("timezone")
        when = []
        if fired_at:
            when.append(f"Fired at {fired_at}.")
        # Only worth saying when it differs: for a fire that ran on time the two
        # are the same instant, and printing both invites the agent to wonder
        # which one matters.
        if scheduled_for and scheduled_for != fired_at:
            when.append(f"This is the occurrence due at {scheduled_for}.")
        if timezone_name:
            when.append(f"The schedule is set in {timezone_name}; times above are UTC.")
        if when:
            lines.append(" ".join(when))
        return lines

    @staticmethod
    def _firing_event_body(
        input_data: dict[str, object], metadata: dict[str, object]
    ) -> dict[str, object]:
        """The event itself, with the empty parts and the firing facts removed.

        A TIME firing has nothing left here, which is the point: it gets a
        sentence and a task rather than three empty objects. A webhook or
        datastore firing still carries its delivery or its row in full.
        """
        body = {
            key: value
            for key, value in input_data.items()
            if key != "metadata" and value
        }
        event_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in _FIRING_FACTS and value
        }
        if event_metadata:
            body["metadata"] = event_metadata
        return body

    @classmethod
    def _schedule_wake_prompt(
        cls, input_data: dict[str, object], instructions: str | None
    ) -> str:
        """What a schedule-started agent reads as its first turn.

        The workflow rendering was wrong here in a way nothing caught, because
        no test pinned this text. A cron-fired agent's opening message was:

            Workflow input JSON:
            {"payload": {}, "metadata": {}, "llm_output": {}}

        -- three empty objects, under a heading naming a workflow that does not
        exist, for a run started by a clock. Everything telling the agent why it
        had woken was in the system prompt, where it reads as standing
        configuration rather than as the thing that just happened.

        So: say what fired and when, then the task, then the event body.

        `instructions` is repeated here deliberately. The system prompt's
        `# Conversation Instructions` is what survives into later turns of a
        multi-turn run; this is what the first turn is answering.
        """
        raw = input_data.get("metadata")
        metadata = raw if isinstance(raw, dict) else {}
        lines = cls._firing_sentences(metadata)

        if instructions and instructions.strip():
            lines.append(f"What to do:\n{instructions.strip()}")

        body = cls._firing_event_body(input_data, metadata)
        if body:
            rendered = json.dumps(body, ensure_ascii=True, indent=2, default=str)
            lines.append(f"Event data:\n{rendered}")
        elif metadata.get("trigger_type") == "TIME":
            lines.append("There is no event data: this firing is the time arriving.")

        return "\n\n".join(lines)
