from __future__ import annotations

import json

from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import connectors, surfaces


runner = CliRunner()


# ---------------------------------------------------------------------------
# Connectors fake client helpers
# ---------------------------------------------------------------------------


def _make_connectors_client_and_captured():
    captured = {}

    class FakeApps:
        def list(self, *, limit=100):
            captured["connectors_limit"] = limit
            return {"items": [{"id": "conn-1", "name": "gmail", "title": "Gmail"}]}

    class FakeConnectors:
        apps = FakeApps()

    class FakeClient:
        connectors = FakeConnectors()

    return FakeClient(), captured


def _patch_connectors(monkeypatch, client, output="pretty"):
    state = SimpleNamespace(
        config={"defaults": {"org_id": "org-1"}},
        output=output,
        full=False,
    )
    monkeypatch.setattr(
        connectors, "run_with_client", lambda ctx, fn: fn(client, state)
    )


# ---------------------------------------------------------------------------
# Surfaces fake client helpers
# ---------------------------------------------------------------------------


def _make_surfaces_client_and_captured():
    captured = {}

    class FakeSurfaces:
        def list(self, *, limit=100):
            captured["surfaces_limit"] = limit
            return {"items": [{"id": "surf-1", "platform": "SLACK", "name": "slack"}]}

    class FakePod:
        def __init__(self):
            self.surfaces = FakeSurfaces()

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return FakePod()

    return FakeClient(), captured


def _patch_surfaces(monkeypatch, client, output="pretty"):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output=output,
        full=False,
    )
    monkeypatch.setattr(surfaces, "run_with_client", lambda ctx, fn: fn(client, state))


# ---------------------------------------------------------------------------
# Connectors tests
# ---------------------------------------------------------------------------


def test_connectors_list_dispatches_api(monkeypatch):
    client, captured = _make_connectors_client_and_captured()
    _patch_connectors(monkeypatch, client)

    result = runner.invoke(app, ["connectors", "list"])

    assert result.exit_code == 0, result.stdout
    assert "gmail" in result.stdout


def test_connectors_list_json_output(monkeypatch):
    client, captured = _make_connectors_client_and_captured()
    _patch_connectors(monkeypatch, client, output="json")

    result = runner.invoke(app, ["--json", "connectors", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert payload["items"][0]["name"] == "gmail"


# ---------------------------------------------------------------------------
# Surfaces tests
# ---------------------------------------------------------------------------


def test_surfaces_list_dispatches_api(monkeypatch):
    client, captured = _make_surfaces_client_and_captured()
    _patch_surfaces(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "surfaces", "list"])

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "pod-1"


def test_surfaces_list_json_output(monkeypatch):
    client, captured = _make_surfaces_client_and_captured()
    _patch_surfaces(monkeypatch, client, output="json")

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "surfaces", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert payload["items"][0]["platform"] == "SLACK"


# ---------------------------------------------------------------------------
# Managed Telegram bot setup
# ---------------------------------------------------------------------------


def _make_telegram_setup_client_and_captured():
    captured = {}

    class FakeSurfaces:
        def start_telegram_bot_setup(self, payload):
            captured["start_payload"] = payload
            return {
                "setup_id": "setup-1",
                "status": "AWAITING_BOT",
                "launch_url": "https://t.me/LemmaManagerBot?start=setup-1",
                "manager_bot_username": "LemmaManagerBot",
                "expires_at": "2026-08-18T10:00:00Z",
            }

        def get_telegram_bot_setup(self, setup_id):
            captured["status_setup_id"] = setup_id
            return {
                "setup_id": setup_id,
                "status": "COMPLETED",
                "launch_url": "https://t.me/LemmaManagerBot?start=setup-1",
                "manager_bot_username": "LemmaManagerBot",
                "expires_at": "2026-08-18T10:00:00Z",
                "bot_username": "acme_ops_bot",
            }

    class FakePod:
        def __init__(self):
            self.surfaces = FakeSurfaces()

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return FakePod()

    return FakeClient(), captured


def test_telegram_setup_starts_and_returns_launch_url(monkeypatch):
    client, captured = _make_telegram_setup_client_and_captured()
    _patch_surfaces(monkeypatch, client, output="json")

    result = runner.invoke(
        app, ["--json", "--pod", "pod-1", "surfaces", "telegram-setup"]
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["launch_url"].startswith("https://t.me/")
    assert payload["setup_id"] == "setup-1"


def test_telegram_setup_defaults_to_the_pod_assistant(monkeypatch):
    """No --agent means no `default_agent_name`, which is what binds the surface
    to the pod assistant instead of requiring an agent to exist first."""
    client, captured = _make_telegram_setup_client_and_captured()
    _patch_surfaces(monkeypatch, client, output="json")

    result = runner.invoke(
        app, ["--json", "--pod", "pod-1", "surfaces", "telegram-setup"]
    )

    assert result.exit_code == 0, result.stdout
    assert "default_agent_name" not in captured["start_payload"]


def test_telegram_setup_forwards_agent_and_name(monkeypatch):
    client, captured = _make_telegram_setup_client_and_captured()
    _patch_surfaces(monkeypatch, client, output="json")

    result = runner.invoke(
        app,
        [
            "--json",
            "--pod",
            "pod-1",
            "surfaces",
            "telegram-setup",
            "--agent",
            "ops",
            "--name",
            "telegram-ops",
            "--disabled",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["start_payload"]["default_agent_name"] == "ops"
    assert captured["start_payload"]["name"] == "telegram-ops"
    assert captured["start_payload"]["is_enabled"] is False


def test_telegram_setup_status_dispatches_setup_id(monkeypatch):
    client, captured = _make_telegram_setup_client_and_captured()
    _patch_surfaces(monkeypatch, client, output="json")

    result = runner.invoke(
        app,
        ["--json", "--pod", "pod-1", "surfaces", "telegram-setup-status", "setup-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["status_setup_id"] == "setup-1"
    payload = json.loads(result.stdout)
    assert payload["bot_username"] == "acme_ops_bot"
