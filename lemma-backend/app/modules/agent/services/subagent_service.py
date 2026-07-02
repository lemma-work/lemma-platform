"""Spawn and interact with sub-agent conversations.

When an agent calls another agent (or a JOB-type function) as a tool we create a
real, persisted child conversation linked by ``parent_id`` (and the child run by
``parent_run_id``) and run it through the normal background job queue — instead
of a blocking, ephemeral inline run. The parent can then spawn, await, read
messages from, message, and stop those running sub-conversations.

Authorization: spawning/messaging/stopping run under the parent agent's
delegated-workload context, so the parent's ``agent.execute`` grant on the child
is honored. Reads are guarded by ownership — an agent may only inspect
conversations it actually spawned (``parent_id`` linkage + same user).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID, DEFAULT_POD_AGENT_NAME
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    AgentRunStatus,
    JsonObject,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.pod.services.authorization_factory import create_authorization_service
from app.modules.usage.services.usage_service_factory import build_usage_service


class SubAgentError(RuntimeError):
    """Raised for sub-agent operations the caller is not allowed to perform."""


@dataclass(slots=True)
class SubAgentHandle:
    conversation_id: UUID
    run_id: UUID | None
    status: str


_AWAIT_POLL_SECONDS = 1.0


class SubAgentService:
    def __init__(self, uow_factory: SessionUnitOfWorkFactory):
        self.uow_factory = uow_factory

    # -- context helpers ----------------------------------------------------

    def _is_default(self, deps) -> bool:
        return deps.workload_id in (None, DEFAULT_POD_AGENT_ID) or deps.agent_name in (
            None,
            DEFAULT_POD_AGENT_NAME,
        )

    def _conversation_service(self, uow) -> ConversationService:
        return ConversationService(
            uow=uow,
            conversation_repository=ConversationRepository(uow),
            agent_repository=AgentRepository(uow),
            authorization_service=create_authorization_service(uow),
            usage_service=build_usage_service(uow),
        )

    async def _agent_ctx(self, uow, deps):
        """Parent agent's delegated context (honors its agent.execute grant)."""
        return await create_authorization_service(uow).build_delegated_workload_context(
            user_id=deps.user_id,
            principal_type="AGENT",
            principal_id=deps.workload_id or DEFAULT_POD_AGENT_ID,
            pod_id=deps.pod_id,
            is_default_pod_agent=self._is_default(deps),
            delegation_actor_name=deps.agent_name,
            # Session approvals (APPROVE_FOR_SESSION) are keyed by conversation.
            delegation_session_id=str(deps.conversation_id),
        )

    def _input_prompt(self, input_data: JsonObject | str) -> str:
        # A plain string is the sub-agent's task verbatim; a dict is structured
        # input rendered as JSON.
        if isinstance(input_data, str):
            return input_data
        payload = json.dumps(input_data, ensure_ascii=True, indent=2, default=str)
        return f"Sub-agent task input (JSON):\n{payload}"

    # -- operations ---------------------------------------------------------

    async def spawn(
        self,
        deps,
        *,
        agent_name: str | None = None,
        input_data: JsonObject | str,
    ) -> SubAgentHandle:
        # Self-spawn: omit agent_name (or pass the parent's own name) to launch
        # another instance of the agent already running in this conversation. The
        # parent has no agent.execute grant on itself, so self-spawn skips the
        # grant check. `is_self` is derived from server-side deps, never from a
        # model-supplied *other* name, so a named other agent stays grant-gated.
        is_self = agent_name is None or (
            not self._is_default(deps) and agent_name == deps.agent_name
        )
        target_name = (
            (None if self._is_default(deps) else deps.agent_name)
            if is_self
            else agent_name
        )
        async with self.uow_factory() as uow:
            token = set_current_context(await self._agent_ctx(uow, deps))
            try:
                service = self._conversation_service(uow)
                conversation = await service.create_conversation(
                    pod_id=deps.pod_id,
                    agent_name=target_name,
                    user_id=deps.user_id,
                    parent_id=deps.conversation_id,
                    require_execute_grant=not is_self,
                    metadata={
                        # Source of truth for depth=1 gating (RunToolAssembler):
                        # this child IS a sub-agent, so its run gets no spawn tools.
                        "is_sub_agent": True,
                        "spawned_by_agent": deps.agent_name,
                        "parent_run_id": str(deps.agent_run_id)
                        if deps.agent_run_id
                        else None,
                        "self_spawned": is_self,
                    },
                )
                result = await service.add_user_message_and_start_run(
                    conversation_id=conversation.id,
                    user_id=deps.user_id,
                    content=self._input_prompt(input_data),
                    pod_id=deps.pod_id,
                    agent_name=target_name,
                    message_metadata={"source": "subagent"},
                    require_execute_grant=not is_self,
                )
                await uow.commit()
                return SubAgentHandle(
                    conversation_id=result.conversation_id,
                    run_id=result.agent_run_id,
                    status=AgentRunStatus.RUNNING.value,
                )
            finally:
                reset_current_context(token)

    async def _owned_child(self, uow, deps, conversation_id: UUID) -> Conversation:
        conversation = await ConversationRepository(uow).get_conversation(
            conversation_id,
            include_runs=True,
        )
        if (
            conversation is None
            or conversation.user_id != deps.user_id
            or conversation.parent_id != deps.conversation_id
        ):
            raise SubAgentError(
                "Sub-conversation not found or not spawned by this agent."
            )
        return conversation

    async def list_children(
        self,
        deps,
        *,
        limit: int = 50,
        status_filter: str | None = None,
    ) -> list[dict[str, object]]:
        """List child conversations spawned from the current conversation."""
        async with self.uow_factory() as uow:
            children = await ConversationRepository(uow).list_children(
                parent_id=deps.conversation_id,
                user_id=deps.user_id,
                limit=limit,
            )
            agent_repo = AgentRepository(uow)
            rows: list[dict[str, object]] = []
            for child in children:
                latest = child.agent_runs[-1] if child.agent_runs else None
                run_status = (
                    latest.status.value
                    if latest is not None
                    else (child.status.value if child.status else "UNKNOWN")
                )
                if (
                    status_filter
                    and status_filter.upper() == "ACTIVE"
                    and (latest is None or latest.status not in ACTIVE_AGENT_RUN_STATUSES)
                ):
                    continue
                agent = (
                    await agent_repo.get(child.agent_id) if child.agent_id else None
                )
                rows.append(
                    {
                        "conversation_id": str(child.id),
                        "agent_name": agent.name if agent else DEFAULT_POD_AGENT_NAME,
                        "title": child.title,
                        "status": run_status,
                        "created_at": child.created_at.isoformat()
                        if child.created_at
                        else None,
                    }
                )
            return rows

    async def get_messages(
        self,
        deps,
        *,
        conversation_id: UUID,
        after_sequence: int | None = None,
        limit: int = 50,
    ) -> list[Message]:
        async with self.uow_factory() as uow:
            await self._owned_child(uow, deps, conversation_id)
            messages, _ = await ConversationRepository(uow).list_messages(
                conversation_id=conversation_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            return messages

    async def status(self, deps, *, conversation_id: UUID) -> dict[str, object]:
        async with self.uow_factory() as uow:
            conversation = await self._owned_child(uow, deps, conversation_id)
            latest = conversation.agent_runs[-1] if conversation.agent_runs else None
            if latest is None:
                return {"conversation_id": str(conversation_id), "status": "UNKNOWN"}
            return {
                "conversation_id": str(conversation_id),
                "run_id": str(latest.id),
                "status": latest.status.value,
                "output": latest.output_data,
                "error": latest.error,
            }

    async def send(
        self,
        deps,
        *,
        conversation_id: UUID,
        content: str,
    ) -> SubAgentHandle:
        async with self.uow_factory() as uow:
            child = await self._owned_child(uow, deps, conversation_id)
            token = set_current_context(await self._agent_ctx(uow, deps))
            try:
                service = self._conversation_service(uow)
                agent = (
                    await AgentRepository(uow).get(child.agent_id)
                    if child.agent_id
                    else None
                )
                result = await service.add_user_message_and_start_run(
                    conversation_id=conversation_id,
                    user_id=deps.user_id,
                    content=content,
                    pod_id=deps.pod_id,
                    agent_name=agent.name if agent else None,
                    message_metadata={"source": "subagent_message"},
                    # Ownership is already proven via _owned_child (this child was
                    # spawned by this agent), so skip the cross-agent read/execute
                    # grant check — mirrors self-spawn in spawn().
                    require_execute_grant=False,
                )
                await uow.commit()
                return SubAgentHandle(
                    conversation_id=result.conversation_id,
                    run_id=result.agent_run_id,
                    status=AgentRunStatus.RUNNING.value,
                )
            finally:
                reset_current_context(token)

    async def stop(self, deps, *, conversation_id: UUID) -> dict[str, object]:
        async with self.uow_factory() as uow:
            child = await self._owned_child(uow, deps, conversation_id)
            token = set_current_context(await self._agent_ctx(uow, deps))
            try:
                service = self._conversation_service(uow)
                agent = (
                    await AgentRepository(uow).get(child.agent_id)
                    if child.agent_id
                    else None
                )
                conversation = await service.stop_conversation(
                    conversation_id=conversation_id,
                    user_id=deps.user_id,
                    pod_id=deps.pod_id,
                    agent_name=agent.name if agent else None,
                )
                await uow.commit()
                return {
                    "conversation_id": str(conversation_id),
                    "status": conversation.status.value
                    if conversation.status
                    else "STOPPED",
                }
            finally:
                reset_current_context(token)

    async def await_run(
        self,
        deps,
        *,
        conversation_id: UUID,
        run_id: UUID,
        timeout_seconds: float,
    ) -> dict[str, object]:
        """Poll the child run until terminal or timeout (fresh reads each tick)."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            async with self.uow_factory() as uow:
                await self._owned_child(uow, deps, conversation_id)
                run = await ConversationRepository(uow).get_agent_run(run_id)
            if run is not None and run.status not in ACTIVE_AGENT_RUN_STATUSES:
                return {
                    "conversation_id": str(conversation_id),
                    "run_id": str(run_id),
                    "status": run.status.value,
                    "output": run.output_data,
                    "error": run.error,
                }
            if loop.time() >= deadline:
                return {
                    "conversation_id": str(conversation_id),
                    "run_id": str(run_id),
                    "status": run.status.value if run else "RUNNING",
                    "timed_out": True,
                    "hint": (
                        "Still running; poll query_subagents (mode='messages') "
                        "or interact_subagent (action='await') again."
                    ),
                }
            await asyncio.sleep(_AWAIT_POLL_SECONDS)
