from __future__ import annotations

import json

from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import functions


runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fake client helpers
# ---------------------------------------------------------------------------

def _make_client_and_captured():
    captured = {}

    class FakeFunctions:
        def list(self, *, limit=100):
            captured["limit"] = limit
            return {"items": [{"id": "fn-1", "name": "adder", "code": "def adder(input): return input"}]}

        def get(self, name):
            captured["function"] = name
            return {"id": "fn-1", "name": name}

        def create(self, request):
            captured["request"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "fn-1", "name": "adder"}

        def update(self, name, request):
            captured["function"] = name
            captured["request"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "fn-1", "name": name}

        def delete(self, name):
            captured["deleted"] = name

        def run(self, name, inputs=None):
            captured["run_function"] = name
            captured["run_inputs"] = inputs
            return {"id": "run-1", "status": "SUCCEEDED"}

        def run_get(self, name, run_id):
            captured["run_get_function"] = name
            captured["run_id"] = run_id
            return {"id": run_id, "status": "SUCCEEDED"}

        def runs(self, name, *, limit=100):
            captured["runs_function"] = name
            captured["runs_limit"] = limit
            return {"items": [{"id": "run-1", "status": "SUCCEEDED"}]}

    class FakePod:
        def __init__(self):
            self.functions = FakeFunctions()

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
    monkeypatch.setattr(functions, "run_with_client", lambda ctx, fn: fn(client, state))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_functions_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "functions", "list"])

    assert result.exit_code == 0, result.stdout
    assert "adder" in result.stdout
    assert captured["pod_id"] == "pod-1"


def test_functions_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["functions", "get", "adder", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured["function"] == "adder"


def test_functions_create_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"name": "adder", "code": "def adder(input): return input"})
    result = runner.invoke(
        app,
        ["--pod", "pod-1", "functions", "create", "--data", payload],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["request"]["name"] == "adder"


def test_functions_delete_with_yes(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["functions", "delete", "adder", "--yes", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("deleted") == "adder"


def test_functions_delete_requires_yes_noninteractive(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    # CliRunner is non-interactive (stdin is not a TTY), so --yes is required.
    result = runner.invoke(app, ["functions", "delete", "adder", "--pod", "pod-1"])

    assert result.exit_code != 0
    assert "--yes" in result.stdout or "non-interactive" in result.stdout


def test_functions_runs_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["functions", "runs", "list", "adder", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("runs_function") == "adder"
    assert "run-1" in result.stdout or "SUCCEEDED" in result.stdout


def test_functions_runs_list_limit_flag(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["functions", "runs", "list", "adder", "--limit", "5", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["runs_limit"] == 5


def test_functions_runs_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["functions", "runs", "get", "adder", "run-9", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("run_get_function") == "adder"
    assert captured.get("run_id") == "run-9"


def test_functions_run_uses_low_latency_poll_default(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    def run_and_wait(
        _functions,
        *,
        function,
        inputs,
        wait,
        wait_timeout,
        poll_interval,
    ):
        captured["run_function"] = function
        captured["run_inputs"] = inputs
        captured["wait"] = wait
        captured["wait_timeout"] = wait_timeout
        captured["poll_interval"] = poll_interval
        return {"id": "run-1", "status": "COMPLETED"}

    monkeypatch.setattr(functions, "_run_and_optionally_wait", run_and_wait)

    result = runner.invoke(
        app,
        ["functions", "run", "adder", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["run_function"] == "adder"
    assert captured["wait"] is True
    assert captured["poll_interval"] == 0.5
