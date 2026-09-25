"""A file argument after its reference has been read: bytes, a name, a type.

Callers name a file by reference -- ``{"pod_path": "/me/report.pdf"}`` and its
siblings, see ``services/files/file_ref.py`` -- and the reference is read under
the caller's own authorization before the operation is dispatched. What reaches
an executor is this value, which each kind turns into its own wire form:
Composio stages it and passes an ``s3key``, OpenAPI puts it in a multipart
part, MCP sends it base64.

In the domain because every kind's executor reads it, and executors live in
infrastructure, which must not import services.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

# Marks a schema node as accepting a file reference. Emitted by the OpenAPI
# importer; recognised, with each provider's own spelling, by
# ``services/files/file_ref.py``.
FILE_MARKER = "x-lemma-file"


@dataclass(frozen=True, slots=True)
class MaterializedFile:
    content: bytes
    filename: str
    media_type: str

    def __repr__(self) -> str:
        # Never the bytes: this sits inside a payload that error paths and
        # debuggers print, and a 20 MB attachment in a log line helps nobody.
        return (
            f"MaterializedFile(filename={self.filename!r}, "
            f"media_type={self.media_type!r}, size={len(self.content)})"
        )

    def as_base64(self) -> str:
        return base64.b64encode(self.content).decode("ascii")


__all__ = ["FILE_MARKER", "MaterializedFile"]
