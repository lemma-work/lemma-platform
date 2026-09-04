"""Turn a measured value into something safe to count.

Every property this module puts on an event has to survive two constraints at
once. An exact count is a fingerprint and a cardinality problem, so it is
reported as the band it falls in. A raw string taken from user input -- a
filename extension, say -- is unbounded, so it is mapped onto a closed set or
dropped.

Both are the same rule: what reaches the warehouse is the shape of the
distribution, never the value itself.
"""

from __future__ import annotations

from datetime import datetime


def bucket(value: int | None, edges: tuple[int, ...]) -> str | None:
    """Band a count."""
    if value is None:
        return None
    low = 0
    for edge in edges:
        if value <= edge:
            return f"{low}-{edge}" if low else f"1-{edge}"
        low = edge
    return f"{edges[-1]}plus"


COUNT_EDGES = (1, 5, 20, 100)


def _range_bucket(
    value: float | None, edges: tuple[tuple[float, str], ...], overflow: str
) -> str | None:
    """Band a magnitude that starts at zero.

    Separate from ``bucket`` because that one is count-shaped: its first label
    reads ``1-n``, which is wrong for a duration or a size, both of which can
    legitimately be zero.
    """
    if value is None:
        return None
    for edge, label in edges:
        if value <= edge:
            return label
    return overflow


_SECONDS_EDGES = (
    (1, "lt1s"),
    (5, "1-5s"),
    (30, "5-30s"),
    (120, "30-120s"),
    (600, "2-10m"),
)
_DAYS_EDGES = (
    (0, "same_day"),
    (7, "1-7d"),
    (30, "7-30d"),
    (90, "30-90d"),
    (365, "90-365d"),
)
_BYTES_EDGES = (
    (10_000, "lt10kb"),
    (100_000, "10-100kb"),
    (1_000_000, "100kb-1mb"),
    (10_000_000, "1-10mb"),
)


def seconds_bucket(seconds: float | None) -> str | None:
    return _range_bucket(seconds, _SECONDS_EDGES, "10m_plus")


def days_bucket(days: float | None) -> str | None:
    return _range_bucket(days, _DAYS_EDGES, "365d_plus")


def bytes_bucket(size: int | None) -> str | None:
    return _range_bucket(size, _BYTES_EDGES, "10mb_plus")


def duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    """Elapsed seconds, or ``None`` when either end is unknown."""
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


#: Document kinds, as a closed set. Never the raw extension: an extension is
#: attacker-supplied, unbounded, and a cardinality problem.
_DOCUMENT_KINDS: dict[str, str] = {
    "csv": "sheet",
    "tsv": "sheet",
    "xls": "sheet",
    "xlsx": "sheet",
    "doc": "doc",
    "docx": "doc",
    "rtf": "doc",
    "odt": "doc",
    "txt": "doc",
    "md": "doc",
    "pdf": "pdf",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "svg": "image",
    "heic": "image",
    "mp3": "audio",
    "wav": "audio",
    "m4a": "audio",
    "ogg": "audio",
    "mp4": "video",
    "mov": "video",
    "webm": "video",
    "avi": "video",
    "py": "code",
    "ts": "code",
    "js": "code",
    "tsx": "code",
    "jsx": "code",
    "json": "code",
    "yaml": "code",
    "yml": "code",
    "sql": "code",
    "sh": "code",
}


def document_kind(path: str | None) -> str:
    if not path or "." not in path:
        return "other"
    return _DOCUMENT_KINDS.get(path.rsplit(".", 1)[-1].strip().lower(), "other")


__all__ = [
    "COUNT_EDGES",
    "bucket",
    "bytes_bucket",
    "days_bucket",
    "document_kind",
    "duration_seconds",
    "seconds_bucket",
]
