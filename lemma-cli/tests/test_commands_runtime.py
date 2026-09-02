from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import runtime

runner = CliRunner()

_PROFILES = [
    {"id": "prof-1", "name": "Default", "source": "AGENT_HOST"},
    {"id": "prof-2", "name": "Fireworks", "source": "OPENAI_COMPATIBLE"},
]


def _make_client_and_captured():
    captured = {}

    class FakeOrgRuntime:
        def profiles(self):
            captured["profiles_called"] = True
            return {"items": list(_PROFILES)}

        def create_profile(self, payload):
            captured["create"] = payload
            return {"id": "prof-3", "name": payload.get("name")}

    class FakeClient:
        def __init__(self):
            self.org_runtime = FakeOrgRuntime()

    return FakeClient(), captured


def _patch(monkeypatch, client):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(runtime, "run_with_client", lambda ctx, fn: fn(client, state))


def test_runtime_profiles_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["runtime", "profiles", "list", "--json"])

    assert result.exit_code == 0, result.stdout
    assert captured.get("profiles_called") is True
    payload = json.loads(result.stdout)
    names = [item.get("name") for item in payload.get("items", [])]
    assert "Default" in names


def test_runtime_profiles_get_by_name(monkeypatch):
    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["runtime", "profiles", "get", "Fireworks"])

    assert result.exit_code == 0, result.stdout
    assert "Fireworks" in result.stdout


def test_runtime_profiles_get_by_id(monkeypatch):
    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["runtime", "profiles", "get", "prof-1"])

    assert result.exit_code == 0, result.stdout
    assert "Default" in result.stdout


def test_runtime_profiles_get_not_found_fails(monkeypatch):
    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["runtime", "profiles", "get", "nope"])

    assert result.exit_code != 0
    assert "not found" in result.stderr.lower() or "nope" in result.stderr


def test_runtime_profiles_create_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        [
            "runtime",
            "profiles",
            "create",
            "OPENAI_COMPATIBLE",
            "--name",
            "Fireworks",
            "--base-url",
            "https://api.fireworks.ai",
            "--api-key",
            "fw-xxx",
            "--default-model",
            "accounts/fireworks/models/glm-5p2",
            "--model",
            "m1",
            "--model",
            "m2",
        ],
    )

    assert result.exit_code == 0, result.stdout
    sent = captured["create"]
    assert sent["source"] == "OPENAI_COMPATIBLE"
    assert sent["name"] == "Fireworks"
    assert sent["base_url"] == "https://api.fireworks.ai"
    assert sent["api_key"] == "fw-xxx"
    assert sent["default_model_name"] == "accounts/fireworks/models/glm-5p2"
    assert sent["model_names"] == ["m1", "m2"]


def test_runtime_profiles_create_binds_an_agent_host_harness(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        [
            "runtime",
            "profiles",
            "create",
            "agent_host",
            "--name",
            "OpenCode on my laptop",
            "--harness-id",
            "0199aa11-2233-7444-8555-666677778888",
        ],
    )

    assert result.exit_code == 0, result.stdout
    sent = captured["create"]
    assert sent["source"] == "AGENT_HOST"
    assert sent["harness_id"] == "0199aa11-2233-7444-8555-666677778888"
