from __future__ import annotations

import json

from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import agents


runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fake client helpers
# ---------------------------------------------------------------------------

def _make_client_and_captured():
    captured = {}

    class FakeAgents:
        def list(self, *, limit=100):
            captured["limit"] = limit
            return {"items": [{"id": "agent-1", "name": "my-agent", "instruction": "Be helpful."}]}

        def get(self, name):
            captured["name"] = name
            return {"id": "agent-1", "name": name}

        def create(self, request):
            captured["request"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "agent-1", "name": "my-agent"}

        def update(self, name, request):
            captured["name"] = name
            captured["request"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "agent-1", "name": name}

        def delete(self, name):
            captured["deleted"] = name

        def permissions(self, name):
            captured["permissions_agent"] = name
            return {"grants": []}

    class FakePod:
        def __init__(self):
            self.agents = FakeAgents()

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return FakePod()

    return FakeClient(), captured


def _patch(monkeypatch, client):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(agents, "run_with_client", lambda ctx, fn: fn(client, state))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_agents_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "agents", "list"])

    assert result.exit_code == 0, result.stdout
    assert "my-agent" in result.stdout
    assert captured["pod_id"] == "pod-1"


def test_agents_list_limit_flag(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "agents", "list", "--limit", "5"])

    assert result.exit_code == 0, result.stdout
    assert captured["limit"] == 5


def test_agents_list_json_output(monkeypatch):
    client, captured = _make_client_and_captured()
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="json",
        full=False,
    )
    monkeypatch.setattr(agents, "run_with_client", lambda ctx, fn: fn(client, state))

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "agents", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload


def test_agents_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["agents", "get", "my-agent", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured["name"] == "my-agent"


def test_agents_create_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"name": "my-agent", "instruction": "Be helpful."})
    result = runner.invoke(
        app,
        ["--pod", "pod-1", "agents", "create", "--data", payload],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["request"]["name"] == "my-agent"
    assert captured["request"]["instruction"] == "Be helpful."


def test_agents_update_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"instruction": "Updated."})
    result = runner.invoke(
        app,
        ["agents", "update", "my-agent", "--data", payload, "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["name"] == "my-agent"


def test_agents_delete_with_yes_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["agents", "delete", "my-agent", "--yes", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("deleted") == "my-agent"


def test_agents_delete_without_yes_refuses_noninteractive(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    # CliRunner is non-interactive (stdin is not a TTY), so --yes is required.
    result = runner.invoke(app, ["agents", "delete", "my-agent", "--pod", "pod-1"])

    assert result.exit_code != 0
    assert "--yes" in result.stdout or "non-interactive" in result.stdout


def test_agents_permissions_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["agents", "permissions", "get", "my-agent", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("permissions_agent") == "my-agent"


def test_agents_create_applies_inline_permissions(monkeypatch):
    """Agents take the same path functions do, even though their create endpoint
    *does* accept inline permissions — one behavior, one report, for both."""
    captured: dict = {}

    class FakeAgents:
        def create(self, request):
            captured["create"] = request.to_dict()
            return {"id": "ag-1", "name": captured["create"]["name"]}

        def replace_permissions(self, name, request):
            captured["permissions"] = (name, request.to_dict())
            return {"agent_name": name, **request.to_dict()}

    class FakePod:
        def __init__(self):
            self.agents = FakeAgents()

    class FakeClient:
        def pod(self, pod_id):
            return FakePod()

    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(agents, "run_with_client", lambda ctx, fn: fn(FakeClient(), state))

    grants = [
        {
            "resource_type": "connector",
            "resource_name": "gmail",
            "permission_ids": ["connector.use"],
        }
    ]
    payload = json.dumps(
        {"name": "mailer", "instruction": "Send mail.", "permissions": {"grants": grants}}
    )
    result = runner.invoke(app, ["agents", "create", "--data", payload, "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert "permissions" not in captured["create"]
    assert captured["permissions"] == ("mailer", {"grants": grants})


def test_agents_create_with_empty_grants_says_so(monkeypatch):
    """An explicitly empty grant list is a real instruction (replace with
    nothing), and leaves the agent unable to reach anything — so it is called
    out rather than reported as a success."""
    captured: dict = {}

    class FakeAgents:
        def create(self, request):
            captured["create"] = request.to_dict()
            return {"id": "ag-1", "name": "mailer"}

        def replace_permissions(self, name, request):
            captured["permissions"] = (name, request.to_dict())
            return {"agent_name": name, **request.to_dict()}

    class FakePod:
        def __init__(self):
            self.agents = FakeAgents()

    class FakeClient:
        def pod(self, pod_id):
            return FakePod()

    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(agents, "run_with_client", lambda ctx, fn: fn(FakeClient(), state))

    payload = json.dumps(
        {"name": "mailer", "instruction": "Send mail.", "permissions": {"grants": []}}
    )
    result = runner.invoke(app, ["agents", "create", "--data", payload, "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured["permissions"] == ("mailer", {"grants": []})
    assert "EMPTY" in result.stdout
