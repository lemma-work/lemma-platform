"""Turn a tool result into MCP content, images included.

Both MCP bridges rendered every result as `TextContent`, so a tool that returns
image content — `view_image`, `pod_view_document_pages` — reached a remote
harness as JSON describing an image it could not see. Codex and Claude Code are
both vision-capable, so the images were being dropped for no reason.

Shared by the conversation and pod bridges so the two cannot drift: discovery
and dispatch already go through one resolver, and this is the third place they
have to agree.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent
from app.modules.agent.domain.tools import WithImages
from app.modules.agent.infrastructure.tools.tool_errors import (
    format_tool_error,
    result_is_failure,
)

from app.modules.agent.domain.value_objects import to_json_value

# Matches the per-image ceiling the workspace tool enforces. An MCP client that
# refuses an oversized image would lose the text result along with it, so the
# image is dropped and the structured payload still gets through.
MAX_MCP_IMAGE_BYTES = 5 * 1024 * 1024

# The same ceiling again, applied to a whole result rather than one image.
#
# `pod_view_document_pages` returns one image per page and takes an unbounded
# page range, so "render pages 1 to 30" produced thirty images that were each
# individually under the per-image limit. Agent Host reads a tool result through
# a stdio bridge that caps a response at 8 MiB (`MAX_MCP_RESPONSE_BYTES` in
# `mcp_bridge.rs`), and base64 adds a third on top — so a request like that blew
# the cap and the agent got an opaque size error instead of the pages, losing
# the structured payload with them. Budgeting here keeps the encoded response
# inside what the bridge accepts: 5 MiB of image is about 6.7 MiB encoded, which
# leaves room for the JSON envelope and the text block beside it.
MAX_MCP_IMAGE_BYTES_TOTAL = 5 * 1024 * 1024


def _binary_parts(result: object) -> list[tuple[bytes, str]]:
    """The images a tool returned beside its result, each within the limit."""
    if not isinstance(result, WithImages):
        return []
    return [
        (image.data, image.media_type)
        for image in result.images
        if image.media_type.startswith("image/")
        and len(image.data) <= MAX_MCP_IMAGE_BYTES
    ]


def image_contents(result: object) -> list[ImageContent]:
    """Every image in ``result`` that fits the budget, in order.

    Truncating is the lesser loss. A result the client refuses outright takes
    the structured payload down with it, so surplus images are dropped and
    whatever fits — plus the text — still reaches the agent. Pages arrive in
    order, so the ones kept are the ones asked for first.
    """
    contents: list[ImageContent] = []
    budget = MAX_MCP_IMAGE_BYTES_TOTAL
    for data, media_type in _binary_parts(result):
        if len(data) > budget:
            continue
        budget -= len(data)
        contents.append(
            ImageContent(
                type="image",
                data=base64.b64encode(data).decode("ascii"),
                mime_type=media_type,
            )
        )
    return contents


def result_payload(result: object) -> Any:
    """The JSON-able payload for the text/structured channel, minus any binary.

    Only the value of a result that came with images: serializing the images
    too would dump megabytes of bytes into the text payload that
    ``image_contents`` already attaches to the image channel -- enough to blow
    a context window, or overflow the stdio bridge and lose the pages.
    """
    if isinstance(result, WithImages):
        return to_json_value(result.value)
    return to_json_value(result)


# Past this, a result is sent once, as text, rather than twice.
#
# A dict result went out as its JSON text *and* as `structuredContent`, so a
# large query result crossed the Agent Host bridge twice and could reach its
# 8 MiB cap on half the data. The text is the copy to keep: MCP asks a server to
# send it alongside structured content, it is what every client hands its
# model, and these tools declare no `outputSchema`, which is the only thing
# that would oblige a server to send the structured copy.
STRUCTURED_COPY_LIMIT_CHARS = 256 * 1024


def text_content(payload: Any) -> TextContent:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return TextContent(type="text", text=text)


def tool_call_result(result: object) -> CallToolResult:
    """A returned tool result as MCP content, flagged if it reports a failure.

    Images ride alongside the text. Remote harnesses (Codex, Claude Code) are
    vision-capable, but every result used to be flattened to text, so
    `view_image` and `pod_view_document_pages` reached them as JSON describing a
    picture they never received.

    ``is_error`` is set from the payload rather than from whether something was
    raised, because almost nothing here raises -- see ``result_is_failure``. It
    only marks the result; ``structured_content`` is unchanged either way, so a
    harness still hands the model the whole payload and the model can still
    adapt. Making a failure visible is not the same as making it fatal.
    """
    images = image_contents(result)
    payload = result_payload(result)
    if isinstance(payload, dict):
        text = text_content(payload)
        return CallToolResult(
            content=[text, *images],
            structured_content=(
                payload if len(text.text) <= STRUCTURED_COPY_LIMIT_CHARS else None
            ),
            is_error=result_is_failure(payload),
        )
    return CallToolResult(content=[text_content(payload), *images])


def tool_call_error(name: str, exc: Exception) -> CallToolResult:
    """A failed tool call as MCP content, in the shape the model reads."""
    payload = format_tool_error(name, exc)
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=json.dumps(payload, default=str))],
        structured_content=payload,
    )
