"""One file protocol, both directions, every kind."""

from app.modules.connectors.services.files.capture import (
    BinaryCandidate,
    classify_binary,
    find_binary,
)
from app.modules.connectors.services.files.file_ref import (
    FILE_MARKER,
    FileReference,
    is_file_schema,
    iter_file_fields,
    parse_file_reference,
)

__all__ = [
    "BinaryCandidate",
    "FILE_MARKER",
    "FileReference",
    "classify_binary",
    "find_binary",
    "is_file_schema",
    "iter_file_fields",
    "parse_file_reference",
]
