from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import apps

runner = CliRunner()


def _make_client_and_captured():
    captured = {}

    class FakeApps:
        def list(self, *, limit=100):
            captured["limit"] = limit
            return {"items": [{"id": "app-1", "name": "my-app", "url": "https://app.example.com"}]}

        def get(self, name):
            captured["get"] = name
            return {"id": "app-1", "name": name}

        def create(self, request):
            captured["create"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "app-1", "name": "my-app"}

        def update(self, name, request):
            captured["update"] = name
            captured["update_payload"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "app-1", "name": name}

        def delete(self, name):
            captured["deleted"] = name

    class FakePod:
        def __init__(self):
            self.apps = FakeApps()

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
    monkeypatch.setattr(apps, "run_with_client", lambda ctx, fn: fn(client, state))


def test_apps_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "apps", "list"])

    assert result.exit_code == 0, result.stdout
    assert "my-app" in result.stdout
    assert captured["pod_id"] == "pod-1"


def test_apps_list_limit_flag(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "apps", "list", "--limit", "5"])

    assert result.exit_code == 0, result.stdout
    assert captured["limit"] == 5


def test_apps_list_json_output(monkeypatch):
    client, _ = _make_client_and_captured()
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="json",
        full=False,
    )
    monkeypatch.setattr(apps, "run_with_client", lambda ctx, fn: fn(client, state))

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "apps", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload


def test_apps_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["apps", "get", "my-app", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured["get"] == "my-app"


def test_apps_create_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"name": "my-app"})
    result = runner.invoke(app, ["--pod", "pod-1", "apps", "create", "--data", payload])

    assert result.exit_code == 0, result.stdout
    assert captured["create"]["name"] == "my-app"


def test_apps_update_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"description": "updated"})
    result = runner.invoke(
        app, ["apps", "update", "my-app", "--data", payload, "--pod", "pod-1"]
    )

    assert result.exit_code == 0, result.stdout
    assert captured["update"] == "my-app"
    assert captured["update_payload"]["description"] == "updated"


def test_apps_delete_with_yes_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["apps", "delete", "my-app", "--yes", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured.get("deleted") == "my-app"


def test_apps_delete_without_yes_refuses_noninteractive(monkeypatch):
    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["apps", "delete", "my-app", "--pod", "pod-1"])

    assert result.exit_code != 0
    assert "--yes" in result.stdout or "non-interactive" in result.stdout


def test_apps_init_writes_server_binding(tmp_path, monkeypatch):
    # `app init` binds the folder to the pod on the active server so later commands
    # target it. Isolated config -> fresh "lemma-cloud" server (writable).
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config.json"
    target = tmp_path / "web"

    result = runner.invoke(
        app,
        [
            "--config-file", str(cfg), "--pod", "pod-abc",
            "apps", "init", str(target), "--no-install",
        ],
    )

    assert result.exit_code == 0, result.output
    binding = target / ".lemma.lemma-cloud.env"
    assert binding.exists()
    assert "LEMMA_POD_ID=pod-abc" in binding.read_text(encoding="utf-8")
    assert "LEMMA_SERVER=lemma-cloud" in (target / ".lemma.env").read_text(encoding="utf-8")


def test_apps_open_never_puts_the_token_in_argv(monkeypatch):
    """argv is world-readable on Linux and is captured by execve auditing.

    A bearer token passed as a command-line argument is therefore readable by
    any other local user while the call runs, and afterwards in the audit log.
    """
    secret = "SECRET-SESSION-TOKEN"
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, "input": kwargs.get("input", "")})
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(apps.shutil, "which", lambda _name: "/usr/local/bin/agent-browser")
    monkeypatch.setattr(apps.subprocess, "run", fake_run)
    monkeypatch.setattr(apps, "resolve_token", lambda *a, **k: secret)
    monkeypatch.setattr(apps, "resolve_base_url", lambda *a, **k: "https://api.example.com")
    monkeypatch.setattr(apps, "resolve_auth_url", lambda *a, **k: "https://auth.example.com")

    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app, ["--pod", "pod-1", "apps", "open", "--url", "https://app.example.com"]
    )

    assert result.exit_code == 0, result.output
    assert calls, "agent-browser was never invoked"
    for call in calls:
        assert secret not in " ".join(call["command"]), call["command"]
    # It still reaches the browser -- over stdin, where the process table
    # cannot see it.
    assert any(secret in call["input"] for call in calls)


def _source_zip(entries: dict[str, str]) -> bytes:
    import io
    from zipfile import ZIP_DEFLATED, ZipFile

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _patch_pull(monkeypatch, archive: bytes):
    captured = {}

    class FakeApps:
        def download_source_archive(self, name):
            captured["downloaded"] = name
            return archive

    class FakePod:
        def __init__(self):
            self.apps = FakeApps()

    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="json",
        full=False,
    )
    monkeypatch.setattr(apps, "pod_client", lambda client, s, pod=None: FakePod())
    monkeypatch.setattr(apps, "run_with_client", lambda ctx, fn: fn(object(), state))
    return captured


def test_apps_pull_writes_source_tree(monkeypatch, tmp_path):
    archive = _source_zip(
        {
            "package.json": '{"name":"my-app"}',
            "src/main.tsx": "export {}\n",
        }
    )
    captured = _patch_pull(monkeypatch, archive)
    target = tmp_path / "my-app"

    result = runner.invoke(
        app, ["--json", "--pod", "pod-1", "apps", "pull", "my-app", str(target)]
    )

    assert result.exit_code == 0, result.stdout
    assert captured["downloaded"] == "my-app"
    assert (target / "package.json").read_text() == '{"name":"my-app"}'
    assert (target / "src" / "main.tsx").read_text() == "export {}\n"
    payload = json.loads(result.stdout)
    assert payload["files"] == 2


def test_apps_pull_refuses_non_empty_target_without_force(monkeypatch, tmp_path):
    _patch_pull(monkeypatch, _source_zip({"index.html": "<!doctype html>"}))
    target = tmp_path / "my-app"
    target.mkdir()
    (target / "keep.txt").write_text("mine")

    result = runner.invoke(
        app, ["--pod", "pod-1", "apps", "pull", "my-app", str(target)]
    )

    assert result.exit_code != 0
    assert (target / "keep.txt").read_text() == "mine"
    assert not (target / "index.html").exists()


def test_apps_pull_force_overwrites_non_empty_target(monkeypatch, tmp_path):
    _patch_pull(monkeypatch, _source_zip({"index.html": "<!doctype html>"}))
    target = tmp_path / "my-app"
    target.mkdir()
    (target / "index.html").write_text("stale")

    result = runner.invoke(
        app, ["--json", "--pod", "pod-1", "apps", "pull", "my-app", str(target), "--force"]
    )

    assert result.exit_code == 0, result.stdout
    assert (target / "index.html").read_text() == "<!doctype html>"


def test_apps_pull_rejects_path_traversal(monkeypatch, tmp_path):
    _patch_pull(monkeypatch, _source_zip({"../escaped.txt": "nope"}))
    target = tmp_path / "my-app"

    result = runner.invoke(
        app, ["--pod", "pod-1", "apps", "pull", "my-app", str(target)]
    )

    assert result.exit_code != 0
    assert not (tmp_path / "escaped.txt").exists()


def test_apps_pull_defaults_target_to_app_name(monkeypatch, tmp_path):
    _patch_pull(monkeypatch, _source_zip({"index.html": "<!doctype html>"}))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "apps", "pull", "my-app"])

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "my-app" / "index.html").exists()
