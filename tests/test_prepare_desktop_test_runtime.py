from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.prepare_desktop_test_runtime import bundle_artifacts, localize_artifacts


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


def _two_platform_manifest(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    """A workflow manifest as published: every platform, in one file."""
    mac_host = tmp_path / "host-packs/lemma-host-pack-aarch64-apple-darwin.zip"
    win_host = tmp_path / "host-packs/lemma-host-pack-x86_64-pc-windows-msvc.zip"
    mac_guest = tmp_path / "guest-runtimes/lemma-guest-runtime-macos-aarch64.zip"
    win_guest = tmp_path / "guest-runtimes/lemma-guest-runtime-windows-x86_64.zip"
    for path, payload in (
        (mac_host, b"mac-host"),
        (win_host, b"win-host"),
        (mac_guest, b"mac-guest"),
        (win_guest, b"win-guest"),
    ):
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(payload)
    manifest = {
        "host_packs": {
            "aarch64-apple-darwin": _metadata(mac_host),
            "x86_64-pc-windows-msvc": _metadata(win_host),
        },
        "guest_runtimes": {
            "macos-aarch64": _metadata(mac_guest),
            "windows-x86_64": _metadata(win_guest),
        },
    }
    return manifest, mac_host, mac_guest


def test_bundles_one_platform_as_app_resources(tmp_path: Path) -> None:
    manifest, mac_host, mac_guest = _two_platform_manifest(tmp_path)
    stage = tmp_path / "bundled"

    bundled = bundle_artifacts(
        manifest,
        tmp_path,
        "aarch64-apple-darwin",
        "macos-aarch64",
        stage,
    )

    # A bundled build carries its payload, so the manifest names a resource and
    # no URL at all -- there is nothing left to download or to gate.
    host = bundled["host_packs"]["aarch64-apple-darwin"]
    assert host["resource"] == "host-runtime.zip"
    assert host["url"] is None
    assert (stage / "host-runtime.zip").read_bytes() == mac_host.read_bytes()
    assert (stage / "guest-runtime.zip").read_bytes() == mac_guest.read_bytes()

    # The other platform is dropped rather than carried: a Windows guest runtime
    # inside a macOS DMG is several hundred megabytes that can never be used.
    assert list(bundled["host_packs"]) == ["aarch64-apple-darwin"]
    assert list(bundled["guest_runtimes"]) == ["macos-aarch64"]

    # And the caller's manifest is untouched, so one download can stage both
    # platforms in turn.
    assert set(manifest["host_packs"]) == {
        "aarch64-apple-darwin",
        "x86_64-pc-windows-msvc",
    }


def test_bundling_verifies_the_platform_it_packages(tmp_path: Path) -> None:
    manifest, _mac_host, _mac_guest = _two_platform_manifest(tmp_path)
    manifest["host_packs"]["aarch64-apple-darwin"]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="does not match"):
        bundle_artifacts(
            manifest,
            tmp_path,
            "aarch64-apple-darwin",
            "macos-aarch64",
            tmp_path / "bundled",
        )


def test_bundling_rejects_a_platform_the_manifest_does_not_carry(
    tmp_path: Path,
) -> None:
    manifest, _mac_host, _mac_guest = _two_platform_manifest(tmp_path)

    with pytest.raises(ValueError, match="no host_packs.aarch64-unknown-linux-gnu"):
        bundle_artifacts(
            manifest,
            tmp_path,
            "aarch64-unknown-linux-gnu",
            "macos-aarch64",
            tmp_path / "bundled",
        )
