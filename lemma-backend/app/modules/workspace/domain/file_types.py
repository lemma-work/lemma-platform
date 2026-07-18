from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional
import os
import mimetypes
from pydantic import BaseModel

from app.core.log.log import get_logger

logger = get_logger(__name__)

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
    description: Optional[str] = None


def is_text_file(path: str) -> bool:
    extention = os.path.splitext(path)[-1]
    return extention in TEXT_FILE_EXTENSIONS


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
    # Audio
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    # Images
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    # Documents
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html",
    ".md": "text/markdown",
    # Videos
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
}


def get_content_type(path: str) -> str:
    """Return the MIME type for the provided file path."""
    extension = os.path.splitext(path)[1].lower()

    if extension in EXTENSION_MIME_MAP:
        return EXTENSION_MIME_MAP[extension]

    mime_type, _ = mimetypes.guess_type(path)
    if mime_type:
        return mime_type

    logger.debug("workspace.mime_type.unknown")
    return "application/octet-stream"


@dataclass
class FileInfo:
    """Information about a file or directory."""

    name: str
    path: str
    type: Literal["file", "directory"]  # 'file' or 'directory'
    size: Optional[int] = None
    created: Optional[str] = None
    last_modified: Optional[str] = None

    @property
    def file_type(self) -> FileType:
        """Get the file type of the file."""
        return ExtensionFileTypeMap.get(
            os.path.splitext(self.path)[1], FileType.UNKNOWN
        )

    @property
    def mime_type(self) -> str:
        """Get the MIME type of the file."""
        return get_content_type(self.path)

    @property
    def is_text_file(self) -> bool:
        """Check if the file is a text file."""
        return is_text_file(self.path)
