"""Versioned manifest for GitHub-published pod bundles.

GitHub publishing may split large binary files into deterministic chunk files so
each connector operation stays comfortably below provider payload limits.  The
manifest makes that layout lossless and lets import reject partial or tampered
repositories before planning any writes.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.modules.pod_bundle.domain.errors import BundleInvalidError

PUBLISH_MANIFEST_PATH = ".lemma/publish-manifest.json"
PUBLISH_MANIFEST_FORMAT_VERSION = 1
PUBLISH_CHUNK_THRESHOLD_BYTES = 150_000

_CHUNK_SUFFIX_RE = re.compile(r"\.chunk(?P<index>\d{4})of(?P<count>\d{4})$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BundleInvalidError("The GitHub publish manifest contains an unsafe path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BundleInvalidError("The GitHub publish manifest contains an unsafe path.")
    return path.as_posix()


def build_publish_layout(
    files: dict[str, bytes],
    *,
    publish_id: str,
    chunk_threshold_bytes: int = PUBLISH_CHUNK_THRESHOLD_BYTES,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Return physical repository files plus their integrity manifest."""
    physical: dict[str, bytes] = {}
    generated: dict[str, dict[str, int | str]] = {}
    sources: dict[str, dict[str, Any]] = {}
    chunks: dict[str, dict[str, Any]] = {}

    for raw_path, content in files.items():
        path = _safe_path(raw_path)
        if path == PUBLISH_MANIFEST_PATH:
            raise BundleInvalidError(
                f"{PUBLISH_MANIFEST_PATH} is reserved for Lemma publishing."
            )
        if not isinstance(content, bytes):
            raise BundleInvalidError(f"Published file '{path}' is not binary content.")

        source = {
            "size": len(content),
            "sha256": _sha256(content),
            "parts": [path],
        }
        if len(content) > chunk_threshold_bytes:
            parts = [
                content[offset : offset + chunk_threshold_bytes]
                for offset in range(0, len(content), chunk_threshold_bytes)
            ]
            count = len(parts)
            paths: list[str] = []
            for index, part in enumerate(parts, start=1):
                part_path = f"{path}.chunk{index:04d}of{count:04d}"
                physical[part_path] = part
                generated[part_path] = {
                    "size": len(part),
                    "sha256": _sha256(part),
                }
                paths.append(part_path)
            source["parts"] = paths
            chunks[path] = dict(source)
        else:
            physical[path] = content
            generated[path] = {
                "size": len(content),
                "sha256": _sha256(content),
            }
        sources[path] = source

    manifest: dict[str, Any] = {
        "format_version": PUBLISH_MANIFEST_FORMAT_VERSION,
        "publish_id": publish_id,
        "complete": True,
        "generated_files": generated,
        "sources": sources,
        "chunks": chunks,
    }
    physical[PUBLISH_MANIFEST_PATH] = json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    return physical, manifest


def parse_publish_manifest(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleInvalidError("The GitHub publish manifest is malformed.") from exc
    if not isinstance(value, dict):
        raise BundleInvalidError("The GitHub publish manifest is malformed.")
    if value.get("format_version") != PUBLISH_MANIFEST_FORMAT_VERSION:
        raise BundleInvalidError("The GitHub publish manifest version is unsupported.")
    if value.get("complete") is not True:
        raise BundleInvalidError("The GitHub publish did not complete.")
    for key in ("publish_id", "generated_files", "sources", "chunks"):
        if key not in value:
            raise BundleInvalidError(f"The GitHub publish manifest is missing '{key}'.")
    if not isinstance(value["publish_id"], str) or not value["publish_id"]:
        raise BundleInvalidError("The GitHub publish manifest has no publish id.")
    if not all(
        isinstance(value[key], dict) for key in ("generated_files", "sources", "chunks")
    ):
        raise BundleInvalidError("The GitHub publish manifest is malformed.")
    return value


def manifest_managed_paths(manifest: dict[str, Any]) -> set[str]:
    generated = manifest.get("generated_files")
    if not isinstance(generated, dict):
        return set()
    return {_safe_path(path) for path in generated} | {PUBLISH_MANIFEST_PATH}


def manifest_publish_id(manifest: dict[str, Any] | None) -> str | None:
    if not manifest:
        return None
    value = manifest.get("publish_id")
    return value if isinstance(value, str) else None


def _expected_metadata(value: object, *, path: str) -> tuple[int, str]:
    if not isinstance(value, dict):
        raise BundleInvalidError(
            f"The GitHub publish manifest metadata for '{path}' is malformed."
        )
    size = value.get("size")
    digest = value.get("sha256")
    if not isinstance(size, int) or size < 0:
        raise BundleInvalidError(
            f"The GitHub publish manifest size for '{path}' is malformed."
        )
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise BundleInvalidError(
            f"The GitHub publish manifest hash for '{path}' is malformed."
        )
    return size, digest


def _read_verified(root: Path, path: str, metadata: object) -> bytes:
    expected_size, expected_hash = _expected_metadata(metadata, path=path)
    candidate = root.joinpath(*PurePosixPath(path).parts)
    if not candidate.is_file():
        raise BundleInvalidError(f"Published bundle file '{path}' is missing.")
    data = candidate.read_bytes()
    if len(data) != expected_size or _sha256(data) != expected_hash:
        raise BundleInvalidError(
            f"Published bundle file '{path}' failed its integrity check."
        )
    return data


def _verified_generated_files(
    bundle_root: Path,
    generated: dict[str, object],
) -> dict[str, bytes]:
    verified: dict[str, bytes] = {}
    for raw_path, metadata in generated.items():
        path = _safe_path(raw_path)
        verified[path] = _read_verified(bundle_root, path, metadata)
    return verified


def _validated_chunk_parts(
    *,
    source: str,
    metadata: object,
    verified: dict[str, bytes],
) -> list[str]:
    if not isinstance(metadata, dict):
        raise BundleInvalidError(f"Chunk metadata for '{source}' is malformed.")
    parts = metadata.get("parts")
    if not isinstance(parts, list) or len(parts) < 2:
        raise BundleInvalidError(f"Chunk list for '{source}' is malformed.")
    safe_parts = [_safe_path(part) for part in parts]
    if len(set(safe_parts)) != len(safe_parts):
        raise BundleInvalidError(f"Chunk list for '{source}' contains duplicates.")

    expected_count = len(safe_parts)
    for expected_index, part_path in enumerate(safe_parts, start=1):
        match = _CHUNK_SUFFIX_RE.search(part_path)
        valid_name = (
            match is not None
            and int(match.group("index")) == expected_index
            and int(match.group("count")) == expected_count
            and part_path[: match.start()] == source
        )
        if not valid_name:
            raise BundleInvalidError(f"Chunk ordering for '{source}' is malformed.")
        if part_path not in verified:
            raise BundleInvalidError(
                f"Chunk '{part_path}' is not declared as a generated file."
            )
    return safe_parts


def _reassemble_source(
    *,
    bundle_root: Path,
    raw_source: object,
    metadata: object,
    verified: dict[str, bytes],
) -> set[str]:
    source = _safe_path(raw_source)
    safe_parts = _validated_chunk_parts(
        source=source,
        metadata=metadata,
        verified=verified,
    )
    combined = b"".join(verified[part] for part in safe_parts)
    expected_size, expected_hash = _expected_metadata(metadata, path=source)
    if len(combined) != expected_size or _sha256(combined) != expected_hash:
        raise BundleInvalidError(
            f"Reassembled published file '{source}' failed its integrity check."
        )

    destination = bundle_root.joinpath(*PurePosixPath(source).parts)
    if destination.exists() and source not in verified:
        raise BundleInvalidError(
            f"Published bundle contains both '{source}' and its chunks."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(combined)
    for part in safe_parts:
        bundle_root.joinpath(*PurePosixPath(part).parts).unlink()
    return set(safe_parts)


def prepare_published_bundle(bundle_root: Path) -> bool:
    """Validate and reassemble a manifest-backed repository in place.

    Returns ``False`` for legacy repositories without a manifest.  All writes
    happen inside the import's temporary extraction directory.
    """
    manifest_path = bundle_root.joinpath(*PurePosixPath(PUBLISH_MANIFEST_PATH).parts)
    if not manifest_path.is_file():
        return False

    manifest = parse_publish_manifest(manifest_path.read_bytes())
    generated = manifest["generated_files"]
    chunks = manifest["chunks"]

    verified = _verified_generated_files(bundle_root, generated)
    declared_parts: set[str] = set()
    for raw_source, metadata in chunks.items():
        declared_parts.update(
            _reassemble_source(
                bundle_root=bundle_root,
                raw_source=raw_source,
                metadata=metadata,
                verified=verified,
            )
        )

    unexpected_chunks = {
        path.relative_to(bundle_root).as_posix()
        for path in bundle_root.rglob("*")
        if path.is_file() and _CHUNK_SUFFIX_RE.search(path.name)
    } - declared_parts
    if unexpected_chunks:
        raise BundleInvalidError(
            "Published bundle contains undeclared chunk files.",
            details={"paths": sorted(unexpected_chunks)[:20]},
        )
    return True
