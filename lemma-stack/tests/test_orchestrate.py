from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lemma_stack import orchestrate
from lemma_stack.config import store
from lemma_stack.output import AdminError
from lemma_stack.release import manifest as release_manifest


def _manifest(version: str):
    return release_manifest.parse(
        {
            "schema_version": 1,
            "version": version,
            "min_admin_version": "0",
            "images": {
                name: f"ghcr.io/lemma-work/lemma-{name}:v{version}"
                for name in (
                    "backend",
                    "frontend",
                    "workspace",
                    "function",
                )
            },
        }
    )


def test_desktop_stable_channel_refreshes_an_existing_pin(paths, monkeypatch):
    config = store.new_document()
    release_manifest.pin(paths, _manifest("0.5.1"))
    latest = _manifest("0.6.1")
    seen: list[str] = []

    def fetch(channel: str):
        seen.append(channel)
        return latest

    monkeypatch.setattr(orchestrate.release_manifest, "fetch", fetch)

    resolved = orchestrate.resolve_manifest(config, paths, prefer_pinned=True)

    assert resolved.version == "0.6.1"
    assert seen == ["stable"]


def test_desktop_explicit_version_channel_keeps_its_pin(paths, monkeypatch):
    config = store.new_document()
    config["install"]["channel"] = "0.5.1"
    release_manifest.pin(paths, _manifest("0.5.1"))

    def unexpected_fetch(_channel: str):
        raise AssertionError("explicit version pins must not refresh")

    monkeypatch.setattr(orchestrate.release_manifest, "fetch", unexpected_fetch)

    resolved = orchestrate.resolve_manifest(config, paths, prefer_pinned=True)

    assert resolved.version == "0.5.1"


def test_desktop_stable_channel_falls_back_to_pin_offline(paths, monkeypatch, capsys):
    config = store.new_document()
    release_manifest.pin(paths, _manifest("0.5.1"))

    def unavailable(_channel: str):
        raise AdminError("offline")

    monkeypatch.setattr(orchestrate.release_manifest, "fetch", unavailable)

    resolved = orchestrate.resolve_manifest(config, paths, prefer_pinned=True)

    assert resolved.version == "0.5.1"
    assert "continuing with pinned Lemma 0.5.1" in capsys.readouterr().err


def test_a_failed_migration_leaves_the_recorded_release_alone(paths, monkeypatch):
    """A version that did not install must not be recorded as installed.

    `lifecycle.up` runs `alembic upgrade head`. Pinning before it meant a failed
    migration left `release.json` claiming the new version while the install was
    still on the old schema — and every later command, including the comparison
    the next upgrade makes, believed it.
    """
    config = store.new_document()
    release_manifest.pin(paths, _manifest("0.5.1"))

    runtime = SimpleNamespace(socket_path=lambda: "/tmp/does-not-matter.sock")
    monkeypatch.setattr(orchestrate.detect, "ensure_ready", lambda provider: runtime)
    monkeypatch.setattr(orchestrate.images, "pull_release", lambda *args, **kwargs: None)

    def _migration_fails(*args, **kwargs):
        raise AdminError("alembic upgrade head exited 1")

    monkeypatch.setattr(orchestrate.lifecycle, "up", _migration_fails)

    with pytest.raises(AdminError):
        orchestrate.bring_up(paths, config, manifest=_manifest("0.6.0"), provider="docker")

    recorded = json.loads(paths.release_file.read_text(encoding="utf-8"))
    assert recorded["version"] == "0.5.1", (
        "a release whose migration failed must not be recorded as installed"
    )
