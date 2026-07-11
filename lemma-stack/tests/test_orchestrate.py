from __future__ import annotations

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
                for name in ("backend", "frontend", "agentbox", "agentbox_runtime")
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
