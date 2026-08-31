"""Transport-neutral file types and MIME detection shared by modules."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel

TEXT_FILE_EXTENSIONS = [
    ".txt",
    ".md",
    ".html",
    ".json",
    ".csv",
    ".py",
    ".js",
    ".css",
    ".svg",
    ".xml",
    ".ts",
    ".tsx",
    ".jsx",
]


class FileDescription(BaseModel):
    file_path: str
    description: str | None = None


def is_text_file(path: str) -> bool:
    return os.path.splitext(path)[-1] in TEXT_FILE_EXTENSIONS


class FileType(str, Enum):
    TEXT = "TEXT"
    PDF = "PDF"
    WORD = "WORD"
    EXCEL = "EXCEL"
    POWERPOINT = "POWERPOINT"
    MARKDOWN = "MARKDOWN"
    PLAIN_TEXT = "PLAIN_TEXT"
    HTML = "HTML"
    SVG = "SVG"
    MERMAID = "MERMAID"
    PYTHON = "PYTHON"
    JAVASCRIPT = "JAVASCRIPT"
    TYPESCRIPT = "TYPESCRIPT"
    JSON = "JSON"
    CSV = "CSV"
    UNKNOWN = "UNKNOWN"


ExtensionFileTypeMap = {
    ".md": FileType.MARKDOWN,
    ".pdf": FileType.PDF,
    ".docx": FileType.WORD,
    ".pptx": FileType.POWERPOINT,
    ".xlsx": FileType.EXCEL,
    ".ppt": FileType.POWERPOINT,
    ".doc": FileType.WORD,
    ".xls": FileType.EXCEL,
    ".csv": FileType.CSV,
    ".json": FileType.JSON,
    ".txt": FileType.PLAIN_TEXT,
    ".html": FileType.HTML,
    ".svg": FileType.SVG,
    ".mermaid": FileType.MERMAID,
    ".py": FileType.PYTHON,
    ".js": FileType.JAVASCRIPT,
    ".ts": FileType.TYPESCRIPT,
    ".jsx": FileType.JAVASCRIPT,
    ".tsx": FileType.TYPESCRIPT,
}

EXTENSION_MIME_MAP = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html",
    ".md": "text/markdown",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
}


def extension_for_mime(mime_type: str | None) -> str | None:
    """The file extension a MIME type should be saved under, or None.

    The reverse of :func:`get_content_type`, and it exists because a name is the
    only thing the datastore has to type a file by: a WhatsApp photo arrives
    with a mime type and no filename at all, and stored as bare ``image`` it
    comes back out as ``application/octet-stream`` -- unviewable, unindexable,
    and indistinguishable from a blob.
    """
    normalized = str(mime_type or "").split(";")[0].strip().lower()
    # "octet-stream" is the absence of a type, not a type. Naming a file
    # ``photo.bin`` from it would be worse than leaving it bare: it looks
    # decided.
    if not normalized or normalized.endswith("/octet-stream"):
        return None
    guessed = mimetypes.guess_extension(normalized)
    if guessed:
        return guessed
    for extension, mapped in EXTENSION_MIME_MAP.items():
        if mapped == normalized:
            return extension
    return None


def get_content_type(path: str) -> str:
    extension = os.path.splitext(path)[1].lower()
    return (
        EXTENSION_MIME_MAP.get(extension)
        or mimetypes.guess_type(path)[0]
        or ("application/octet-stream")
    )


def sniff_image_mime(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"BM"):
        return "image/bmp"
    return None


@dataclass
class FileInfo:
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int | None = None
    created: str | None = None
    last_modified: str | None = None

    @property
    def file_type(self) -> FileType:
        return ExtensionFileTypeMap.get(
            os.path.splitext(self.path)[1], FileType.UNKNOWN
        )

    @property
    def mime_type(self) -> str:
        return get_content_type(self.path)

    @property
    def is_text_file(self) -> bool:
        return is_text_file(self.path)


#: What every layer falls back to when nothing worked out what the bytes are.
#: Both spellings occur in the wild; neither names a type.
_UNTYPED_MIME_TYPES = {"application/octet-stream", "binary/octet-stream"}


def is_untyped_mime(mime_type: str | None) -> bool:
    """True when a mime type is the *absence* of a type rather than a type.

    Reading ``application/octet-stream`` as an answer is how a PNG ends up
    refused for not being an image: the datastore types a file by its name alone,
    a chat surface is free to send one with no name, and every reader downstream
    then believes the fallback. A caller holding the bytes should sniff instead
    of trusting this -- which it can only do if it can tell the difference.
    """
    normalized = str(mime_type or "").split(";")[0].strip().lower()
    return not normalized or normalized in _UNTYPED_MIME_TYPES


def sniff_media_mime(content: bytes) -> str | None:
    """The type of these bytes from their magic number, or None.

    A superset of :func:`sniff_image_mime`, and the difference matters on a chat
    surface: Telegram sends a voice note with no filename and a photo with
    neither a filename nor a declared type, so an image-only sniffer still leaves
    the audio stored as a blob -- saved, listed, and unplayable.

    Only formats a surface actually delivers. A sniffer that guesses widely is a
    sniffer that guesses wrong, and a wrong type is worse than none: it looks
    decided.
    """
    image = sniff_image_mime(content)
    if image:
        return image
    if content.startswith(b"OggS"):
        return "audio/ogg"
    if content.startswith(b"ID3") or content[:2] in (
        b"\xff\xfb",
        b"\xff\xf3",
        b"\xff\xf2",
    ):
        return "audio/mpeg"
    if content.startswith(b"fLaC"):
        return "audio/flac"
    # ISO base media: the brand at offset 8 separates audio-only M4A from video.
    if content[4:8] == b"ftyp":
        return "audio/mp4" if content[8:11] == b"M4A" else "video/mp4"
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if content.startswith(b"RIFF") and content[8:12] == b"AVI ":
        return "video/x-msvideo"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    return None
