from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_stack.output import AdminError
from lemma_stack.release import manifest as m


def sample(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "version": "1.4.0",
        "min_admin_version": "0.1.0",
        "images": {
            "backend": {"ref": "ghcr.io/lemma-work/lemma-backend:v1.4.0", "digest": "sha256:aa"},
            "frontend": {"ref": "ghcr.io/lemma-work/lemma-frontend:v1.4.0"},
            # Retained in published manifests only for old installer versions.
            "workspace": {"ref": "ghcr.io/lemma-work/lemma-workspace:v1.4.0"},
            "function": {"ref": "ghcr.io/lemma-work/lemma-function:v1.4.0"},
        },
        "infra": {"postgres": "docker.io/pgvector/pgvector:0.8.0-pg18"},
    }
    data.update(overrides)
    return data


def test_parse_and_pull_refs():
    manifest = m.parse(sample())
    assert manifest.version == "1.4.0"
    assert manifest.image("backend").pull_ref == "ghcr.io/lemma-work/lemma-backend:v1.4.0@sha256:aa"
    assert manifest.image("frontend").pull_ref == "ghcr.io/lemma-work/lemma-frontend:v1.4.0"
    # infra falls back to built-in defaults when missing from the manifest
    assert manifest.infra_image("postgres") == "docker.io/pgvector/pgvector:0.8.0-pg18"
    assert manifest.infra_image("redis") == m.DEFAULT_INFRA_IMAGES["redis"]


def test_infrastructure_images_accept_release_digests():
    data = sample(
        infra={
            "postgres": {
                "ref": "docker.io/pgvector/pgvector:0.8.3-pg18",
                "digest": "sha256:infra",
            }
        }
    )

    assert m.parse(data).infra_image("postgres").endswith("@sha256:infra")


def test_release_workflow_uses_the_stack_supertokens_version():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/release-local-images.yml"
    ).read_text(encoding="utf-8")
    assert f"SUPERTOKENS_IMAGE: {m.DEFAULT_INFRA_IMAGES['supertokens']}" in workflow
    assert '"linux/amd64", "linux/arm64"' in workflow
    assert 'digests[name]["platforms"][platform]' in workflow


def test_pull_refs_contain_no_separate_manager_or_document_service():
    manifest = m.parse(sample())
    refs = manifest.all_pull_refs()
    assert not any("kreuzberg" in ref for ref in refs)
    assert any("lemma-workspace" in ref for ref in refs)
    assert any("lemma-function" in ref for ref in refs)


def test_native_host_start_pulls_only_infrastructure():
    manifest = m.parse(sample())
    refs = manifest.infra_pull_refs()

    assert refs == [
        manifest.infra_image("postgres"),
        manifest.infra_image("redis"),
        manifest.infra_image("supertokens"),
    ]
    assert not any("lemma-backend" in ref for ref in refs)
    assert not any("lemma-frontend" in ref for ref in refs)


def test_missing_image_rejected():
    data = sample()
    del data["images"]["function"]
    with pytest.raises(AdminError, match="function"):
        m.parse(data)


def test_wrong_schema_rejected():
    with pytest.raises(AdminError, match="schema_version"):
        m.parse(sample(schema_version=99))


def test_schema_one_accepts_additive_native_host_pack():
    data = sample(
        host_packs={
            "aarch64-apple-darwin": {
                "url": "https://example.test/lemma-host-pack.zip",
                "sha256": "a" * 64,
                "size": 1234,
                "format": "zip",
            }
        },
    )

    pack = m.parse(data).host_pack("aarch64-apple-darwin")

    assert pack.sha256 == "a" * 64
    assert pack.size == 1234


def test_native_host_pack_is_optional_but_must_be_valid_when_present():
    assert m.parse(sample()).host_packs == {}
    with pytest.raises(AdminError, match="invalid host pack"):
        m.parse(
            sample(
                host_packs={
                    "bad": {
                        "url": "http://insecure.test/pack.zip",
                        "sha256": "nope",
                        "size": 0,
                    }
                },
            )
        )


def test_managed_guest_runtime_is_additive_and_verified():
    data = sample(
        guest_runtimes={
            "macos-aarch64": {
                "url": "https://example.test/lemma-guest-runtime.zip",
                "sha256": "b" * 64,
                "size": 5678,
                "format": "zip",
            }
        }
    )

    runtime = m.parse(data).guest_runtime("macos-aarch64")
    assert runtime.sha256 == "b" * 64
    assert runtime.size == 5678


def test_min_admin_version_gate():
    with pytest.raises(AdminError, match="requires lemma-stack"):
        m.parse(sample(min_admin_version="999.0.0"))


def test_pin_archives_previous_release(paths):
    first = m.parse(sample(version="1.0.0"))
    second = m.parse(sample(version="1.1.0"))
    m.pin(paths, first)
    m.pin(paths, second)
    assert m.load_pinned(paths).version == "1.1.0"
    archived = json.loads((paths.releases_dir / "lemma-1.0.0.json").read_text())
    assert archived["version"] == "1.0.0"


def test_release_url_resolution(monkeypatch):
    monkeypatch.delenv("LEMMA_STACK_RELEASE_URL", raising=False)
    monkeypatch.delenv("LEMMA_STACK_RELEASE_BASE_URL", raising=False)
    assert m.release_url("stable").endswith("/releases/latest/download/lemma-local.json")
    assert m.release_url("1.4.0").endswith("/releases/download/v1.4.0/lemma-local.json")
    assert m.release_url("v1.4.0").endswith("/releases/download/v1.4.0/lemma-local.json")
    monkeypatch.setenv("LEMMA_STACK_RELEASE_URL", "file:///tmp/x.json")
    assert m.release_url("stable") == "file:///tmp/x.json"
