"""_resolve_provider must go through select_runtime so a provider that was
never installed gets installed (or fails actionably) instead of dying later
with "CLI not found on PATH". Regression test for the fresh-machine desktop
flow where store.provider()'s podman default skipped installation entirely."""

from __future__ import annotations

import tomlkit

from lemma_stack.runtime import detect
from lemma_stack.supervise import Supervisor


def _resolve(monkeypatch, tmp_path, config, env_provider=None):
    monkeypatch.setenv("LEMMA_STACK_ROOT", str(tmp_path))
    if env_provider is None:
        monkeypatch.delenv("LEMMA_CONTAINER_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("LEMMA_CONTAINER_RUNTIME", env_provider)
    calls = []

    def fake_select(requested, *, assume_yes):
        calls.append(requested)
        return "podman"

    monkeypatch.setattr(detect, "select_runtime", fake_select)
    supervisor = Supervisor(dry_run=True)
    provider = supervisor._resolve_provider(config)
    return provider, calls


def test_fresh_config_resolves_via_auto(monkeypatch, tmp_path):
    provider, calls = _resolve(monkeypatch, tmp_path, tomlkit.document())
    assert calls == ["auto"]
    assert provider == "podman"


def test_persisted_choice_is_revalidated(monkeypatch, tmp_path):
    config = tomlkit.document()
    config["runtime"] = {"provider": "podman"}
    provider, calls = _resolve(monkeypatch, tmp_path, config)
    assert calls == ["podman"]
    assert provider == "podman"


def test_env_override_wins(monkeypatch, tmp_path):
    config = tomlkit.document()
    config["runtime"] = {"provider": "podman"}
    provider, calls = _resolve(monkeypatch, tmp_path, config, env_provider="docker")
    assert calls == ["docker"]
