from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import organizations, pods

runner = CliRunner()


# ---------------------------------------------------------------------------
# Orgs helpers
# ---------------------------------------------------------------------------


class FakeOrgs:
    def __init__(self):
        self._calls = {}

    def list(self, *, limit=100):
        self._calls["list"] = {"limit": limit}
        return {"items": [{"id": "org-1", "name": "Acme"}]}

    def get(self, org):
        self._calls["get"] = {"org": org}
        return {"id": org, "name": "Acme"}


def _make_org_run(fake_orgs_obj):
    def fake_run_with_client(ctx, fn):
        # organizations._orgs(client) does getattr(client, "orgs") or client.organizations
        client = SimpleNamespace(orgs=fake_orgs_obj)
        state = SimpleNamespace(config={"defaults": {"org_id": None}}, output="pretty")
        return fn(client, state)

    return fake_run_with_client


# ---------------------------------------------------------------------------
# 1. orgs get
# ---------------------------------------------------------------------------


def test_orgs_get_dispatches_api(monkeypatch):
    fake_orgs = FakeOrgs()
    monkeypatch.setattr(organizations, "run_with_client", _make_org_run(fake_orgs))

    result = runner.invoke(app, ["orgs", "get", "my-org"])

    assert result.exit_code == 0, result.stdout
    assert fake_orgs._calls.get("get") == {"org": "my-org"}


# ---------------------------------------------------------------------------
# 2. orgs list --json
# ---------------------------------------------------------------------------


def test_orgs_list_json_output(monkeypatch):
    fake_orgs = FakeOrgs()
    monkeypatch.setattr(organizations, "run_with_client", _make_org_run(fake_orgs))

    result = runner.invoke(app, ["--json", "orgs", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert any(item["id"] == "org-1" for item in payload["items"])


# ---------------------------------------------------------------------------
# Pods helpers
# ---------------------------------------------------------------------------

POD_UUID = "11111111-1111-1111-1111-111111111111"


class FakePods:
    def __init__(self):
        self._calls = {}

    def list(self, *, org_id=None, limit=100, page_token=None):
        self._calls["list"] = {"org_id": org_id, "limit": limit}
        return {
            "items": [{"id": POD_UUID, "name": "my-pod", "organization_id": org_id}]
        }

    def get(self, pod_id):
        self._calls["get"] = {"pod_id": pod_id}
        return {"id": pod_id, "name": "my-pod"}


def _make_pods_run(fake_pods_obj):
    def fake_run_with_client(ctx, fn):
        client = SimpleNamespace(pods=fake_pods_obj)
        state = SimpleNamespace(
            config={
                "defaults": {"org_id": "org-1", "pod_id": None},
                "_runtime": {"pod": POD_UUID},
            },
            output="pretty",
        )
        return fn(client, state)

    return fake_run_with_client


# ---------------------------------------------------------------------------
# 3. pods get
# ---------------------------------------------------------------------------


def test_pods_get_dispatches_api(monkeypatch):
    fake_pods_obj = FakePods()
    monkeypatch.setattr(pods, "run_with_client", _make_pods_run(fake_pods_obj))

    result = runner.invoke(app, ["pods", "get", POD_UUID])

    assert result.exit_code == 0, result.stdout
    assert fake_pods_obj._calls.get("get") == {"pod_id": POD_UUID}


# ---------------------------------------------------------------------------
# 4. pods list --json
# ---------------------------------------------------------------------------


def test_pods_list_json_output(monkeypatch):
    fake_pods_obj = FakePods()
    monkeypatch.setattr(pods, "run_with_client", _make_pods_run(fake_pods_obj))

    result = runner.invoke(app, ["--json", "pods", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert any(item["id"] == POD_UUID for item in payload["items"])
