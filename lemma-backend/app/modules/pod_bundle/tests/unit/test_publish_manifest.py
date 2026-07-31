from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.infrastructure.publish_manifest import (
    PUBLISH_MANIFEST_PATH,
    build_publish_layout,
    prepare_published_bundle,
)


def _write_layout(root: Path, files: dict[str, bytes]) -> None:
    for path, content in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def test_legacy_repository_without_manifest_is_supported(tmp_path: Path):
    (tmp_path / "pod.json").write_text("{}", encoding="utf-8")
    assert prepare_published_bundle(tmp_path) is False


def test_manifest_reassembles_large_file_byte_for_byte(tmp_path: Path):
    original = bytes(range(251)) * 2_000
    physical, _ = build_publish_layout(
        {"pod.json": b"{}", "apps/demo/dist.zip": original},
        publish_id="publish-1",
    )
    _write_layout(tmp_path, physical)

    assert prepare_published_bundle(tmp_path) is True
    assert (tmp_path / "apps/demo/dist.zip").read_bytes() == original


def test_manifest_rejects_missing_or_tampered_chunks(tmp_path: Path):
    original = b"x" * 400_000
    physical, _ = build_publish_layout(
        {"pod.json": b"{}", "apps/demo/dist.zip": original},
        publish_id="publish-1",
    )
    chunk_paths = sorted(path for path in physical if ".chunk" in path)

    missing_root = tmp_path / "missing"
    _write_layout(missing_root, physical)
    (missing_root / chunk_paths[0]).unlink()
    with pytest.raises(BundleInvalidError, match="missing"):
        prepare_published_bundle(missing_root)

    tampered_root = tmp_path / "tampered"
    _write_layout(tampered_root, physical)
    (tampered_root / chunk_paths[0]).write_bytes(b"tampered")
    with pytest.raises(BundleInvalidError, match="integrity"):
        prepare_published_bundle(tampered_root)


def test_manifest_rejects_undeclared_chunk_and_generated_file_tampering(tmp_path: Path):
    physical, _ = build_publish_layout(
        {"pod.json": b'{"name":"safe"}'},
        publish_id="publish-1",
    )
    _write_layout(tmp_path, physical)
    unexpected = tmp_path / "pod.json.chunk0001of0001"
    unexpected.write_bytes(b"shadow")
    with pytest.raises(BundleInvalidError, match="undeclared chunk"):
        prepare_published_bundle(tmp_path)

    unexpected.unlink()
    (tmp_path / "pod.json").write_bytes(b'{"name":"tampered"}')
    with pytest.raises(BundleInvalidError, match="integrity"):
        prepare_published_bundle(tmp_path)


def test_manifest_rejects_incomplete_publish(tmp_path: Path):
    physical, _ = build_publish_layout({"pod.json": b"{}"}, publish_id="publish-1")
    manifest_path = Path(PUBLISH_MANIFEST_PATH)
    manifest = physical[manifest_path.as_posix()].replace(
        b'"complete": true',
        b'"complete": false',
    )
    physical[manifest_path.as_posix()] = manifest
    _write_layout(tmp_path, physical)
    with pytest.raises(BundleInvalidError, match="did not complete"):
        prepare_published_bundle(tmp_path)
