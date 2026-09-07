"""Accounting distinguishes supported token usage from extra billing categories."""

from collections.abc import Mapping

import pytest
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
    priceable_text_request,
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
    assert priceable_text_request(messages, parameters, {})


@pytest.mark.parametrize(
    "content",
    [
        BinaryContent(data=b"image", media_type="image/png"),
        ImageUrl("https://example.com/image.png"),
        AudioUrl("https://example.com/audio.mp3"),
        VideoUrl("https://example.com/video.mp4"),
        DocumentUrl("https://example.com/document.pdf"),
        UploadedFile(file_id="file-example", provider_name="openai"),
    ],
)
def test_multimedia_input_is_not_a_text_request(content: UserContent) -> None:
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart([content])])]
    assert not priceable_text_request(messages, ModelRequestParameters(), {})


def test_media_inside_tool_returns_is_not_treated_as_plain_json() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "view", [BinaryContent(data=b"image", media_type="image/png")]
                )
            ]
        )
    ]
    assert not priceable_text_request(messages, ModelRequestParameters(), {})


def test_native_tools_are_rejected_even_without_results_yet() -> None:
    assert not priceable_text_request(
        [], ModelRequestParameters(native_tools=[WebSearchTool()]), {}
    )


def test_native_tool_history_is_rejected_without_current_native_tools() -> None:
    messages: list[ModelMessage] = [
        ModelResponse(
            parts=[
                NativeToolCallPart("web_search", {"query": "example"}),
                NativeToolReturnPart("web_search", {"result": "found"}),
            ]
        )
    ]
    assert not priceable_text_request(messages, ModelRequestParameters(), {})


def test_reasoning_signatures_do_not_change_the_billing_category() -> None:
    messages: list[ModelMessage] = [
        ModelResponse(
            parts=[ThinkingPart("", signature="opaque", provider_name="openai")]
        )
    ]
    assert priceable_text_request(messages, ModelRequestParameters(), {})


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
    assert not priceable_text_request([], ModelRequestParameters(), settings)


def test_cache_markers_cannot_sneak_one_hour_pricing_into_text_accounting() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(["text", CachePoint(ttl="1h")])])
    ]
    assert not priceable_text_request(messages, ModelRequestParameters(), {})


def test_image_output_is_not_a_text_request() -> None:
    assert not priceable_text_request(
        [], ModelRequestParameters(allow_image_output=True), {}
    )
