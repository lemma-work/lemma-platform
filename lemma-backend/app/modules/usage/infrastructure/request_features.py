"""Recognize request shapes whose billable categories are recorded."""

from collections.abc import Mapping, Sequence

from pydantic_ai.messages import (
    BinaryContent,
    CachePoint,
    ImageUrl,
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

_SERVER_SIDE_SETTINGS = (
    "openai_previous_response_id",
    "openai_conversation_id",
    "google_cached_content",
    "openai_audio",
    "audio",
    "modalities",
    "openai_modalities",
)


def priceable_request(
    messages: Sequence[ModelMessage],
    parameters: ModelRequestParameters,
    settings: Mapping[str, object],
    *,
    prices_images_as_text: bool,
) -> bool:
    """Whether every part of this request is billed at ordinary token rates.

    `prices_images_as_text` is the rate card's answer about images, which is
    the only content category whose price depends on the card rather than on
    the request -- see `RateCard.prices_images_as_text`.
    """
    if parameters.native_tools or parameters.allow_image_output:
        return False
    # A server-side continuation can restore content or work absent locally.
    if any(settings.get(key) for key in _SERVER_SIDE_SETTINGS):
        return False
    return all(
        _priceable_part(part, prices_images_as_text)
        for message in messages
        for part in message.parts
    )


def _priceable_part(
    part: ModelRequestPart | ModelResponsePart, images_are_text: bool
) -> bool:
    if isinstance(part, SystemPromptPart | TextPart | RetryPromptPart):
        return True
    if isinstance(part, UserPromptPart):
        return isinstance(part.content, str) or all(
            _priceable_content(content, images_are_text) for content in part.content
        )
    if isinstance(part, ToolAvailabilityDeltaPart):
        return True
    if isinstance(part, ToolCallPart):
        return _json_content(part.args)
    if isinstance(part, ToolReturnPart):
        # `files` is pydantic-ai's own split: everything that is not one of
        # `BinaryContent`/`ImageUrl`/`AudioUrl`/`DocumentUrl`/`VideoUrl` reaches
        # the provider through `model_response_str`, as ordinary text, priced by
        # `input_mtok` like any other text. Only the files need a price this
        # rate card may not carry.
        #
        # Testing the content for JSON primitives instead treated everything
        # else as unpriceable -- and a tool returning a dataclass or a pydantic
        # model, which is most of them (`ExecCommandResult`, for one), is
        # neither JSON nor a file. So an agent answered the opening prompt and
        # was refused on the continuation carrying its first tool result, after
        # the tokens for both had been spent, and the refusal was reported as a
        # usage limit the account had not reached.
        return all(_priceable_content(file, images_are_text) for file in part.files)
    if isinstance(part, ThinkingPart):
        # Signed or redacted reasoning still uses ordinary input/output tokens.
        return True
    # Native tool calls/results, compaction and file parts need distinct prices
    # even when they only occur in replayed history.
    return False


def _priceable_content(content: object, images_are_text: bool) -> bool:
    if isinstance(content, str | TextContent):
        return True
    if isinstance(content, CachePoint):
        return content.ttl == "5m"
    return images_are_text and _is_image(content)


def _is_image(content: object) -> bool:
    """Image input the provider counts into its ordinary input tokens.

    A `BinaryContent` carrying anything else -- audio, video, a PDF -- is
    metered by the provider in a category of its own, and pydantic-ai's
    normalized receipt does not report the split those categories need.
    """
    if isinstance(content, BinaryContent):
        return content.is_image
    return isinstance(content, ImageUrl)


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
