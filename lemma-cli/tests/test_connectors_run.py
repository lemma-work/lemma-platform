"""`lemma connectors run` and the resolution it shares with `operations`.

Executing anything through a connector used to take four commands: `overview` to
learn the org-local auth-config name, `operations search` for the operation id,
`operations get` for the payload shape, then `execute`. Every one of those is a
separate agent step. These tests pin the collapsed flow — and the resolver rules
that make a bare connector id enough.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import connectors

runner = CliRunner()


_INSTALLS = [
    {
        "id": "ac-1",
        "name": "workspace-gmail",
        "connector_id": "gmail",
        "kind": "LEMMA",
        "is_default": True,
    },
    {
        "id": "ac-2",
        "name": "legacy-gmail",
        "connector_id": "gmail",
        "kind": "COMPOSIO",
    },
    {"id": "ac-3", "name": "team-slack", "connector_id": "slack", "kind": "LEMMA"},
]

_OPERATIONS = {
    "gmail_list_messages": {
        "name": "gmail_list_messages",
        "description": "List recent emails in the mailbox.",
        "input_schema": {
            "type": "object",
            "properties": {"max_results": {"type": "integer"}},
        },
    },
    "gmail_send_email": {
        "name": "gmail_send_email",
        "description": "Send an email.",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
        },
    },
}


def _fake_client(captured: dict):
    class Operations:
        def search(self, auth_config, query=None, *, limit=100):
            captured.setdefault("searches", []).append((auth_config, query, limit))
            hits = [
                {"name": name, "description": op["description"]}
                for name, op in _OPERATIONS.items()
                if not query or query.split()[0].lower() in op["description"].lower()
            ] or [
                {"name": name, "description": op["description"]}
                for name, op in _OPERATIONS.items()
            ]
            return {"connector_id": "gmail", "items": hits, "returned_count": len(hits)}

        def batch(self, auth_config, operations):
            captured.setdefault("batches", []).append((auth_config, list(operations)))
            items = [_OPERATIONS[name] for name in operations if name in _OPERATIONS]
            if operations and not items:
                raise KeyError(operations[0])
            return {"connector_id": "gmail", "items": items, "returned_count": len(items)}

        def get(self, auth_config, operation):
            return _OPERATIONS[operation]

    class Accounts:
        def list(self, *, app=None, limit=100):
            return {"items": [{"id": "acct-9", "email": "me@example.com"}]}

    class AuthConfigs:
        def list(self, *, limit=100):
            captured["auth_config_lists"] = captured.get("auth_config_lists", 0) + 1
            return {"items": list(_INSTALLS)}

    class Connectors:
        def __init__(self):
            self.operations = Operations()
            self.accounts = Accounts()
            self.auth_configs = AuthConfigs()

        def execute(self, auth_config, operation, *, payload, account_id=None):
            captured["execute"] = {
                "auth_config": auth_config,
                "operation": operation,
                "payload": payload,
                "account_id": account_id,
            }
            return {"result": {"ok": True}}

    return SimpleNamespace(connectors=Connectors())


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    connectors._AUTH_CONFIG_CACHE.clear()
    yield
    connectors._AUTH_CONFIG_CACHE.clear()


def _patch(monkeypatch, client):
    monkeypatch.setattr(
        connectors,
        "run_with_client",
        lambda ctx, fn: fn(client, SimpleNamespace(config={"_runtime": {"org": "org-1"}})),
    )


def test_run_resolves_connector_id_operation_and_executes(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "run",
            "gmail",
            "gmail_list_messages",
            "-d",
            json.dumps({"max_results": 5}),
        ],
    )

    assert result.exit_code == 0, result.stdout
    # `gmail` resolved to the DEFAULT install, not the first one listed.
    assert captured["execute"]["auth_config"] == "workspace-gmail"
    assert captured["execute"]["operation"] == "gmail_list_messages"
    assert captured["execute"]["payload"] == {"max_results": 5}


def test_run_resolves_a_plain_english_intent(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "run", "gmail", "List recent emails", "--dry-run"]
    )

    assert result.exit_code == 0, result.stdout
    # A dry run resolves and reports the schema without calling execute.
    assert "execute" not in captured
    assert "gmail_list_messages" in result.stdout


def test_run_prints_the_schema_instead_of_failing_when_input_is_missing(monkeypatch):
    """An operation with required input and no --data is the moment you need the
    schema, not an error."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(app, ["connectors", "run", "gmail", "gmail_send_email"])

    assert result.exit_code == 0, result.stdout
    assert "execute" not in captured
    assert "to" in result.stdout  # the required field is named


def test_run_accepts_an_account_email(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "run",
            "gmail",
            "gmail_list_messages",
            "-d",
            "{}",
            "--account",
            "me@example.com",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["execute"]["account_id"] == "acct-9"


def test_run_reports_an_unknown_connector_with_what_is_installed(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(app, ["connectors", "run", "notion", "anything"])

    assert result.exit_code != 0
    # The error names the installs that DO exist, so the next command is obvious
    # without a separate `overview` call.
    assert "workspace-gmail" in result.output


def test_search_treats_a_lone_non_install_positional_as_the_query(monkeypatch):
    """`operations search "send email"` used to bind the query to the auth-config
    slot and 404, so the auto-discovery its help advertised could never fire."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "operations", "search", "send email", "--limit", "5"]
    )

    assert result.exit_code != 0  # two installs -> must disambiguate...
    # ...but the failure is about the ambiguous connector, NOT a bogus lookup of
    # "send email" as an auth config.
    assert "Several connectors are installed" in result.output


def test_search_folds_input_schemas_into_short_result_lists(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "--json",
            "connectors",
            "operations",
            "search",
            "-c",
            "gmail",
            "-q",
            "send",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    # The schemas came from ONE extra batch call, not a get-per-hit.
    assert len(captured.get("batches", [])) == 1
    payload = json.loads(result.stdout)
    assert payload["items"][0]["input_schema"]["properties"] == {"to": {"type": "string"}}


def test_operations_get_auto_discovers_like_its_siblings(monkeypatch):
    """`get` was the one operations command that demanded an explicit auth
    config, so a single-install org still had to name it."""
    captured: dict = {}
    client = _fake_client(captured)
    monkeypatch.setattr(
        connectors, "_auth_config_items", lambda _client: [_INSTALLS[2]]
    )
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["connectors", "operations", "get", "gmail_send_email"])

    assert result.exit_code == 0, result.stdout
    assert "gmail_send_email" in result.stdout


def test_resolution_lists_installs_once_per_command(monkeypatch):
    """Classifying a positional and resolving it must not cost two round trips."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "operations", "execute", "workspace-gmail", "gmail_list_messages"]
    )

    assert result.exit_code == 0, result.stdout
    assert captured["auth_config_lists"] == 1
