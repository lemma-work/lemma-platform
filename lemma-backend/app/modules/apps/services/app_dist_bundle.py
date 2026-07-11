"""Helpers for validating and unpacking uploaded app dist archives."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel

from app.modules.apps.domain.errors import AppValidationError
from app.modules.apps.services.archive_validation import inspect_app_archive


class AppDistFile(BaseModel):
    path: str
    content: bytes


class AppDistBundle(BaseModel):
    files: list[AppDistFile]


def _normalize_archive_path(raw_path: str) -> str:
    normalized = raw_path.replace("\\", "/").strip("/")
    if not normalized:
        raise AppValidationError("Dist archive contains an empty file path")

    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AppValidationError(f"Dist archive contains an invalid path: {raw_path}")
    return path.as_posix()


def load_app_dist_bundle(dist_archive_bytes: bytes | Path) -> AppDistBundle:
    inspect_app_archive(dist_archive_bytes, label="Dist archive")
    try:
        archive = ZipFile(
            dist_archive_bytes
            if isinstance(dist_archive_bytes, Path)
            else BytesIO(dist_archive_bytes)
        )
    except BadZipFile as exc:
        raise AppValidationError("Dist archive must be a valid zip file") from exc

    files: list[AppDistFile] = []
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            path = _normalize_archive_path(info.filename)
            if path.startswith("__MACOSX/"):
                continue

            files.append(AppDistFile(path=path, content=archive.read(info)))

    if not files:
        raise AppValidationError("Dist archive is empty")

    file_map = {item.path: item for item in files}
    if "index.html" not in file_map:
        raise AppValidationError("Dist archive must include index.html at its root")

    return AppDistBundle(files=files)
