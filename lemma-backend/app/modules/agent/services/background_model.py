"""A model call outside a turn: resolve, build, meter, run -- once, for all of them.

Titles, the memory pass, the vision delegate and history compaction each ask a
model something that is not a turn. Each used to resolve its runtime, build its
model and meter the call its own way. Now each says what it asks
(``domain/background.BackgroundCall``) and for which ``RuntimePurpose``, and
this does the rest:

* **resolve** through the one chain (``runtime_resolution.resolve_runtime``);
* **build** through the provider port (``domain/ports.ModelProvider``), so no
  call site touches a vendor SDK or the agent framework;
* **meter** under the purpose's source tag, so the call's spend is recorded as
  what it was and against whom the caller's usage context says;
* **run** it, asked once more plainly when a strict endpoint refuses a field.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from functools import lru_cache

from app.core.log.log import get_logger

from app.modules.agent.domain.background import BackgroundAnswer, BackgroundCall
from app.modules.agent.domain.ports import ModelProvider
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.domain.runtime_choice import RuntimePurpose
from app.modules.agent.services.runtime_resolution import (
    ProfileResolver,
    RuntimeRequest,
    WorkspaceRuntimeResolver,
    resolve_runtime,
)
from app.modules.usage.contracts.execution import UsageExecutionContext
from app.modules.usage.contracts.metering import metering_execution

logger = get_logger(__name__)

#: The call to make, once the runtime it runs on is known.
type CallFor = Callable[[ResolvedAgentRuntime], BackgroundCall]

#: What each purpose's spend is recorded under.
USAGE_SOURCES: dict[RuntimePurpose, str] = {
    RuntimePurpose.TITLE: "conversation_title",
    RuntimePurpose.MEMORY: "agent_memory",
    RuntimePurpose.VISION: "vision",
}


@lru_cache(maxsize=1)
def default_model_provider() -> ModelProvider:
    """The pydantic-ai adapter; imported on first use, not at startup."""
    from app.modules.agent.infrastructure.providers.background import (
        PydanticAIModelProvider,
    )

    return PydanticAIModelProvider()


class BackgroundModel:
    """The resolve -> build -> meter -> run sequence, with its provider injectable."""

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self._provider = provider

    @property
    def provider(self) -> ModelProvider:
        return self._provider or default_model_provider()

    async def resolve(
        self,
        purpose: RuntimePurpose,
        request: RuntimeRequest,
        *,
        profiles: Callable[[], ProfileResolver] | None = None,
        workspace_runtime: WorkspaceRuntimeResolver | None = None,
    ) -> ResolvedAgentRuntime | None:
        return await resolve_runtime(
            request,
            purpose=purpose,
            profiles=profiles,
            workspace_runtime=workspace_runtime,
        )

    def build(
        self, resolved: ResolvedAgentRuntime, *, fallback_model_name: str | None = None
    ) -> object | None:
        return self.provider.build(resolved, fallback_model_name=fallback_model_name)

    async def model_or[T](
        self, purpose: RuntimePurpose, request: RuntimeRequest, *, fallback: T
    ) -> object | T:
        """The purpose's configured model, or ``fallback`` (the run's own).

        For compaction and the memory pass: a model a deployment may move the
        job onto, by name, on the system provider. Unset, unreachable or
        unbuildable, the job stays on the run's model.
        """
        if not request.model_name:
            return fallback
        resolved = await self.resolve(purpose, request)
        if resolved is None:
            return fallback
        try:
            model = self.build(resolved)
        except Exception:
            # Warning, not debug: falling back runs the job on the run's own,
            # expensive, model.
            logger.warning(
                "agent.system_model.resolution_failed.observed",
                model_name=request.model_name,
                purpose=purpose.value,
                exc_info=True,
            )
            return fallback
        return model or fallback

    async def ask(
        self,
        purpose: RuntimePurpose,
        model: object,
        call: BackgroundCall,
        *,
        usage: UsageExecutionContext,
        timeout_seconds: float | None = None,
    ) -> BackgroundAnswer:
        """Run ``call`` on ``model``, metered as ``purpose`` against ``usage``.

        ``timeout_seconds`` bounds the call inside the metering, so a call cut
        short is still recorded.
        """
        source = USAGE_SOURCES.get(purpose, usage.source_type)
        if usage.pod_id is not None and call.affinity_key is None:
            call = replace(call, affinity_key=f"{usage.pod_id}:{purpose.value}")
        async with (
            metering_execution(replace(usage, source_type=source)),
            asyncio.timeout(timeout_seconds),
        ):
            return await self.provider.run(model, call)

    async def run(
        self,
        purpose: RuntimePurpose,
        request: RuntimeRequest,
        call_for: CallFor,
        *,
        usage: UsageExecutionContext,
        profiles: Callable[[], ProfileResolver] | None = None,
        workspace_runtime: WorkspaceRuntimeResolver | None = None,
    ) -> BackgroundAnswer:
        """Resolve, build, meter and run, in that order.

        ``call_for`` builds the call from the resolved runtime: what it may be
        asked (its reasoning settings) depends on what its model declares.
        """
        resolved = await self.resolve(
            purpose, request, profiles=profiles, workspace_runtime=workspace_runtime
        )
        assert resolved is not None  # these purposes resolve or raise
        model = self.build(
            resolved, fallback_model_name=resolved.model_name_for_harness
        )
        return await self.ask(purpose, model, call_for(resolved), usage=usage)


__all__ = ["USAGE_SOURCES", "BackgroundModel", "CallFor", "default_model_provider"]
