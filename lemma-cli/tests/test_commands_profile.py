from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import profile

runner = CliRunner()


def _make_client_and_captured():
    captured = {}

    class FakeUser:
        def profile(self):
            captured["profile_called"] = True
            return {"id": "user-1", "email": "me@example.com", "first_name": "Anon"}

        def update_profile(self, payload):
            captured["update"] = payload
            return {"id": "user-1", **payload}

    class FakeClient:
        def __init__(self):
            self.user = FakeUser()

    return FakeClient(), captured


def _patch(monkeypatch, client):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(profile, "run_with_client", lambda ctx, fn: fn(client, state))


def test_profile_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["me", "get"])

    assert result.exit_code == 0, result.stdout
    assert captured.get("profile_called") is True
    assert "me@example.com" in result.stdout


def test_profile_get_json_output(monkeypatch):
    client, _ = _make_client_and_captured()
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="json",
        full=False,
    )
    monkeypatch.setattr(profile, "run_with_client", lambda ctx, fn: fn(client, state))

    result = runner.invoke(app, ["--json", "me", "get"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["email"] == "me@example.com"


def test_profile_update_via_flags(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["me", "update", "--first-name", "Ada", "--country", "UK"],
    )

    assert result.exit_code == 0, result.stdout
    sent = captured["update"]
    assert sent["first_name"] == "Ada"
    assert sent["country"] == "UK"
    # Flags left unset are dropped (None values are cleaned).
    assert "last_name" not in sent


def test_profile_update_via_data(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"first_name": "Grace", "timezone": "UTC"})
    result = runner.invoke(app, ["me", "update", "--data", payload])

    assert result.exit_code == 0, result.stdout
    sent = captured["update"]
    assert sent["first_name"] == "Grace"
    assert sent["timezone"] == "UTC"


def test_config_show_reports_the_resolved_context(monkeypatch, tmp_path):
    """`config show` used to print the configured server names and `active
    server: X` and nothing else — every other field it computed was folded away
    by the generic renderer. It is the command you reach for when things fail
    against the wrong server, which is exactly how a dogfood session lost six
    commands to an unnoticed switch."""
    from typer.testing import CliRunner

    from lemma_cli.cli_core.app import app

    monkeypatch.setenv("LEMMA_ORG_ID", "org-9")
    monkeypatch.setenv("LEMMA_POD_ID", "pod-9")
    monkeypatch.delenv("LEMMA_SERVER", raising=False)

    result = CliRunner().invoke(
        app, ["--config-file", str(tmp_path / "config.json"), "config", "show"]
    )

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "server" in out
    assert "org" in out and "org-9" in out
    assert "pod" in out and "pod-9" in out
    assert "env file" in out
