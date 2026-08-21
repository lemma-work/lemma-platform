"""Find the file in an operation result, whatever shape the provider used.

Every kind returns binary content differently, and the previous check looked
only at the top level for one specific shape. That meant Composio results --
which nest file outputs arbitrarily deep under ``data`` and use their own
``{name, mimetype, s3url}`` envelope -- were never recognised at all, so asking
for a download quietly did nothing.

Detection is therefore a recursive walk over three known shapes, and the result
is classified by size rather than by whether the caller remembered to ask:
small payloads stay inline, large ones are streamed to the pod datastore, and
anything past the hard ceiling is refused instead of being buffered.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Literal

_MAX_WALK_DEPTH = 8


@dataclass(frozen=True, slots=True)
class BinaryCandidate:
    """A file found in a result, described but not yet fetched."""

    source: Literal["inline", "url"]
    path: list[str | int]
    filename: str | None = None
    media_type: str | None = None
    # Exactly one of these is set.
    data: bytes | None = None
    url: str | None = None
    size_bytes: int | None = None


def _decode_base64(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error, ValueError:
        return None


def classify_binary(
    value: Any, path: list[str | int] | None = None
) -> BinaryCandidate | None:
    """Recognise one value as binary content, or return None."""
    path = path or []

    if isinstance(value, (bytes, bytearray)):
        return BinaryCandidate(
            source="inline", path=path, data=bytes(value), size_bytes=len(value)
        )

    if not isinstance(value, dict):
        return None

    # 1. Our own envelope, produced by the http/mcp/package executors.
    if value.get("type") == "binary_content" and value.get("content_base64"):
        data = _decode_base64(value["content_base64"])
        if data is not None:
            return BinaryCandidate(
                source="inline",
                path=path,
                data=data,
                filename=value.get("file_name"),
                media_type=value.get("media_type"),
                size_bytes=value.get("size_bytes") or len(data),
            )

    # 2. Composio's file envelope. Recognising this is what makes a Google Drive
    #    download reachable: with Composio's own file handling on, the payload
    #    was written to container-local disk and the caller was handed a path it
    #    could not open.
    if value.get("s3url") and value.get("name"):
        return BinaryCandidate(
            source="url",
            path=path,
            url=str(value["s3url"]),
            filename=str(value["name"]),
            media_type=value.get("mimetype") or value.get("mime_type"),
        )

    return None


def find_binary(
    result: Any, *, path: list[str | int] | None = None, depth: int = 0
) -> BinaryCandidate | None:
    """Walk a result and return the first binary payload found.

    Depth-limited: a provider response is data, and a pathological one should
    not be able to drive unbounded recursion.
    """
    path = path or []
    if depth > _MAX_WALK_DEPTH:
        return None

    found = classify_binary(result, path)
    if found is not None:
        return found

    if isinstance(result, dict):
        for key, value in result.items():
            nested = find_binary(value, path=[*path, key], depth=depth + 1)
            if nested is not None:
                return nested
    elif isinstance(result, list):
        for index, value in enumerate(result):
            nested = find_binary(value, path=[*path, index], depth=depth + 1)
            if nested is not None:
                return nested
    return None


def replace_at(result: Any, path: list[str | int], replacement: Any) -> Any:
    """Return ``result`` with the value at ``path`` swapped for ``replacement``."""
    if not path:
        return replacement
    cursor = result
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = replacement
    return result
