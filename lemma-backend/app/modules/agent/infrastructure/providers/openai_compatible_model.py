"""The chat model behind every OpenAI-compatible runtime profile.

Two departures from pydantic-ai's ``OpenAIChatModel``, both about what reaches
the wire, neither about any one vendor:

- ``stream_options`` stays to the spec (``_get_stream_options``);
- an assistant message is rendered the same way on every request it is part of
  (``_map_messages``), so a provider's prompt cache keeps the history it saw.
"""

from __future__ import annotations

from collections.abc import Sequence

from openai.types import chat
from openai.types.chat import ChatCompletionStreamOptionsParam
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings

#: The assistant fields pydantic-ai reads a model's reasoning from, after the
#: profile's own (``_process_thinking``).
_THINKING_FIELDS = ("reasoning", "reasoning_content")


def thinking_field_on_every_call(
    mapped: list[chat.ChatCompletionMessageParam], profile: ModelProfile
) -> list[chat.ChatCompletionMessageParam]:
    """Give every tool-calling assistant message the thinking field, empty when
    it has no thoughts, once the history sends that field at all.

    pydantic-ai's ``'auto'`` mode backfills the empty field only on the calls
    after the last user message: some reasoning endpoints refuse a current-turn
    call without it. The next turn moves that boundary, and the same messages
    go out without the field -- every turn rewrites the history the provider
    cached, from the first such call on. Backfilling every call renders each
    message once and for all; a reasoning endpoint asks for the field on every
    turn of a request with tools, so the earlier ones are within its contract.

    Only the one field the history already sends, read the same way pydantic-ai
    reads it: a server that rejects unknown fields has accepted that one.
    """
    if profile.get("openai_chat_send_back_thinking_parts", "auto") != "auto":
        return mapped
    custom = profile.get("openai_chat_thinking_field", None)
    names = tuple(name for name in (custom, *_THINKING_FIELDS) if name)
    assistants = [message for message in mapped if message["role"] == "assistant"]
    sent = {name for message in assistants for name in names if name in message}
    if len(sent) != 1:
        return mapped
    (field,) = sent
    for message in assistants:
        if message.get("tool_calls") and field not in message:
            message[field] = ""  # type: ignore[literal-required]
    return mapped


class OpenAICompatibleChatModel(OpenAIChatModel):
    """An OpenAI-compatible chat model whose requests extend each other.

    ``stream_options``: pydantic-ai's ``openai_continuous_usage_stats`` does two
    unrelated things. It puts ``continuous_usage_stats`` in the request's
    ``stream_options``, and it switches the streaming handler from summing each
    chunk's usage to replacing with it. Only the second is wanted here, and the
    two have to be separated because they are not equally portable.

    The replace half is what keeps a provider that reports a running cumulative
    total on every chunk from being re-added on top of itself; without it a
    single turn bills a multiple of what it used. The request half is a vLLM
    extension rather than OpenAI's -- the openai SDK's own stream-options type
    does not carry the field -- so a strict OpenAI-compatible endpoint rejects
    the whole request for an unknown field. Models behind one provider disagree
    about accepting it, and this profile fronts whatever endpoint an operator
    points it at, so sending it makes streaming fail for some of them.

    Dropping it costs nothing: a provider that sends cumulative usage does so on
    its own, and the field cannot turn that off. So the setting stays on the
    profile for the accumulation it selects, and never reaches the wire.
    """

    def _get_stream_options(
        self, model_settings: OpenAIChatModelSettings
    ) -> ChatCompletionStreamOptionsParam:
        return {"include_usage": True}

    async def _map_messages(
        self,
        messages: Sequence[ModelMessage],
        model_request_parameters: ModelRequestParameters,
        *,
        model_settings: ModelSettings | None = None,
    ) -> list[chat.ChatCompletionMessageParam]:
        mapped = await super()._map_messages(
            messages, model_request_parameters, model_settings=model_settings
        )
        return thinking_field_on_every_call(mapped, self.profile)


__all__ = ["OpenAICompatibleChatModel", "thinking_field_on_every_call"]
