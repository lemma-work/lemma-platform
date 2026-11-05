"""The provider port for background model calls, on pydantic-ai.

Builds the model a resolved runtime names and asks it one ``BackgroundCall``.
The vendor and framework types stop here: the call sites (titles, the memory
pass, the vision delegate, compaction) describe what they ask in
``domain/background`` and read a ``BackgroundAnswer`` back.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai import BinaryContent, UsageLimits, capture_run_messages
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserContent
from pydantic_ai.models import Model

from app.modules.agent.domain.background import (
    BackgroundAnswer,
    BackgroundCall,
    BackgroundLimitReached,
    BackgroundLimits,
    PromptPart,
    ToolReturnRecord,
)
from app.modules.agent.domain.tools import ImagePart
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.infrastructure.harness.lemma.toolset_adapter import (
    function_toolset,
)
from app.modules.agent.services.tool_runtime import ToolRuntime
from app.modules.agent.infrastructure.providers import (
    model_factory as runtime_model_factory,
)

#: A strict endpoint refuses a request field it does not know with one of these.
_REFUSED_FIELD = (400, 422)


class PydanticAIModelProvider:
    """``domain/ports.ModelProvider`` over pydantic-ai models.

    ``agent_factory`` and ``model_for`` replace the agent and the model build,
    for a test that fakes the model boundary and nothing else.
    """

    def __init__(
        self,
        *,
        agent_factory: Callable[..., PydanticAIAgent[Any, Any]] | None = None,
        model_for: Callable[..., object] | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._model_for = model_for

    def build(
        self, resolved: ResolvedAgentRuntime, *, fallback_model_name: str | None = None
    ) -> object | None:
        """The model ``resolved`` names, or ``None`` when it cannot be built.

        ``fallback_model_name`` makes the build mandatory: a runtime with no
        selected model gets that name, and a protocol nothing can build raises.
        """
        if self._model_for is not None:
            return self._model_for(
                resolved=resolved, fallback_model_name=fallback_model_name
            )
        # Read through the module, so a test that replaces the factory's
        # functions reaches this call.
        if fallback_model_name is not None:
            return runtime_model_factory.require_pydantic_ai_model_from_runtime_profile(
                runtime_profile=resolved.runtime_snapshot().as_payload(),
                runtime_credentials=resolved.credentials or {},
                fallback_model_name=fallback_model_name,
            )
        return runtime_model_factory.pydantic_ai_model_from_runtime_profile(
            runtime_profile=resolved.runtime_snapshot().as_payload(),
            runtime_credentials=resolved.credentials,
        )

    async def run(self, model: object, call: BackgroundCall) -> BackgroundAnswer:
        """Ask ``model`` the call, once more plainly if a field is refused."""
        agent: PydanticAIAgent[Any, Any] = (self._agent_factory or PydanticAIAgent)(
            cast(Model | str, model),
            instructions=call.instructions,
            system_prompt=call.system_prompt or (),
            deps_type=ToolContext if call.tools else type(None),
            toolsets=(
                [function_toolset(call.tools, ToolRuntime(call.tools))]
                if call.tools
                else []
            ),
        )
        limits = _usage_limits(cast(Model | str, model), call.limits)
        prompt = _prompt(call.prompt)
        with capture_run_messages() as sent:
            try:
                result = await self._ask(agent, call, prompt, limits)
            except UsageLimitExceeded as exceeded:
                # What ran before the limit stands: a call that writes may have
                # kept something, and says so by its returns.
                raise BackgroundLimitReached(
                    str(exceeded), tool_returns=_tool_returns(sent)
                ) from exceeded
        return BackgroundAnswer(
            output=str(result.output if result.output is not None else ""),
            tool_returns=_tool_returns(result.all_messages()),
        )

    @staticmethod
    async def _ask(
        agent: PydanticAIAgent[Any, Any],
        call: BackgroundCall,
        prompt: str | list[UserContent],
        limits: UsageLimits,
    ) -> AgentRunResult[Any]:
        try:
            return await agent.run(
                prompt,
                deps=call.deps,
                usage_limits=limits,
                model_settings=runtime_model_factory.provider_model_settings(
                    runtime_model_factory.with_affinity(
                        call.model_settings, call.affinity_key
                    )
                ),
            )
        except ModelHTTPError as error:
            # Asked once more without the reasoning field -- only when it was
            # sent. A refusal of anything else, or an auth or rate-limit
            # answer, is the caller's to handle.
            if (
                call.plain_model_settings == call.model_settings
                or error.status_code not in _REFUSED_FIELD
            ):
                raise
            if call.on_plain_retry is not None:
                call.on_plain_retry(error.status_code)
            return await agent.run(
                prompt,
                deps=call.deps,
                usage_limits=limits,
                model_settings=runtime_model_factory.provider_model_settings(
                    runtime_model_factory.with_affinity(
                        call.plain_model_settings, call.affinity_key
                    )
                ),
            )


def _tool_returns(messages: Sequence[ModelMessage]) -> list[ToolReturnRecord]:
    return [
        ToolReturnRecord(tool_name=part.tool_name, content=part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _usage_limits(model: Model | str, limits: BackgroundLimits) -> UsageLimits:
    wanted = UsageLimits(
        request_limit=limits.request_limit,
        input_tokens_limit=limits.input_tokens_limit,
        output_tokens_limit=limits.output_tokens_limit,
        total_tokens_limit=limits.total_tokens_limit,
        count_tokens_before_request=limits.count_tokens_before_request,
    )
    if isinstance(model, str):
        return wanted
    return runtime_model_factory.usage_limits_for(model, wanted)


def _prompt(prompt: str | Sequence[PromptPart]) -> str | list[UserContent]:
    if isinstance(prompt, str):
        return prompt
    return [
        BinaryContent(data=part.data, media_type=part.media_type)
        if isinstance(part, ImagePart)
        else part
        for part in prompt
    ]


__all__ = ["PydanticAIModelProvider"]
