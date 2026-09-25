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

Each source says "file" its own way -- Composio with ``file_uploadable``, OpenAPI
with ``format: binary``, MCP servers with ``contentEncoding: base64`` -- and the
stored schema keeps the provider's own form, because the executor needs it to
encode the file back. :func:`present_input_schema` is the view a *caller* gets:
every file node replaced by the one reference form above.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from app.modules.connectors.domain.file_input import FILE_MARKER

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


def is_single_file_schema(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get(FILE_MARKER) is True:
        return True
    # Composio's flag, on its `{name, mimetype, s3key}` object: a file staged to
    # Composio's own storage, which no caller of ours can produce directly.
    if schema.get("file_uploadable") is True:
        return True
    # OpenAPI's own way of saying "binary", for specs imported before the marker
    # existed.
    if schema.get("type") == "string" and schema.get("format") == "binary":
        return True
    if schema.get("contentEncoding") == "base64":
        return True
    # The OpenAPI importer's reference schema, stored before it carried the
    # marker: an object variant that takes a `pod_path`.
    properties = schema.get("properties")
    return (
        isinstance(properties, dict)
        and "pod_path" in properties
        and (schema.get("type") == "object")
    )


def _is_pod_path_variant(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    return (
        schema.get(FILE_MARKER) is not True
        and isinstance(properties, dict)
        and "pod_path" in properties
    )


def schema_variants(schema: dict[str, object]) -> list[object]:
    variants: list[object] = []
    for key in ("oneOf", "anyOf"):
        listed = schema.get(key)
        if isinstance(listed, list):
            variants.extend(listed)
    return variants


def is_file_schema(schema: object) -> bool:
    """Whether a schema node accepts a file, or a list of files.

    A list counts: Gmail's `attachment` is `anyOf [file, array of file]`, and a
    field that takes several attachments is as much a file field as one that
    takes one.
    """
    if not isinstance(schema, dict):
        return False
    if is_single_file_schema(schema):
        return True
    if schema.get("type") == "array" and is_single_file_schema(schema.get("items")):
        return True
    return any(is_file_schema(variant) for variant in schema_variants(schema))


_REFERENCE_HINT = (
    'Pass a file reference: {"pod_path": "/me/report.pdf"} for a file in the pod, '
    '{"file_id": "<pod file id>"}, {"url": "https://..."}, or '
    '{"base64": "...", "filename": "report.pdf"} for small inline content.'
)


def file_reference_schema(description: str | None = None) -> dict[str, object]:
    """The one schema a caller sees for a file argument, whatever the kind."""
    text = description.rstrip() if description else ""
    if not text.endswith(_REFERENCE_HINT):
        # Idempotent: a schema that is already presented comes back unchanged.
        text = f"{text} {_REFERENCE_HINT}".strip()
    return {
        FILE_MARKER: True,
        "type": "object",
        "description": text,
        "properties": {
            "pod_path": {
                "type": "string",
                "description": "Path of a file in the pod, e.g. /me/report.pdf.",
            },
            "file_id": {"type": "string", "description": "Id of a pod file."},
            "url": {"type": "string", "description": "A public https URL."},
            "base64": {"type": "string", "description": "Base64-encoded bytes."},
            "filename": {
                "type": "string",
                "description": "Name to send the file under. Defaults to the "
                "pod file's own name.",
            },
            "media_type": {
                "type": "string",
                "description": "MIME type, when the name does not imply it.",
            },
        },
        "anyOf": [
            {"required": ["pod_path"]},
            {"required": ["file_id"]},
            {"required": ["url"]},
            {"required": ["base64"]},
        ],
        "additionalProperties": False,
    }


def present_input_schema(schema: dict[str, object]) -> dict[str, object]:
    """The input schema as a caller should see it: file nodes as references.

    Pure and non-mutating -- the stored schema is what executors encode
    against, so it must come back untouched.
    """
    presented = _present(schema)
    return presented if isinstance(presented, dict) else schema


def _description(schema: dict[str, object]) -> str | None:
    description = schema.get("description")
    return description if isinstance(description, str) else None


def _present(node: object) -> object:
    if not isinstance(node, dict):
        return node
    if is_single_file_schema(node) or any(
        _is_pod_path_variant(variant) for variant in schema_variants(node)
    ):
        # A variant taking `pod_path` is the OpenAPI importer's own `oneOf
        # [pod_path, base64, text]`. Swapping only that branch would leave
        # `{"base64": ...}` matching two branches of a oneOf, which fails
        # validation; the whole node is a file argument, so it is replaced.
        return file_reference_schema(_description(node))
    return {key: _present_child(key, value) for key, value in node.items()}


def _present_child(key: str, value: object) -> object:
    """Recurse into the keywords that hold subschemas, and nowhere else.

    `properties` maps names to schemas, where a property literally called
    `items` is a name, not a keyword -- so each keyword is handled for the
    shape it has.
    """
    if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
        return {name: _present(sub) for name, sub in value.items()}
    if key in ("oneOf", "anyOf", "allOf") and isinstance(value, list):
        return [_present(variant) for variant in value]
    if key == "items":
        return _present(value)
    return value


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
