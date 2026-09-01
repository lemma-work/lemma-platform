"""Resolving what a run needs before it can start.

Four lookups the runner performs once per run: the runtime profile behind the
conversation's configured model, the output type a TASK conversation forces,
and the connector accounts the agent is configured against.

A mixin rather than module functions because they are monkeypatched on the
instance in tests and read ``self.uow_factory``; moved out of
``agent_runner_service`` because that file is at the architecture ratchet's
per-file limit.
"""

from __future__ import annotations

from uuid import UUID

from app.core.crypto import get_secret_cipher
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    ConversationType,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
    ResolvedAgentRuntime,
)
from app.modules.agent.tools.callable_tool_factory import AgentCallableToolFactory
from app.modules.agent.tools.final_answer import get_final_answer_tool


class RunResolutionMixin:
    async def _resolve_agent_runtime(
        self,
        agent_runtime: AgentRuntimeConfig,
        *,
        user_id: UUID,
        organization_id: UUID | None,
    ) -> ResolvedAgentRuntime:
        with run_phase("resolve_runtime"):
            async with self.uow_factory() as uow:
                service = AgentRuntimeProfileService(
                    AgentRuntimeProfileRepository(
                        uow,
                        encryption=get_secret_cipher(),
                    )
                )
                return await service.resolve(
                    runtime=agent_runtime,
                    organization_id=organization_id,
                    user_id=user_id,
                )

    def _agent_with_resolved_runtime_metadata(
        self,
        agent: Agent,
        *,
        resolved_runtime: ResolvedAgentRuntime,
    ) -> Agent:
        del resolved_runtime
        return agent

    def _resolve_output_type(
        self, agent: Agent, conversation: Conversation
    ) -> object | None:
        # TASK conversations always get the final_answer tool: it drives the task
        # lifecycle (status WAITING/COMPLETED/FAILED), not just structured output.
        # The output *schema* is only applied when the agent configures one — see
        # get_final_answer_tool, which uses `output: str` otherwise (no schema is
        # pushed to the model when output_schema is absent).
        if conversation.type == ConversationType.TASK:
            return get_final_answer_tool(agent)
        return None

    async def _resolve_configured_accounts(
        self,
        *,
        agent: Agent,
        user_id: UUID,
    ) -> dict[str, UUID]:
        with run_phase("configured_accounts"):
            return await AgentCallableToolFactory(
                self.uow_factory
            ).resolve_configured_accounts(agent=agent, user_id=user_id)
