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

from mcp.types import ImageContent, TextContent

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
    """Image payloads carried by a pydantic-ai ``ToolReturn``, if any."""
    content = getattr(result, "content", None)
    if not content:
        return []
    images: list[tuple[bytes, str]] = []
    for item in content if isinstance(content, (list, tuple)) else [content]:
        data = getattr(item, "data", None)
        media_type = getattr(item, "media_type", None)
        if (
            isinstance(data, bytes)
            and isinstance(media_type, str)
            and media_type.startswith("image/")
            and len(data) <= MAX_MCP_IMAGE_BYTES
        ):
            images.append((data, media_type))
    return images


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
                mimeType=media_type,
            )
        )
    return contents


def text_content(payload: Any) -> TextContent:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return TextContent(type="text", text=text)
