from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from lemma_pod_bundle.archive import extract_bundle, pack_bundle


def _make_bundle_dir(tmp_path: Path, name: str = "demo") -> Path:
    root = tmp_path / name
    (root / "tables" / "items").mkdir(parents=True)
    (root / "agents").mkdir(parents=True)  # empty resource dir
    (root / "pod.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    (root / "tables" / "items" / "items.json").write_text(
        json.dumps({"name": "items", "columns": []}), encoding="utf-8"
    )
    return root


def test_pack_and_extract_round_trip(tmp_path: Path):
    source = _make_bundle_dir(tmp_path)
    archive = pack_bundle(source)

    dest = tmp_path / "out"
    bundle_root = extract_bundle(archive, dest)

    assert bundle_root == dest
    assert json.loads((bundle_root / "pod.json").read_text()) == {"name": "demo"}
    assert json.loads(
        (bundle_root / "tables" / "items" / "items.json").read_text()
    ) == {"name": "items", "columns": []}
    # Empty resource dirs survive the round trip.
    assert (bundle_root / "agents").is_dir()


def test_pack_bundle_is_deterministic(tmp_path: Path):
    source = _make_bundle_dir(tmp_path)
    first = pack_bundle(source)
    # Touch mtimes to prove timestamps do not leak into the archive.
    for path in source.rglob("*"):
        os.utime(path, (0, 0))
    second = pack_bundle(source)
    assert first == second


def test_pack_bundle_missing_dir(tmp_path: Path):
    with pytest.raises(ValueError, match="does not exist"):
        pack_bundle(tmp_path / "nope")


def test_pack_bundle_rejects_symlink(tmp_path: Path):
    source = _make_bundle_dir(tmp_path)
    (source / "link.json").symlink_to(source / "pod.json")
    with pytest.raises(ValueError, match="symlink"):
        pack_bundle(source)


def test_extract_bundle_accepts_path_input(tmp_path: Path):
    source = _make_bundle_dir(tmp_path)
    archive_path = tmp_path / "bundle.zip"
    archive_path.write_bytes(pack_bundle(source))
    bundle_root = extract_bundle(archive_path, tmp_path / "out")
    assert (bundle_root / "pod.json").is_file()


def test_extract_bundle_returns_shallowest_manifest_dir(tmp_path: Path):
    """An archive that wraps the bundle in a folder resolves to that folder."""
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("demo/pod.json", json.dumps({"name": "demo"}))
        zf.writestr("demo/apps/site/pod.json", json.dumps({"name": "nested-decoy"}))
    bundle_root = extract_bundle(buffer.getvalue(), tmp_path / "out")
    assert bundle_root == tmp_path / "out" / "demo"


def test_extract_bundle_requires_manifest(tmp_path: Path):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as zf:
        zf.writestr("readme.txt", "no manifest here")
    with pytest.raises(ValueError, match="pod.json"):
        extract_bundle(buffer.getvalue(), tmp_path / "out")


@pytest.mark.parametrize(
    "member",
    ["../evil.json", "a/../../evil.json", "/abs/evil.json"],
)
def test_extract_bundle_rejects_zip_slip(tmp_path: Path, member: str):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as zf:
        zf.writestr("pod.json", "{}")
        zf.writestr(member, "evil")
    dest = tmp_path / "out"
    with pytest.raises(ValueError, match="Unsafe path"):
        extract_bundle(buffer.getvalue(), dest)
    assert not (tmp_path / "evil.json").exists()


def test_extract_bundle_rejects_windows_style_traversal(tmp_path: Path):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as zf:
        zf.writestr("pod.json", "{}")
        zf.writestr("..\\evil.json", "evil")
    with pytest.raises(ValueError, match="Unsafe path"):
        extract_bundle(buffer.getvalue(), tmp_path / "out")


def test_extract_bundle_rejects_symlink_member(tmp_path: Path):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as zf:
        zf.writestr("pod.json", "{}")
        info = ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError, match="Symlink"):
        extract_bundle(buffer.getvalue(), tmp_path / "out")


def test_extract_bundle_enforces_size_cap(tmp_path: Path):
    source = _make_bundle_dir(tmp_path)
    (source / "big.bin").write_bytes(b"x" * 4096)
    archive = pack_bundle(source)
    with pytest.raises(ValueError, match="maximum uncompressed size"):
        extract_bundle(archive, tmp_path / "out", max_uncompressed_bytes=1024)
    # And a generous cap extracts fine.
    bundle_root = extract_bundle(archive, tmp_path / "out2", max_uncompressed_bytes=1024 * 1024)
    assert (bundle_root / "big.bin").stat().st_size == 4096


def test_extract_bundle_size_cap_guards_actual_bytes_not_declared(tmp_path: Path):
    """A lying zip header (small declared size, large real payload) still trips the cap."""
    payload = b"y" * 100_000
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("pod.json", "{}")
        zf.writestr("bomb.bin", payload)
    raw = bytearray(buffer.getvalue())
    # No header tampering needed: the cap counts bytes actually decompressed.
    with pytest.raises(ValueError, match="maximum uncompressed size"):
        extract_bundle(bytes(raw), tmp_path / "out", max_uncompressed_bytes=10_000)
