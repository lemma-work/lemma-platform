from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import runtime

runner = CliRunner()

_PROFILES = [
    {"id": "prof-1", "name": "Codex", "runtime_type": "HARNESS"},
    {
        "id": "prof-2",
        "name": "Fireworks",
        "runtime_type": "OPENAI_COMPATIBLE",
    },
]


def make_client():
    captured = {}

    class FakeOrgRuntime:
        def profiles(self):
            captured["list"] = True
            return {"items": list(_PROFILES)}

        def get_profile(self, profile_id):
            captured["get"] = profile_id
            return next(item for item in _PROFILES if item["id"] == profile_id)

        def create_profile(self, payload):
            captured["create"] = payload
            return {"id": "prof-3", **payload}

        def update_profile(self, profile_id, payload):
            captured["update"] = (profile_id, payload)
            return {"id": profile_id, **payload}

        def refresh_profile(self, profile_id):
            captured["refresh"] = profile_id
            return {"id": profile_id}

        def delete_profile(self, profile_id):
            captured["delete"] = profile_id
            return {"id": profile_id, "status": "DISABLED"}

    return SimpleNamespace(org_runtime=FakeOrgRuntime()), captured


def patch_client(monkeypatch, client):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(runtime, "run_with_client", lambda _ctx, fn: fn(client, state))


def test_runtime_profiles_list_and_get(monkeypatch):
    client, captured = make_client()
    patch_client(monkeypatch, client)
    listed = runner.invoke(app, ["runtime", "profiles", "list", "--json"])
    fetched = runner.invoke(app, ["runtime", "profiles", "get", "prof-2", "--json"])
    assert listed.exit_code == 0, listed.stdout
    assert fetched.exit_code == 0, fetched.stdout
    assert captured["list"] is True
    assert captured["get"] == "prof-2"


def test_runtime_profiles_create_provider_uses_runtime_type(monkeypatch):
    client, captured = make_client()
    patch_client(monkeypatch, client)
    result = runner.invoke(
        app,
        [
            "runtime",
            "profiles",
            "create",
            "openai_compatible",
            "--name",
            "Fireworks",
            "--base-url",
            "https://api.fireworks.ai",
            "--api-key",
            "fw-xxx",
            "--default-model",
            "m2",
            "--model",
            "m1",
            "--model",
            "m2",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["create"] == {
        "runtime_type": "OPENAI_COMPATIBLE",
        "scope": "PERSONAL",
        "name": "Fireworks",
        "base_url": "https://api.fireworks.ai",
        "api_key": "fw-xxx",
        "default_model_name": "m2",
        "model_names": ["m1", "m2"],
    }


def test_runtime_profiles_create_harness_has_no_host_type(monkeypatch):
    client, captured = make_client()
    patch_client(monkeypatch, client)
    result = runner.invoke(
        app,
        [
            "runtime",
            "profiles",
            "create",
            "harness",
            "--name",
            "Codex",
            "--harness-id",
            "00000000-0000-4000-8000-000000000001",
            "--harness-revision",
            "revision-1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["create"]["runtime_type"] == "HARNESS"
    assert "daemon_id" not in captured["create"]


def test_runtime_profiles_update_refresh_and_delete(monkeypatch):
    client, captured = make_client()
    patch_client(monkeypatch, client)
    updated = runner.invoke(
        app,
        [
            "runtime",
            "profiles",
            "update",
            "prof-2",
            "--data",
            json.dumps({"default_model_name": "m2"}),
        ],
    )
    refreshed = runner.invoke(app, ["runtime", "profiles", "refresh", "prof-2"])
    deleted = runner.invoke(app, ["runtime", "profiles", "delete", "prof-2"])
    assert updated.exit_code == refreshed.exit_code == deleted.exit_code == 0
    assert captured["update"] == ("prof-2", {"default_model_name": "m2"})
    assert captured["refresh"] == "prof-2"
    assert captured["delete"] == "prof-2"
