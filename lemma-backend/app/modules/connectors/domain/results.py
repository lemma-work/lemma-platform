"""What an operation hands back when the upstream returned bytes, not JSON.

Every kind that can meet a binary response produces this one shape, so the
layers above -- file capture, the agent harness, the pod-bundle fetcher --
recognise a download by its ``type`` discriminator rather than by knowing which
connector it came from. Those readers key off the serialized dict, so the field
names here are a wire contract: ``extra="forbid"`` is what stops a fifth field
appearing on one path and not another.

It lives in ``domain`` because both the ``http`` and ``mcp`` executors need it
and neither should import the other. It arrived here from the vendored
``lemma_connectors.core.results``, which is where the shape was first agreed and
which the ``http`` and ``mcp`` kinds went on importing long after the vendored
clients stopped being the thing that produced it.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field


# Two forms, and they mean different things. `filename="report.pdf"` is a plain
# value; `filename*=UTF-8''report%20Q1.pdf` (RFC 5987) is percent-encoded, and
# handing that one back verbatim names the file `report%20Q1.pdf`.
_FILENAME_RE = re.compile(r'filename(?P<extended>\*)?=(?:UTF-8\'\')?"?([^";]+)"?')


class _HttpResponseLike(Protocol):
    """The two things a response has to expose to become one of these.

    Narrower than ``httpx.Response`` on purpose: this module has no business
    knowing about status codes or redirect history, and a protocol keeps the
    executors free to hand over anything response-shaped without either side
    reaching for ``Any``.
    """

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def content(self) -> bytes: ...


class BinaryContentResult(BaseModel):
    """Structured binary payload returned by an upstream integration operation."""

    type: Literal["binary_content"] = "binary_content"
    content_base64: str = Field(
        ...,
        description="Binary payload encoded as base64 for safe JSON transport.",
    )
    media_type: str = Field(
        default="application/octet-stream",
        description="MIME type reported by the upstream API response.",
    )
    file_name: str | None = Field(
        default=None,
        description="Filename inferred from the upstream response when available.",
    )
    size_bytes: int = Field(
        ...,
        description="Decoded binary payload size in bytes.",
    )
    content_disposition: str | None = Field(
        default=None,
        description="Raw Content-Disposition header returned by the upstream API.",
    )

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        media_type: str | None = None,
        file_name: str | None = None,
        content_disposition: str | None = None,
    ) -> "BinaryContentResult":
        return cls(
            content_base64=base64.b64encode(data).decode("ascii"),
            media_type=(media_type or "application/octet-stream").strip()
            or "application/octet-stream",
            file_name=file_name,
            size_bytes=len(data),
            content_disposition=content_disposition,
        )

    @classmethod
    def from_http_response(
        cls,
        response: _HttpResponseLike,
        *,
        fallback_media_type: str | None = None,
        fallback_file_name: str | None = None,
    ) -> "BinaryContentResult":
        headers = response.headers
        disposition = _header_get(headers, "content-disposition")
        return cls.from_bytes(
            bytes(response.content or b""),
            media_type=_header_get(headers, "content-type") or fallback_media_type,
            file_name=fallback_file_name or _file_name_from_disposition(disposition),
            content_disposition=disposition,
        )


def _header_get(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitively, because the mapping may not be.

    httpx's own header mapping answers the first lookup whatever the casing.
    A plain dict does not, and it is the shape a caller builds by hand -- so a
    response carrying `Content-Type` or `CONTENT-TYPE` would otherwise read as
    having no content type at all.
    """
    value = headers.get(name)
    if value is None:
        wanted = name.casefold()
        for key, candidate in headers.items():
            if key.casefold() == wanted:
                value = candidate
                break
    return str(value) if value is not None else None


def _file_name_from_disposition(disposition: str | None) -> str | None:
    if not disposition:
        return None
    match = _FILENAME_RE.search(disposition)
    if not match:
        return None
    name = match.group(2).strip()
    return unquote(name) if match.group("extended") else name
