"""Turning a tool result into MCP content a remote harness can actually read.

Both bridges once rendered every result as text, so `view_image` and
`pod_view_document_pages` reached a vision-capable agent as JSON describing a
picture it could not see. These cover the other half: that the images which do
go out fit inside what the client on the far end will accept.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import BinaryContent, ToolReturn

from app.modules.agent.services.mcp_content import (
    MAX_MCP_IMAGE_BYTES,
    MAX_MCP_IMAGE_BYTES_TOTAL,
    image_contents,
    result_payload,
)


@dataclass
class _Binary:
    data: bytes
    media_type: str


@dataclass
class _Result:
    content: list[_Binary]


def _png(size: int) -> _Binary:
    return _Binary(data=b"x" * size, media_type="image/png")


def test_images_are_returned_in_order() -> None:
    result = _Result([_png(10), _png(20)])

    contents = image_contents(result)

    assert [c.type for c in contents] == ["image", "image"]
    assert [c.mimeType for c in contents] == ["image/png", "image/png"]


def test_one_oversized_image_is_dropped_without_taking_the_others() -> None:
    result = _Result([_png(MAX_MCP_IMAGE_BYTES + 1), _png(32)])

    assert len(image_contents(result)) == 1


def test_a_long_page_range_is_truncated_rather_than_refused() -> None:
    """The failure this prevents is an opaque one.

    ``pod_view_document_pages`` returns one image per page and takes an
    unbounded range, so thirty pages that each clear the per-image limit still
    added up past what Agent Host's stdio bridge accepts for a whole response —
    and the agent got a size error instead of the pages, with the structured
    payload lost alongside them. Sending the pages that fit is the lesser loss,
    and they are the ones asked for first.
    """
    page = MAX_MCP_IMAGE_BYTES_TOTAL // 4
    result = _Result([_png(page) for _ in range(30)])

    contents = image_contents(result)

    assert len(contents) == 4, "the budget should stop after four whole pages"
    encoded = sum(len(c.data) for c in contents)
    # Base64 adds a third, and the bridge measures the encoded response.
    assert encoded < 8 * 1024 * 1024


def test_a_result_with_no_images_produces_none() -> None:
    assert image_contents(_Result([])) == []
    assert image_contents(object()) == []


def test_tool_return_payload_excludes_the_binary_content() -> None:
    """The text/structured channel must carry the describable result only.

    ``view_image``/``pod_view_document_pages`` return a ``ToolReturn`` whose
    picture rides ``.content``. Serializing the whole ``ToolReturn`` dumped the
    raw image bytes into the text payload too — the ~25k-token duplication (and,
    on a multi-page doc, the overflow that lost the pages). ``result_payload``
    must serialize ``.return_value`` alone.
    """
    picture = b"\x89PNG\r\n" + b"x" * 4096
    result = ToolReturn(
        return_value={"success": True, "path": "/me/shot.png"},
        content=[BinaryContent(data=picture, media_type="image/png")],
    )

    payload = result_payload(result)

    assert payload == {"success": True, "path": "/me/shot.png"}
    assert b"PNG" not in repr(payload).encode()


def test_plain_result_is_serialized_as_is() -> None:
    assert result_payload({"ok": True}) == {"ok": True}
