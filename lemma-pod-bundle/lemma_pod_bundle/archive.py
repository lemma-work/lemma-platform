"""Deterministic zip packing and safe extraction of pod bundle directories.

``pack_bundle`` produces byte-identical archives for identical directory
contents (sorted member order, fixed timestamps, normalized permissions), so a
bundle's archive can be content-hashed or compared. ``extract_bundle`` is the
defensive inverse for archives received over the wire: it rejects zip-slip
paths and symlinks, optionally enforces an uncompressed-size cap, and locates
the bundle root by its ``pod.json`` manifest.
"""

from __future__ import annotations

import io
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .layout import POD_MANIFEST_FILE

# Fixed timestamp for deterministic output (the zip epoch, 1980-01-01).
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o644
_DIR_MODE = 0o755

# Copy buffer for capped extraction.
_CHUNK_SIZE = 1024 * 1024


def pack_bundle(source_dir: Path) -> bytes:
    """Zip a bundle directory deterministically and return the archive bytes.

    Members are stored relative to ``source_dir`` in sorted order with fixed
    timestamps and permissions, so packing the same tree twice yields identical
    bytes. Empty directories are preserved (a bundle keeps its empty resource
    dirs). Symlinks are refused rather than silently followed or embedded.
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise ValueError(f"Bundle directory does not exist: {source_dir}")

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*"), key=lambda p: p.relative_to(source_dir).as_posix()):
            arcname = path.relative_to(source_dir).as_posix()
            if path.is_symlink():
                raise ValueError(f"Refusing to pack symlink: {arcname}")
            if path.is_dir():
                info = ZipInfo(arcname + "/", date_time=_ZIP_EPOCH)
                info.external_attr = (stat.S_IFDIR | _DIR_MODE) << 16
                info.external_attr |= 0x10  # MS-DOS directory flag
                archive.writestr(info, b"")
            elif path.is_file():
                info = ZipInfo(arcname, date_time=_ZIP_EPOCH)
                info.compress_type = ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | _FILE_MODE) << 16
                archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def _is_unsafe_member_name(name: str) -> bool:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute():
        return True
    # Windows drive letters ("C:/...") are not absolute for PurePosixPath.
    if pure.parts and pure.parts[0].endswith(":"):
        return True
    return any(part == ".." for part in pure.parts)


def _is_symlink_member(info: ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def extract_bundle(
    archive: bytes | Path,
    dest_dir: Path,
    *,
    max_uncompressed_bytes: int | None = None,
) -> Path:
    """Extract a bundle archive into ``dest_dir`` and return the bundle root.

    The bundle root is the shallowest extracted directory containing the
    ``pod.json`` manifest (an archive may wrap the bundle in a top-level
    folder). Raises ``ValueError`` for unsafe archives (absolute paths, ``..``
    traversal, symlinks), when the uncompressed size exceeds
    ``max_uncompressed_bytes``, or when no manifest is present.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()

    if isinstance(archive, (bytes, bytearray)):
        source: io.BytesIO | Path = io.BytesIO(bytes(archive))
    else:
        source = Path(archive)

    total_written = 0
    with ZipFile(source) as zf:
        for info in zf.infolist():
            name = info.filename
            if _is_unsafe_member_name(name):
                raise ValueError(f"Unsafe path in bundle archive: {name}")
            if _is_symlink_member(info):
                raise ValueError(f"Symlink not allowed in bundle archive: {name}")

            target = dest_dir / PurePosixPath(name.replace("\\", "/"))
            if not target.resolve().is_relative_to(dest_resolved):
                raise ValueError(f"Unsafe path in bundle archive: {name}")

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as member, open(target, "wb") as out:
                while True:
                    chunk = member.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if (
                        max_uncompressed_bytes is not None
                        and total_written > max_uncompressed_bytes
                    ):
                        raise ValueError(
                            "Bundle archive exceeds the maximum uncompressed size "
                            f"({max_uncompressed_bytes} bytes)"
                        )
                    out.write(chunk)

    manifests = sorted(
        dest_dir.rglob(POD_MANIFEST_FILE),
        key=lambda p: (len(p.relative_to(dest_dir).parts), p.relative_to(dest_dir).as_posix()),
    )
    if not manifests:
        raise ValueError(
            f"Bundle archive has no '{POD_MANIFEST_FILE}' manifest"
        )
    return manifests[0].parent
