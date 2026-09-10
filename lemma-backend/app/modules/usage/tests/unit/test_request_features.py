"""Accounting distinguishes supported token usage from extra billing categories."""

from collections.abc import Mapping, Sequence

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    CachePoint,
    DocumentUrl,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UploadedFile,
    UserContent,
    UserPromptPart,
    VideoUrl,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.tools import ToolDefinition

from app.modules.usage.infrastructure.request_features import (
    priceable_request,
)


def priceable(
    messages: Sequence[ModelMessage],
    parameters: ModelRequestParameters,
    settings: Mapping[str, object] | None = None,
    *,
    images: bool = True,
) -> bool:
    """Most cases do not turn on images; the card that prices them is the norm."""
    return priceable_request(
        messages, parameters, settings or {}, prices_images_as_text=images
    )


def test_plain_text_and_json_function_tools_are_supported() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[UserPromptPart(["hello", TextContent("world"), CachePoint()])]
        ),
        ModelResponse(parts=[ToolCallPart("lookup", {"name": "example"})]),
        ModelRequest(
            parts=[ToolReturnPart("lookup", {"items": [1, None, {"value": True}]})]
        ),
        ModelResponse(parts=[ThinkingPart("visible reasoning"), TextPart("result")]),
    ]
    parameters = ModelRequestParameters(
        function_tools=[
            ToolDefinition(name="lookup", parameters_json_schema={"type": "object"})
        ]
    )
    assert priceable(messages, parameters, {})


@pytest.mark.parametrize(
    "content",
    [
        BinaryContent(data=b"audio", media_type="audio/mpeg"),
        BinaryContent(data=b"pdf", media_type="application/pdf"),
        AudioUrl("https://example.com/audio.mp3"),
        VideoUrl("https://example.com/video.mp4"),
        DocumentUrl("https://example.com/document.pdf"),
        UploadedFile(file_id="file-example", provider_name="openai"),
    ],
)
def test_media_the_provider_bills_in_its_own_category_is_not_priceable(
    content: UserContent,
) -> None:
    """Audio, video and documents each have rates and counts of their own.

    `RequestUsage` reports one input total, so the split those rates need is
    not in the receipt however generous the card is about images.
    """
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart([content])])]
    assert not priceable(messages, ModelRequestParameters(), {})


@pytest.mark.parametrize(
    "image",
    [
        BinaryContent(data=b"image", media_type="image/png"),
        ImageUrl("https://example.com/image.png"),
    ],
)
def test_an_image_is_input_tokens_when_the_card_prices_it_that_way(
    image: UserContent,
) -> None:
    """This is what lets an agent look at something.

    A provider counts image input into `input_tokens` and bills it at
    `input_mtok`, so a card with no image rate of its own prices the request
    exactly. Calling every image unpriceable instead stopped a run at the
    boundary after it looked at one, and refused a text-only model's vision
    delegate outright -- every one of whose requests carries an image.
    """
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart([image])])]
    assert priceable(messages, ModelRequestParameters(), {}, images=True)
    assert not priceable(messages, ModelRequestParameters(), {}, images=False)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (BinaryContent(data=b"image", media_type="image/png"), True),
        (BinaryContent(data=b"pdf", media_type="application/pdf"), False),
    ],
)
def test_a_tool_returning_an_image_is_priced_like_one_in_the_prompt(
    content: UserContent, expected: bool
) -> None:
    """`view_image` hands its image back through a tool return."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[ToolReturnPart("view", [content])])
    ]
    assert priceable(messages, ModelRequestParameters(), {}) is expected


def test_native_tools_are_rejected_even_without_results_yet() -> None:
    assert not priceable([], ModelRequestParameters(native_tools=[WebSearchTool()]), {})


def test_native_tool_history_is_rejected_without_current_native_tools() -> None:
    messages: list[ModelMessage] = [
        ModelResponse(
            parts=[
                NativeToolCallPart("web_search", {"query": "example"}),
                NativeToolReturnPart("web_search", {"result": "found"}),
            ]
        )
    ]
    assert not priceable(messages, ModelRequestParameters(), {})


def test_reasoning_signatures_do_not_change_the_billing_category() -> None:
    messages: list[ModelMessage] = [
        ModelResponse(
            parts=[ThinkingPart("", signature="opaque", provider_name="openai")]
        )
    ]
    assert priceable(messages, ModelRequestParameters(), {})


@pytest.mark.parametrize(
    "settings",
    [
        {"openai_previous_response_id": "auto"},
        {"openai_conversation_id": "conversation"},
        {"google_cached_content": "cache"},
        {"openai_modalities": ["audio"]},
    ],
)
def test_server_continuations_and_audio_settings_are_not_text_requests(
    settings: Mapping[str, object],
) -> None:
    assert not priceable([], ModelRequestParameters(), settings)


def test_cache_markers_cannot_sneak_one_hour_pricing_into_text_accounting() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(["text", CachePoint(ttl="1h")])])
    ]
    assert not priceable(messages, ModelRequestParameters(), {})


def test_image_output_is_not_a_text_request() -> None:
    assert not priceable([], ModelRequestParameters(allow_image_output=True), {})


def test_a_tool_returning_a_model_is_text_because_that_is_what_is_sent() -> None:
    """Most tools return a model or a dataclass, not a JSON primitive.

    pydantic-ai serializes it with `model_response_str`, so the provider is
    billed for text. Treating it as unpriceable refused the continuation after
    the first tool call of any run under a monetary limit.
    """

    class ExecCommandResult(BaseModel):
        stdout: str
        exit_code: int

    messages: list[ModelMessage] = [
        ModelResponse(parts=[ToolCallPart("exec", {"command": "ls"})]),
        ModelRequest(
            parts=[
                ToolReturnPart("exec", ExecCommandResult(stdout="a.txt", exit_code=0))
            ]
        ),
    ]
    assert priceable(messages, ModelRequestParameters(), {})
