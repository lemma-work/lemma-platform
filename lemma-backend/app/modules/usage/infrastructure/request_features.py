"""Recognize request shapes whose billable categories are recorded."""

from collections.abc import Mapping, Sequence

from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelRequestPart,
    ModelResponsePart,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters


def priceable_text_request(
    messages: Sequence[ModelMessage],
    parameters: ModelRequestParameters,
    settings: Mapping[str, object],
) -> bool:
    if parameters.native_tools or parameters.allow_image_output:
        return False
    # A server-side continuation can restore content or work absent locally.
    if any(
        settings.get(key)
        for key in (
            "openai_previous_response_id",
            "openai_conversation_id",
            "google_cached_content",
            "openai_audio",
            "audio",
            "modalities",
            "openai_modalities",
        )
    ):
        return False
    return all(_text_part(part) for message in messages for part in message.parts)


def _text_part(part: ModelRequestPart | ModelResponsePart) -> bool:
    if isinstance(part, SystemPromptPart | TextPart | RetryPromptPart):
        return True
    if isinstance(part, UserPromptPart):
        return isinstance(part.content, str) or all(
            isinstance(content, str | TextContent)
            or (isinstance(content, CachePoint) and content.ttl == "5m")
            for content in part.content
        )
    if isinstance(part, ToolAvailabilityDeltaPart):
        return True
    if isinstance(part, ToolCallPart):
        return _json_content(part.args)
    if isinstance(part, ToolReturnPart):
        return _json_content(part.content)
    if isinstance(part, ThinkingPart):
        # Signed or redacted reasoning still uses ordinary input/output tokens.
        return True
    # Native tool calls/results, compaction and file parts need distinct prices
    # even when they only occur in replayed history.
    return False


def _json_content(value: object) -> bool:
    if value is None or isinstance(value, str | bool | int | float):
        return True
    if isinstance(value, list | tuple):
        return all(_json_content(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _json_content(item) for key, item in value.items()
        )
    return False
