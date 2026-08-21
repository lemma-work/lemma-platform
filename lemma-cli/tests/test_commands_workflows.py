from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import workflows

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------


class FakeWorkflows:
    def __init__(self):
        self._calls = {}

    def list(self, *, limit=100):
        self._calls["list"] = {"limit": limit}
        return {"items": [{"id": "wf-1", "name": "my-wf"}]}

    def get(self, workflow):
        self._calls["get"] = {"workflow": workflow}
        return {"id": workflow, "name": workflow}

    def create(self, request):
        self._calls["create"] = {"request": request}
        return {"id": "wf-new", "name": "my-wf"}

    def delete(self, workflow):
        self._calls["delete"] = {"workflow": workflow}
        return None

    def runs(self, workflow, *, limit=100):
        self._calls["runs_list"] = {"workflow": workflow, "limit": limit}
        return {"items": [{"id": "run-1", "status": "SUCCEEDED"}]}


def _make_fake_pod(fake_wf):
    return SimpleNamespace(workflows=fake_wf, pod_id="pod-1")


def _make_fake_run(fake_wf):
    def fake_run_with_client(ctx, fn):
        client = SimpleNamespace(pod=lambda pod_id: _make_fake_pod(fake_wf))
        state = SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty")
        return fn(client, state)

    return fake_run_with_client


# ---------------------------------------------------------------------------
# 1. workflows list
# ---------------------------------------------------------------------------


def test_workflows_list_dispatches_api(monkeypatch):
    fake_wf = FakeWorkflows()
    monkeypatch.setattr(workflows, "run_with_client", _make_fake_run(fake_wf))

    result = runner.invoke(app, ["--pod", "pod-1", "workflows", "list"])

    assert result.exit_code == 0, result.stdout
    assert "my-wf" in result.stdout
    assert "list" in fake_wf._calls


# ---------------------------------------------------------------------------
# 2. workflows list --json
# ---------------------------------------------------------------------------


def test_workflows_list_json_output(monkeypatch):
    fake_wf = FakeWorkflows()
    monkeypatch.setattr(workflows, "run_with_client", _make_fake_run(fake_wf))

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "workflows", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert payload["items"] == [{"id": "wf-1", "name": "my-wf"}]


# ---------------------------------------------------------------------------
# 3. workflows get
# ---------------------------------------------------------------------------


def test_workflows_get_dispatches_api(monkeypatch):
    fake_wf = FakeWorkflows()
    monkeypatch.setattr(workflows, "run_with_client", _make_fake_run(fake_wf))

    result = runner.invoke(app, ["workflows", "get", "my-wf", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert fake_wf._calls.get("get") == {"workflow": "my-wf"}


# ---------------------------------------------------------------------------
# 4. workflows create
# ---------------------------------------------------------------------------


def test_workflows_create_dispatches_api(monkeypatch):
    captured = {}

    class CapturingWorkflows(FakeWorkflows):
        def create(self, request):
            captured["name"] = getattr(request, "name", None) or (
                request.get("name") if isinstance(request, dict) else None
            )
            return {"id": "wf-new", "name": "my-wf"}

    fake_wf = CapturingWorkflows()
    monkeypatch.setattr(workflows, "run_with_client", _make_fake_run(fake_wf))

    result = runner.invoke(
        app,
        ["workflows", "create", "--data", '{"name": "my-wf"}', "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout


# ---------------------------------------------------------------------------
# 5. workflows delete --yes
# ---------------------------------------------------------------------------


def test_workflows_delete_with_yes(monkeypatch):
    fake_wf = FakeWorkflows()
    monkeypatch.setattr(workflows, "run_with_client", _make_fake_run(fake_wf))

    result = runner.invoke(
        app, ["workflows", "delete", "my-wf", "--yes", "--pod", "pod-1"]
    )

    assert result.exit_code == 0, result.stdout
    assert fake_wf._calls.get("delete") == {"workflow": "my-wf"}


# ---------------------------------------------------------------------------
# 6. workflows delete without --yes in non-interactive mode
# ---------------------------------------------------------------------------


def test_workflows_delete_requires_yes(monkeypatch):
    fake_wf = FakeWorkflows()
    monkeypatch.setattr(workflows, "run_with_client", _make_fake_run(fake_wf))

    # CliRunner is non-interactive (stdin is not a TTY), so confirm_destructive
    # should call fail() and produce a non-zero exit.
    result = runner.invoke(app, ["workflows", "delete", "my-wf", "--pod", "pod-1"])

    assert result.exit_code != 0
    # Either "non-interactive" or "--yes" should appear in the output.
    assert ("non-interactive" in result.stdout) or ("--yes" in result.stdout)


# ---------------------------------------------------------------------------
# 7. workflows runs list
# ---------------------------------------------------------------------------


def test_workflows_runs_list_dispatches_api(monkeypatch):
    fake_wf = FakeWorkflows()
    monkeypatch.setattr(workflows, "run_with_client", _make_fake_run(fake_wf))

    result = runner.invoke(
        app, ["workflows", "runs", "list", "my-wf", "--pod", "pod-1"]
    )

    assert result.exit_code == 0, result.stdout
    assert "run-1" in result.stdout
    assert fake_wf._calls.get("runs_list", {}).get("workflow") == "my-wf"


def test_runs_waiting_says_what_its_queue_covers(monkeypatch):
    """ "Nothing is waiting on you." was read as "nothing is waiting".

    A run parked on a FORM with no assignee is submittable by any member with
    execute access but belongs to no one's queue, so it never appears here — and
    `lemma-user` sends you to this command when a workflow is stuck. The empty
    case has to state its own scope, or it convinces you the run is fine.
    """
    from types import SimpleNamespace

    from lemma_cli.cli_core.commands import workflows as workflows_mod

    class FakeWorkflows:
        def list_my_waits(self, *, limit=100):
            return {"items": []}

    monkeypatch.setattr(
        workflows_mod,
        "run_with_client",
        lambda ctx, fn: fn(
            SimpleNamespace(
                pod=lambda pod_id: SimpleNamespace(workflows=FakeWorkflows())
            ),
            SimpleNamespace(
                config={"_runtime": {"pod": "pod-1"}}, output="pretty", full=False
            ),
        ),
    )

    result = runner.invoke(app, ["workflows", "runs", "waiting", "--pod", "pod-1"])

    assert result.exit_code == 0, result.output
    out = " ".join(result.stdout.split())
    assert "assigned to you" in out
    # It points at where the answer actually lives.
    assert "runs get" in out
    # And no longer implies the pod has nothing parked.
    assert "Nothing is waiting on you." not in out
