"""Request bodies for the OpenAPI HTTP executor: JSON, form, multipart, blob, MIME.

Split out of ``openapi_http_executor.py`` when file inputs arrived, so the
executor keeps to sending requests and reading responses. Everything here is
pure: payload in, the ``httpx`` request keywords out.

File arguments arrive as :class:`MaterializedFile` -- read upstream under the
caller's authorization -- and go out as a multipart part with their own name
and content type, a raw body, or base64 inside JSON.
"""

from __future__ import annotations

import base64
import binascii

from app.modules.connectors.domain.file_input import MaterializedFile

# Guard against pathological in-memory payloads (no streaming by design).
_MAX_FILE_BYTES = 100 * 1024 * 1024


class OpenApiHttpExecutionError(Exception):
    """HTTP-layer failure carrying status + provider detail.

    Shaped so ``LemmaOperationGateway._translate_execution_error`` classifies it
    like the vendored-package execution path (reads ``status_code`` + ``details``).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


def scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def json_safe(value: object) -> object:
    """A file read upstream, as base64, wherever JSON is going to carry it."""
    if isinstance(value, MaterializedFile):
        return value.as_base64()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def _decode_file_object(value: dict[str, object]) -> bytes:
    raw = value.get("bytes")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if "base64" in value:
        try:
            return base64.b64decode(str(value["base64"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise OpenApiHttpExecutionError(
                f"Invalid base64 file input: {exc}"
            ) from exc
    if "text" in value:
        return str(value["text"]).encode("utf-8")
    if "pod_path" in value:
        raise OpenApiHttpExecutionError(
            "File input references a pod path that was not read before the call; "
            'pass {"base64": ...} or run it with a pod.'
        )
    raise OpenApiHttpExecutionError(
        "File input object must contain 'pod_path', 'base64', or 'text'."
    )


def coerce_file_bytes(value: object) -> tuple[bytes, str | None]:
    """Resolve a file argument to raw bytes.

    Normally a :class:`MaterializedFile` -- the reference was read upstream,
    under the caller's authorization. Raw bytes, ``{"base64": ...}``,
    ``{"text": ...}`` and a plain string are still taken as they always were.
    """
    if isinstance(value, MaterializedFile):
        data, filename = value.content, value.filename
    elif isinstance(value, (bytes, bytearray)):
        data, filename = bytes(value), None
    elif isinstance(value, str):
        data, filename = value.encode("utf-8"), None
    elif isinstance(value, dict):
        data = _decode_file_object(value)
        name = value.get("filename")
        filename = name if isinstance(name, str) else None
    else:
        raise OpenApiHttpExecutionError(f"Unsupported file input type: {type(value)!r}")

    if len(data) > _MAX_FILE_BYTES:
        raise OpenApiHttpExecutionError(
            f"File input exceeds the {_MAX_FILE_BYTES // (1024 * 1024)}MB limit."
        )
    return data, filename


def multipart_body(
    body: dict[str, object],
    *,
    binary_fields: list[str],
    form_fields: list[str],
) -> dict[str, object]:
    files: dict[str, tuple[str, bytes] | tuple[str, bytes, str]] = {}
    data: dict[str, str] = {}
    for name in binary_fields:
        value = body.get(name)
        if value is None:
            continue
        raw, filename = coerce_file_bytes(value)
        if isinstance(value, MaterializedFile):
            # The part's own content type: a server that sniffs it (Drive,
            # Slack) otherwise stores every upload as octet-stream.
            files[name] = (filename or name, raw, value.media_type)
        else:
            files[name] = (filename or name, raw)
    for name in form_fields:
        value = body.get(name)
        if value is not None:
            data[name] = scalar(value)
    return {"files": files, "data": data}


def raw_body(body: object) -> dict[str, object]:
    if body is None:
        return {}
    if isinstance(body, MaterializedFile) or (
        isinstance(body, dict) and ({"base64", "pod_path", "text", "bytes"} & set(body))
    ):
        data, _ = coerce_file_bytes(body)
        return {"content": data}
    return {"json": json_safe(body)}


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def build_body(
    request_body: dict[str, object] | None,
    payload: dict[str, object],
    headers: dict[str, str],
) -> dict[str, object]:
    """The ``httpx`` body keywords for one operation call."""
    if not request_body:
        return {}
    if request_body.get("encoding") == "rfc822":
        return {"json": _rfc822(request_body, payload)}
    body = payload.get(str(request_body.get("field") or "body"))
    if body is None:
        return {}
    content_type = (
        str(request_body.get("content_type") or "application/json")
        .split(";", 1)[0]
        .strip()
        .lower()
    )
    binary_fields = _strings(request_body.get("binary_fields"))

    if content_type == "application/json" and not binary_fields:
        return {"json": json_safe(body)}
    fields = body if isinstance(body, dict) else {}
    if content_type == "application/x-www-form-urlencoded":
        # httpx sets the Content-Type itself for `data=`, and drops the keys
        # the caller left out rather than sending empty values for them.
        return {
            "data": {
                name: scalar(value)
                for name, value in fields.items()
                if value is not None
            }
        }
    if content_type == "multipart/form-data":
        return multipart_body(
            fields,
            binary_fields=binary_fields,
            form_fields=_strings(request_body.get("form_fields")),
        )
    return _blob(body, content_type, headers)


def _blob(
    body: object, content_type: str, headers: dict[str, str]
) -> dict[str, object]:
    """Single-blob body (octet-stream / */*): send raw bytes."""
    raw, _ = coerce_file_bytes(body)
    if isinstance(body, MaterializedFile) and content_type in ("", "*/*"):
        # The spec accepts anything, so say what this actually is.
        content_type = body.media_type
    headers.setdefault("Content-Type", content_type or "application/octet-stream")
    return {"content": raw}


def _rfc822(
    request_body: dict[str, object], payload: dict[str, object]
) -> dict[str, object]:
    """Built from the whole payload rather than read from one field of it: the
    operation takes `to`, `subject`, `attachments` and the rest, and sends
    them as one encoded message."""
    from app.modules.connectors.infrastructure.adapters.mime_message import (
        MimeMessageError,
        rfc822_json_body,
    )

    try:
        return rfc822_json_body(request_body, payload)
    except MimeMessageError as exc:
        raise OpenApiHttpExecutionError(str(exc)) from exc


__all__ = [
    "OpenApiHttpExecutionError",
    "build_body",
    "coerce_file_bytes",
    "json_safe",
    "multipart_body",
    "raw_body",
    "scalar",
]
