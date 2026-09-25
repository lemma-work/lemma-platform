"""Read the files an operation's arguments name, before the operation runs.

The input half of the file protocol, which until now existed only as parsing
helpers nothing called: a Gmail attachment given as ``{"pod_path": ...}`` went
to Composio as that literal dict and was rejected, and an OpenAPI multipart
upload raised "no pod context" for every pod path. Every kind failed the same
way because none of them read the file.

This runs between resolving an execution and dispatching it, under the
*caller's* authorization context -- a person's, or an agent's delegated
workload context -- so a connector call can never become a way to send a file
somewhere that its caller could not have read. The storage read inside the
datastore contract already releases the pooled connection; nothing here holds
one across a provider call.

Each file-typed value is replaced by a :class:`MaterializedFile`. The executor
for the install's kind turns that into its own wire form.
"""

from __future__ import annotations

import base64
import binascii
import copy
import mimetypes
from collections.abc import Iterator
from uuid import UUID

import httpx

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.file_input import MaterializedFile
from app.modules.connectors.domain.ports import PodFileGatewayPort
from app.modules.connectors.services.files.file_ref import (
    FileReference,
    is_single_file_schema,
    schema_variants,
    parse_file_reference,
)

_DEFAULT_MEDIA_TYPE = "application/octet-stream"


def _media_type(filename: str | None, declared: str | None) -> str:
    if declared:
        return declared
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or _DEFAULT_MEDIA_TYPE


def _refused(message: str, *, field: str, reason: str) -> ConnectorValidationError:
    # A validation error that keeps its message, deliberately: this is about
    # the caller's own argument -- which field, and why -- not a provider's
    # reply, which is what `OperationExecutionValidationError` exists to scrub.
    return ConnectorValidationError(message, details={"reason": reason, "field": field})


# Where a file value sits: its container and its key or index there, so it can
# be read in place, plus a path naming it for the caller.
_Holder = dict[str, object] | list[object]
_Slot = tuple[_Holder, str | int, str]


def _get(holder: _Holder, key: str | int) -> object:
    if isinstance(holder, list):
        return holder[key] if isinstance(key, int) else None
    return holder.get(key) if isinstance(key, str) else None


def _set(holder: _Holder, key: str | int, value: object) -> None:
    if isinstance(holder, list) and isinstance(key, int):
        holder[key] = value
    elif isinstance(holder, dict) and isinstance(key, str):
        holder[key] = value


def contains_file_reference(schema: object, payload: dict[str, object]) -> bool:
    """Whether anything in ``payload`` is a file reference to read.

    Cheap enough to call on every execution, so the common case -- no file at
    all -- opens no scope and reads nothing.
    """
    box: dict[str, object] = {"$": payload}
    return any(
        _is_reference(_get(holder, key))
        for holder, key, _ in _file_slots(schema, box, "$", "$")
    )


def _is_reference(value: object) -> bool:
    return not isinstance(value, (bytes, bytearray)) and (
        parse_file_reference(value) is not None
    )


def _file_slots(
    schema: object,
    holder: _Holder,
    key: str | int,
    path: str,
) -> Iterator[_Slot]:
    """Every place under ``holder[key]`` that the schema says holds a file."""
    value = _get(holder, key)
    if value is None or not isinstance(schema, dict):
        return
    if _file_node(schema) is not None:
        if isinstance(value, list):
            yield from ((value, i, f"{path}[{i}]") for i in range(len(value)))
        else:
            yield holder, key, path
    elif isinstance(value, dict):
        yield from _object_slots(schema, value, path)
    elif isinstance(value, list):
        items = schema.get("items")
        for index in range(len(value) if isinstance(items, dict) else 0):
            yield from _file_slots(items, value, index, f"{path}[{index}]")


def _object_slots(
    schema: dict[str, object], value: dict[str, object], path: str
) -> Iterator[_Slot]:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            if name in value:
                yield from _file_slots(sub, value, name, f"{path}.{name}")
    for variant in schema_variants(schema):
        if isinstance(variant, dict) and variant.get("properties"):
            yield from _object_slots(variant, value, path)


def _file_node(schema: dict[str, object]) -> dict[str, object] | None:
    """The file schema this node stands for, or None if it is not a file field.

    Handles the list forms: an array of files, and Gmail's `anyOf [file, array
    of file]` -- either way the value may be one reference or several.
    """
    items = schema.get("items")
    if is_single_file_schema(schema):
        return schema
    if schema.get("type") == "array" and isinstance(items, dict):
        if is_single_file_schema(items):
            return items
    for variant in schema_variants(schema):
        found = _file_node(variant) if isinstance(variant, dict) else None
        if found is not None:
            return found
    return None


class FileInputResolver:
    """Replaces every file reference in a payload with the file's bytes."""

    def __init__(
        self,
        pod_file_gateway: PodFileGatewayPort | None,
        *,
        pod_id: UUID | None,
        ctx: Context,
    ):
        self._gateway = pod_file_gateway
        self._pod_id = pod_id
        self._ctx = ctx
        self._total = 0

    async def resolve(
        self, schema: object, payload: dict[str, object]
    ) -> dict[str, object]:
        """A copy of ``payload`` with each reference read. Never mutates it."""
        box: dict[str, object] = {"$": copy.deepcopy(payload)}
        for holder, key, path in list(_file_slots(schema, box, "$", "$")):
            _set(holder, key, await self._one(_get(holder, key), path=path))
        resolved = box["$"]
        return resolved if isinstance(resolved, dict) else payload

    async def _one(self, value: object, *, path: str) -> object:
        reference = parse_file_reference(value)
        if reference is None or isinstance(value, (bytes, bytearray)):
            # Not a reference: a provider-native value (a Composio s3key object
            # somebody already staged), or raw text for an OpenAPI blob. The
            # executor knows what to do with those, as it always has.
            return value
        materialized = await self._materialize(reference, path=path)
        self._total += len(materialized.content)
        limit = connector_settings.connector_file_input_max_bytes
        if len(materialized.content) > limit or self._total > limit:
            raise _refused(
                f"File input at {path} exceeds the {limit // (1024 * 1024)} MB limit "
                "for files passed to one operation.",
                field=path,
                reason="file_input_too_large",
            )
        return materialized

    async def _materialize(self, ref: FileReference, *, path: str) -> MaterializedFile:
        if ref.pod_path is not None or ref.file_id is not None:
            return await self._from_pod(ref, path=path)
        if ref.base64_data is not None:
            try:
                content = base64.b64decode(ref.base64_data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise _refused(
                    f"File input at {path} is not valid base64.",
                    field=path,
                    reason="invalid_base64",
                ) from exc
            name = ref.filename or "file"
            return MaterializedFile(content, name, _media_type(name, ref.media_type))
        if ref.url is not None:
            return await self._from_url(ref, path=path)
        if ref.inline_text is not None:
            name = ref.filename or "file.txt"
            return MaterializedFile(
                ref.inline_text.encode("utf-8"), name, _media_type(name, ref.media_type)
            )
        if ref.raw_bytes is not None:
            name = ref.filename or "file"
            return MaterializedFile(
                ref.raw_bytes, name, _media_type(name, ref.media_type)
            )
        raise _refused(
            f"File input at {path} names no file.",
            field=path,
            reason="empty_file_reference",
        )

    async def _from_pod(self, ref: FileReference, *, path: str) -> MaterializedFile:
        if self._gateway is None or self._pod_id is None:
            raise _refused(
                f"File input at {path} names a pod file, but this call has no pod. "
                "Run it from a pod (pass pod_id, or use pod.connectors.execute), "
                "or pass the file inline as base64.",
                field=path,
                reason="file_input_needs_pod",
            )
        try:
            if ref.file_id is not None:
                try:
                    file_id = UUID(str(ref.file_id))
                except ValueError as exc:
                    raise _refused(
                        f"File input at {path} has a file_id that is not a UUID.",
                        field=path,
                        reason="invalid_file_id",
                    ) from exc
                content, media_type, name = await self._gateway.read_bytes_by_id(
                    pod_id=self._pod_id, file_id=file_id, ctx=self._ctx
                )
            else:
                content, media_type, name = await self._gateway.read_bytes(
                    pod_id=self._pod_id, path=str(ref.pod_path), ctx=self._ctx
                )
        except ConnectorValidationError:
            raise
        except DomainError as exc:
            # Not found, or not readable by whoever is asking. The datastore's
            # own message says which, and it is about the caller's own input.
            raise _refused(
                f"Could not read the file for {path}: {exc.message}",
                field=path,
                reason=exc.code or "file_input_unreadable",
            ) from exc
        filename = ref.filename or name or "file"
        return MaterializedFile(
            content, filename, _media_type(filename, ref.media_type or media_type)
        )

    async def _from_url(self, ref: FileReference, *, path: str) -> MaterializedFile:
        from urllib.parse import urlsplit

        from app.core.net.http_client import get_shared_http_client
        from app.core.net.url_guard import UnsafeUrlError, fetch_guarded

        try:
            content = await fetch_guarded(
                get_shared_http_client(),
                str(ref.url),
                max_bytes=connector_settings.connector_file_input_max_bytes,
                timeout=60.0,
            )
        except UnsafeUrlError as exc:
            raise _refused(
                f"Refused to fetch the file for {path}.",
                field=path,
                reason=exc.reason,
            ) from exc
        except httpx.HTTPError as exc:
            raise _refused(
                f"Could not fetch the file for {path}: {type(exc).__name__}.",
                field=path,
                reason="file_input_fetch_failed",
            ) from exc
        filename = (
            ref.filename or urlsplit(str(ref.url)).path.rsplit("/", 1)[-1] or "file"
        )
        return MaterializedFile(
            content, filename, _media_type(filename, ref.media_type)
        )


def split_output_path(
    schema: object, payload: dict[str, object] | None
) -> tuple[dict[str, object], str | None]:
    """Take Lemma's ``output_path`` out of what the provider will be sent.

    ``output_path`` says where a file *result* should land in the pod. It is an
    argument to Lemma, and it was forwarded to the provider along with the
    rest -- Composio and MCP received it as an unknown argument. It stays in
    the payload only when the operation's own schema declares a field of that
    name (the OpenAPI importer declares it, and its executor ignores it).
    """
    payload = dict(payload or {})
    output_path = payload.get("output_path")
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not (isinstance(properties, dict) and "output_path" in properties):
        payload.pop("output_path", None)
    return payload, output_path if isinstance(output_path, str) else None


__all__ = ["FileInputResolver", "contains_file_reference", "split_output_path"]
