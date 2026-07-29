"""Explicit cloud fallback for an unaccepted Agent Host run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator
from uuid import UUID

from pydantic_ai import UsageLimits

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent.capabilities import build_lemma_harness_tooling
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeProfileType,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRuntimeConfig,
    HarnessKind,
    HarnessOptions,
    JsonObject,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
    view_image_toolset,
)
from app.composition.agent_usage import UsageReservation

if TYPE_CHECKING:
    from app.modules.agent.services.agent_runner_service import AgentRunnerService


logger = get_logger(__name__)


@dataclass(slots=True)
class RuntimeExecutionState:
    profile: dict[str, object | None] | None = None
    reservation: UsageReservation | None = None


def profile_model_settings(
    runtime_profile_snapshot: dict[str, object | None] | None,
) -> JsonObject | None:
    if not isinstance(runtime_profile_snapshot, dict):
        return None
    config = runtime_profile_snapshot.get("config")
    if not isinstance(config, dict):
        return None
    model_settings = config.get("model_settings")
    return (
        model_settings if isinstance(model_settings, dict) and model_settings else None
    )


async def run_agent_host_fallback(
    fallback_profile_id: str,
    *,
    service: AgentRunnerService,
    state: RuntimeExecutionState,
    user_id: UUID,
    conversation: Conversation,
    ctx: ConversationContext,
    full_toolsets: list[object],
    agent: Agent,
    agent_run_id: UUID,
    messages: list[Message],
    usage_limits: UsageLimits,
) -> AsyncIterator[AgentEvent]:
    """Run a fallback only before Agent Host has durably accepted work."""
    fallback_runtime = await service._resolve_agent_runtime(
        AgentRuntimeConfig(profile_id=fallback_profile_id),
        user_id=user_id,
        organization_id=conversation.organization_id,
    )
    if fallback_runtime.harness_kind is HarnessKind.HARNESS:
        raise RuntimeError("Agent Host fallback chains are not supported")

    primary_profile_id = (
        state.profile.get("profile_id") if state.profile is not None else None
    )
    fallback_snapshot = fallback_runtime.public_snapshot()
    fallback_snapshot["fallback_from_profile_id"] = primary_profile_id
    fallback_credentials = fallback_runtime.credentials or {}
    fallback_ctx = ctx.model_copy(
        update={
            "runtime_profile": fallback_snapshot,
            "runtime_credentials": fallback_credentials,
            "supports_pause_signal": (
                fallback_runtime.harness_kind is HarnessKind.LEMMA
            ),
        }
    )

    fallback_toolsets = list(full_toolsets)
    fallback_supports_vision = (
        fallback_runtime.model is not None
        and RuntimeModelCapability.VISION in fallback_runtime.model.capabilities
    )
    if fallback_supports_vision and view_image_toolset not in fallback_toolsets:
        fallback_toolsets.append(view_image_toolset)

    fallback_capabilities: list[object] = []
    fallback_model_settings: JsonObject | None = None
    if fallback_runtime.harness_kind is HarnessKind.LEMMA:
        fallback_model_settings = profile_model_settings(fallback_snapshot)
        fallback_capabilities = await build_lemma_harness_tooling(
            uow_factory=service.uow_factory,
            agent=agent,
            ctx=fallback_ctx,
            full_toolsets=fallback_toolsets,
            agent_run_id=agent_run_id,
            model_name=fallback_runtime.model_name_for_harness,
            enable_prompt_caching=(
                fallback_runtime.profile.runtime_type
                is RuntimeProfileType.OPENAI_COMPATIBLE
                and settings.lemma_llm_caching_enabled
            ),
        )
        fallback_toolsets = []

    await service.usage_recorder.release(state.reservation)
    state.reservation = await service.usage_recorder.reserve(
        organization_id=conversation.organization_id,
        user_id=user_id,
        runtime_profile=fallback_snapshot,
    )
    state.profile = fallback_snapshot
    fallback_options = HarnessOptions(
        model_name=fallback_runtime.model_name_for_harness,
        toolsets=fallback_toolsets,
        capabilities=fallback_capabilities,
        model_settings=fallback_model_settings,
        usage_limits=usage_limits,
        output_type=service._resolve_output_type(agent, conversation),
        should_stop=service._make_stop_checker(agent_run_id),
        extra={
            "runtime_profile": fallback_snapshot,
            "runtime_credentials": fallback_credentials,
        },
    )
    logger.warning(
        "agent.agent_runner_service.agent_host_fallback_started",
        agent_run_id=agent_run_id,
        primary_profile_id=primary_profile_id,
        fallback_profile_id=fallback_profile_id,
        fallback_harness=fallback_runtime.harness_kind.value,
    )
    yield AgentEvent(
        type=AgentEventType.STATUS,
        data={
            "status": "agent_host.fallback.started",
            "fallback_profile_id": fallback_profile_id,
        },
        agent_run_id=agent_run_id,
    )
    fallback_harness = service.harness_registry.get(fallback_runtime.harness_kind)
    fallback_agent = service._agent_with_resolved_runtime_metadata(
        agent,
        resolved_runtime=fallback_runtime,
    )
    async for fallback_event in fallback_harness.run(
        agent=fallback_agent,
        conversation=conversation,
        messages=messages,
        ctx=fallback_ctx,
        options=fallback_options,
        agent_run_id=agent_run_id,
    ):
        yield fallback_event
