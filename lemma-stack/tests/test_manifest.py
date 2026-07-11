from __future__ import annotations

import json
import re
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
            "agentbox": "ghcr.io/lemma-work/lemma-agentbox:v1.4.0",
            "agentbox_runtime": {"ref": "ghcr.io/lemma-work/lemma-agentbox-runtime:v1.4.0"},
        },
        "infra": {"postgres": "docker.io/pgvector/pgvector:0.8.0-pg16"},
    }
    data.update(overrides)
    return data


def test_parse_and_pull_refs():
    manifest = m.parse(sample())
    assert manifest.version == "1.4.0"
    assert manifest.image("backend").pull_ref == "ghcr.io/lemma-work/lemma-backend:v1.4.0@sha256:aa"
    assert manifest.image("frontend").pull_ref == "ghcr.io/lemma-work/lemma-frontend:v1.4.0"
    # infra falls back to built-in defaults when missing from the manifest
    assert manifest.infra_image("postgres") == "docker.io/pgvector/pgvector:0.8.0-pg16"
    assert manifest.infra_image("redis") == m.DEFAULT_INFRA_IMAGES["redis"]


def test_release_workflow_uses_the_stack_supertokens_version():
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github/workflows/release-local-images.yml"
    ).read_text(encoding="utf-8")
    match = re.search(r'"supertokens": "([^"]+)"', workflow)

    assert match is not None
    assert match.group(1) == m.DEFAULT_INFRA_IMAGES["supertokens"]


def test_kreuzberg_pull_only_when_enabled():
    manifest = m.parse(sample())
    refs_without = manifest.all_pull_refs(kreuzberg=False)
    refs_with = manifest.all_pull_refs(kreuzberg=True)
    assert not any("kreuzberg" in ref for ref in refs_without)
    assert any("kreuzberg" in ref for ref in refs_with)


def test_missing_image_rejected():
    data = sample()
    del data["images"]["agentbox_runtime"]
    with pytest.raises(AdminError, match="agentbox_runtime"):
        m.parse(data)


def test_wrong_schema_rejected():
    with pytest.raises(AdminError, match="schema_version"):
        m.parse(sample(schema_version=99))


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
