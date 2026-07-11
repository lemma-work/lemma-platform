"""Transport-neutral file types and MIME detection shared by modules."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel

TEXT_FILE_EXTENSIONS = [
    ".txt", ".md", ".html", ".json", ".csv", ".py", ".js", ".css",
    ".svg", ".xml", ".ts", ".tsx", ".jsx",
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


def get_content_type(path: str) -> str:
    extension = os.path.splitext(path)[1].lower()
    return EXTENSION_MIME_MAP.get(extension) or mimetypes.guess_type(path)[0] or (
        "application/octet-stream"
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
