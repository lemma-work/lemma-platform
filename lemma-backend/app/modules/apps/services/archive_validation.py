"""Metadata-first validation for uploaded app ZIP archives."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from app.modules.apps.config import apps_settings
from app.modules.apps.domain.errors import AppValidationError


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    entry_count: int
    uncompressed_bytes: int


def _normalized_path(raw_path: str, *, label: str) -> str:
    if "\\" in raw_path or raw_path.startswith(("/", "\\")):
        raise AppValidationError(f"{label} contains an invalid path")
    path = PurePosixPath(raw_path)
    normalized = path.as_posix().rstrip("/")
    if (
        not raw_path
        or normalized in {"", ".", ".."}
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise AppValidationError(f"{label} contains an invalid path")
    return normalized


def inspect_app_archive(data: bytes | Path, *, label: str) -> ArchiveInspection:
    try:
        archive = ZipFile(data if isinstance(data, Path) else BytesIO(data))
    except BadZipFile as exc:
        raise AppValidationError(f"{label} must be a valid zip file") from exc

    total = 0
    paths: set[str] = set()
    with archive:
        infos = archive.infolist()
        if len(infos) > apps_settings.app_archive_max_entries:
            raise AppValidationError(f"{label} contains too many entries")
        for info in infos:
            path = _normalized_path(info.filename, label=label)
            if path in paths:
                raise AppValidationError(f"{label} contains duplicate paths")
            paths.add(path)
            if info.flag_bits & 0x1:
                raise AppValidationError(f"{label} contains an encrypted entry")
            if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                raise AppValidationError(
                    f"{label} uses an unsupported compression method"
                )
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise AppValidationError(f"{label} contains a symbolic link")
            if info.is_dir():
                continue
            total += info.file_size
            if total > apps_settings.app_archive_max_uncompressed_bytes:
                raise AppValidationError(f"{label} expands beyond the configured limit")
            unsafe_ratio = info.file_size > 0 and (
                info.compress_size == 0
                or info.file_size / info.compress_size
                > apps_settings.app_archive_max_compression_ratio
            )
            if unsafe_ratio:
                raise AppValidationError(f"{label} has an unsafe compression ratio")
        if archive.testzip() is not None:
            raise AppValidationError(f"{label} contains a corrupt entry")
    return ArchiveInspection(entry_count=len(infos), uncompressed_bytes=total)
