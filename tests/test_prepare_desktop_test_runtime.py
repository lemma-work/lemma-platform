from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.prepare_desktop_test_runtime import localize_artifacts


def _metadata(path: Path) -> dict[str, object]:
    return {
        "url": "https://example.invalid/artifact.zip",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
        "format": "zip",
    }


def test_localizes_only_verified_workflow_artifacts(tmp_path: Path) -> None:
    host = tmp_path / "host-packs/lemma-host-pack-aarch64-apple-darwin.zip"
    guest = tmp_path / "guest-runtimes/lemma-guest-runtime-macos-aarch64.zip"
    host.parent.mkdir()
    guest.parent.mkdir()
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")
    manifest = {
        "host_packs": {"aarch64-apple-darwin": _metadata(host)},
        "guest_runtimes": {"macos-aarch64": _metadata(guest)},
    }

    localized = localize_artifacts(manifest, tmp_path)

    assert localized["host_packs"]["aarch64-apple-darwin"]["url"] == host.as_uri()
    assert localized["guest_runtimes"]["macos-aarch64"]["url"] == guest.as_uri()
    assert manifest["host_packs"]["aarch64-apple-darwin"]["url"].startswith(
        "https://"
    )


def test_rejects_tampered_download(tmp_path: Path) -> None:
    host = tmp_path / "lemma-host-pack-aarch64-apple-darwin.zip"
    guest = tmp_path / "lemma-guest-runtime-macos-aarch64.zip"
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")
    host_metadata = _metadata(host)
    host_metadata["sha256"] = "0" * 64
    manifest = {
        "host_packs": {"aarch64-apple-darwin": host_metadata},
        "guest_runtimes": {"macos-aarch64": _metadata(guest)},
    }

    with pytest.raises(ValueError, match="does not match"):
        localize_artifacts(manifest, tmp_path)
