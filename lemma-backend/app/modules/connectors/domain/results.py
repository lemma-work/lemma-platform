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
from collections.abc import Mapping
from email.message import Message
from email.utils import collapse_rfc2231_value
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


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
    """The filename a `Content-Disposition` header names, if it names one.

    Parsed rather than pattern-matched. `Content-Disposition` is a structured
    header and its grammar has more in it than a regex over
    ``filename=`` catches: parameter names are case-insensitive, whitespace is
    allowed around the ``=``, a quoted value may contain a semicolon, and the
    RFC 5987 ``filename*`` form carries a charset and percent-encoding that has
    to be decoded or the saved file is called ``report%20Q1.pdf``.

    When a header carries both forms -- which senders do precisely so that a
    client understanding only one still gets a name -- RFC 6266 §4.3 says to
    take ``filename*``. `Message.get_filename` returns whichever came first
    instead, so the preference is applied here.
    """
    if not disposition:
        return None
    parsed = Message()
    parsed["content-disposition"] = disposition
    plain: str | None = None
    for name, value in parsed.get_params(header="content-disposition") or ():
        if name.lower() != "filename":
            continue
        if isinstance(value, tuple):
            # The extended form: (charset, language, percent-encoded value).
            return collapse_rfc2231_value(value).strip() or None
        if plain is None:
            plain = value
    return (plain or "").strip() or None
