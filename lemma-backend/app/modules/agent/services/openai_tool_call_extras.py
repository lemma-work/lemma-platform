"""Round-trip the opaque ``extra_content`` an endpoint attaches to a tool call.

An OpenAI-compatible endpoint may put an ``extra_content`` object on a tool call
it returns and require that object back, unchanged, on the same call when the
conversation is sent again. Gemini 3's endpoint does exactly that: each turn's
first function call carries ``extra_content.google.thought_signature``, and the
next request in the tool loop is refused without it::

    400 Function call is missing a thought_signature in functionCall parts.

pydantic-ai's chat model reads only the spec fields of a tool call, so the
object was dropped on the way in and never sent back: every Gemini 3 run with a
tool call failed on its second model request.

Nothing here reads inside the object. It is carried on the ``ToolCallPart``'s
``provider_details`` together with the base URL it came from, and echoed only
to that same endpoint -- an agent's model can change mid-conversation, and a
different endpoint has no reason to accept a field it never sent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from openai.types import chat
from openai.types.chat import ChatCompletionMessageFunctionToolCallParam
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from pydantic_ai.messages import (
    ModelResponse,
    ModelResponsePart,
    ModelResponseStreamEvent,
    PartStartEvent,
    ToolCallPart,
)
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIStreamedResponse

#: The tool-call field carried back, and the `provider_details` keys it rides
#: under. `origin` is the base URL that sent it.
EXTRA_CONTENT_FIELD = "extra_content"
ORIGIN_KEY = "origin"


def tool_call_extras(
    extra: Mapping[str, Any] | None, origin: str
) -> dict[str, Any] | None:
    """The `provider_details` for a tool call whose wire form had ``extra``."""
    content = (extra or {}).get(EXTRA_CONTENT_FIELD)
    if not isinstance(content, Mapping) or not content:
        return None
    return {EXTRA_CONTENT_FIELD: dict(content), ORIGIN_KEY: origin}


def extra_content_for(part: ToolCallPart, origin: str) -> dict[str, Any] | None:
    """What to send back on this call to ``origin``, if anything."""
    details = part.provider_details or {}
    content = details.get(EXTRA_CONTENT_FIELD)
    if details.get(ORIGIN_KEY) != origin or not isinstance(content, Mapping):
        return None
    return dict(content)


class _ToolCallExtrasStreamedResponse(OpenAIStreamedResponse):
    def _map_tool_call_delta(
        self, choice: ChunkChoice
    ) -> Iterable[ModelResponseStreamEvent]:
        for dtc in choice.delta.tool_calls or []:
            details = tool_call_extras(dtc.model_extra, self._provider_url)
            maybe_event = self._parts_manager.handle_tool_call_delta(
                vendor_part_id=dtc.index,
                tool_name=dtc.function.name if dtc.function is not None else None,
                args=dtc.function.arguments if dtc.function is not None else None,
                tool_call_id=dtc.id,
                provider_name=self._provider_name if details else None,
                provider_details=details,
            )
            if maybe_event is not None:
                if isinstance(maybe_event, PartStartEvent):
                    self._advance_part_boundary("tool")
                yield maybe_event


class ToolCallExtrasChatModel(OpenAIChatModel):
    """An OpenAI chat model that keeps each tool call's ``extra_content``."""

    @property
    def _streamed_response_cls(self) -> type[OpenAIStreamedResponse]:
        return _ToolCallExtrasStreamedResponse

    def _process_response(self, response: chat.ChatCompletion | str) -> ModelResponse:
        extras = _extras_by_call_id(response, self.base_url)
        processed = super()._process_response(response)
        if not extras:
            return processed
        parts: list[ModelResponsePart] = [
            replace(
                part,
                provider_name=self.system,
                provider_details=extras[part.tool_call_id],
            )
            if isinstance(part, ToolCallPart) and part.tool_call_id in extras
            else part
            for part in processed.parts
        ]
        return replace(processed, parts=parts)

    def _map_tool_call(  # type: ignore[override]  # pyright: ignore[reportIncompatibleMethodOverride]
        self, t: ToolCallPart
    ) -> ChatCompletionMessageFunctionToolCallParam:
        mapped = OpenAIChatModel._map_tool_call(t)
        content = extra_content_for(t, self.base_url)
        if content is not None:
            # Not a field of the openai SDK's param type; the SDK sends keys it
            # has no annotation for as they are.
            mapped[EXTRA_CONTENT_FIELD] = content  # type: ignore[typeddict-unknown-key]
        return mapped


def _extras_by_call_id(
    response: chat.ChatCompletion | str, origin: str
) -> dict[str, dict[str, Any]]:
    if not isinstance(response, chat.ChatCompletion) or not response.choices:
        return {}
    calls = response.choices[0].message.tool_calls or []
    found: dict[str, dict[str, Any]] = {}
    for call in calls:
        details = tool_call_extras(call.model_extra, origin)
        if details is not None and call.id:
            found[call.id] = details
    return found
