"""How a caller names a file when passing one to a connector operation.

One wire format for every kind, so the same payload works whether the operation
runs through Composio, a vendored package, an OpenAPI descriptor or MCP::

    {"pod_path": "/me/report.pdf"}      # a file in the pod datastore
    {"file_id":  "0193..."}             # the same, by id
    {"base64":   "...", "filename": …}  # inline, for small payloads
    {"url":      "https://..."}         # fetched, subject to the URL guard

File-typed inputs are found by walking the operation's **input schema**, not its
execution descriptor. That distinction is the whole point: Composio operations
carry no execution descriptor, so the descriptor-driven version silently skipped
them and pod-file uploads never worked for any Composio tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

# Marks a schema node as accepting one of the reference forms above. Emitted by
# the OpenAPI importer, the MCP discoverer, and the catalog importer translating
# Composio's own `file_uploadable` flag.
FILE_MARKER = "x-lemma-file"

_REFERENCE_KEYS = ("pod_path", "file_id", "base64", "url", "bytes", "text")


@dataclass(frozen=True, slots=True)
class FileReference:
    """A parsed file input, before it has been materialized."""

    pod_path: str | None = None
    file_id: str | None = None
    base64_data: str | None = None
    url: str | None = None
    inline_text: str | None = None
    raw_bytes: bytes | None = None
    filename: str | None = None
    media_type: str | None = None

    @property
    def needs_pod_context(self) -> bool:
        return self.pod_path is not None or self.file_id is not None


def is_file_schema(schema: Any) -> bool:
    """Whether a schema node accepts a file reference."""
    if not isinstance(schema, dict):
        return False
    if schema.get(FILE_MARKER) is True:
        return True
    # OpenAPI's own way of saying "binary", for specs imported before the marker
    # existed.
    if schema.get("type") == "string" and schema.get("format") == "binary":
        return True
    if schema.get("contentEncoding") == "base64":
        return True
    return any(is_file_schema(variant) for variant in schema.get("oneOf") or ())


def iter_file_fields(
    schema: dict[str, Any] | None, payload: dict[str, Any] | None
) -> Iterator[tuple[list[str], Any]]:
    """Yield ``(path, value)`` for every file-typed field present in a payload.

    Paths are lists of keys so a caller can replace the value in place; nesting
    matters because a multipart body puts its file fields one level down under
    ``body``.
    """
    if not isinstance(schema, dict) or not isinstance(payload, dict):
        return
    properties = schema.get("properties") or {}
    for name, sub_schema in properties.items():
        if name not in payload or payload[name] is None:
            continue
        value = payload[name]
        if is_file_schema(sub_schema):
            yield [name], value
            continue
        if isinstance(value, dict):
            for nested_path, nested_value in iter_file_fields(sub_schema, value):
                yield [name, *nested_path], nested_value


def parse_file_reference(value: Any) -> FileReference | None:
    """Interpret a payload value as a file reference, or return None."""
    if isinstance(value, (bytes, bytearray)):
        return FileReference(raw_bytes=bytes(value))
    if not isinstance(value, dict):
        return None
    if not any(key in value for key in _REFERENCE_KEYS):
        return None
    return FileReference(
        pod_path=value.get("pod_path"),
        file_id=value.get("file_id"),
        base64_data=value.get("base64"),
        url=value.get("url"),
        inline_text=value.get("text"),
        raw_bytes=value.get("bytes") if isinstance(value.get("bytes"), bytes) else None,
        filename=value.get("filename"),
        media_type=value.get("media_type") or value.get("mime_type"),
    )


def set_in(payload: dict[str, Any], path: list[str], value: Any) -> None:
    """Replace the value at ``path`` inside ``payload``."""
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
