"""The model that speaks on a cascade call.

Any Lemma model, by public name, through the agent module's runtime: the same
profiles, credentials and metering a run uses. It has no tools and writes for
the ear -- short sentences, no lists, no markup -- because everything it writes
is read aloud as it arrives.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModelSettings

from app.modules.agent.contracts.model_runtime import resolve_system_runtime
from app.modules.calls.domain.party import CallParty
from app.modules.usage.contracts.execution import UsageExecutionContext
from app.modules.usage.contracts.metering import meter_model, metering_execution

#: A spoken reply is a few sentences. The bound is loose because a model that
#: reasons spends its budget before saying a word.
_MAX_REPLY_TOKENS = 1200
_REQUESTS_PER_REPLY = 2

type ReasoningEffort = Literal["none", "minimal", "low", "medium", "high"]
type Speaker = Literal["caller", "voice", "note"]


@dataclass(frozen=True, slots=True)
class Line:
    speaker: Speaker
    text: str


class LemmaTalker:
    def __init__(
        self,
        *,
        model: Model,
        instructions: str,
        party: CallParty,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> None:
        self._agent = Agent(model, instructions=instructions, output_type=str)
        self._party = party
        # `reasoning_effort` is a Chat Completions field; a model spoken to
        # over another wire format ignores it.
        self._settings = OpenAIChatModelSettings(max_tokens=_MAX_REPLY_TOKENS)
        if reasoning_effort is not None:
            self._settings["openai_reasoning_effort"] = reasoning_effort

    @classmethod
    async def resolve(
        cls,
        *,
        model_name: str | None,
        instructions: str,
        party: CallParty,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LemmaTalker:
        runtime = await resolve_system_runtime(
            usage_limits=UsageLimits(request_limit=_REQUESTS_PER_REPLY),
            user_id=party.user_id,
            organization_id=party.organization_id,
            pod_id=party.pod_id,
            model_name=model_name,
        )
        model = meter_model(runtime.model, runtime.runtime_profile, source="call_voice")
        return cls(
            model=model,
            instructions=instructions,
            party=party,
            reasoning_effort=reasoning_effort,
        )

    async def reply(self, history: Sequence[Line], prompt: str) -> AsyncIterator[str]:
        """The reply to ``prompt``, as text deltas while it is written."""
        context = UsageExecutionContext(
            user_id=self._party.user_id,
            organization_id=self._party.organization_id,
            pod_id=self._party.pod_id,
            conversation_id=self._party.conversation_id,
            source_type="call",
            source_id=str(self._party.conversation_id),
        )
        async with metering_execution(context):
            async with self._agent.run_stream(
                prompt,
                message_history=_messages(history),
                model_settings=self._settings,
                usage_limits=UsageLimits(request_limit=_REQUESTS_PER_REPLY),
            ) as streamed:
                async for delta in streamed.stream_text(delta=True):
                    yield delta


def _messages(history: Sequence[Line]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for line in history:
        if line.speaker == "voice":
            messages.append(ModelResponse(parts=[TextPart(content=line.text)]))
        else:
            text = line.text if line.speaker == "caller" else f"[note] {line.text}"
            messages.append(ModelRequest(parts=[UserPromptPart(content=text)]))
    return messages
