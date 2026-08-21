from __future__ import annotations

import json

from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import data


runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fake client helpers
# ---------------------------------------------------------------------------


def _make_client_and_captured():
    captured = {}

    class FakeTables:
        def list(self, *, limit=100):
            captured["tables_limit"] = limit
            return {"items": [{"id": "tbl-1", "name": "my_table"}]}

        def get(self, table):
            captured["table"] = table
            return {"id": "tbl-1", "name": table, "columns": []}

        def create(self, request):
            captured["request"] = (
                request.to_dict() if hasattr(request, "to_dict") else request
            )
            return {"id": "tbl-1", "name": "my_table"}

        def add_column(self, table, request):
            captured["add_column_table"] = table
            captured["add_column_request"] = (
                request.to_dict() if hasattr(request, "to_dict") else request
            )
            return {"id": "tbl-1"}

        def delete(self, table):
            captured["deleted"] = table

    class FakeRecords:
        def list(self, table, *, limit=20, **kwargs):
            captured["records_table"] = table
            return {"items": [{"id": "rec-1", "score": 42}]}

        def create(self, table, data=None):
            captured["records_create"] = {"table": table, "data": data}
            return {"id": "rec-1"}

        def delete(self, table, record_id):
            captured["records_deleted"] = {"table": table, "id": record_id}

    class FakePod:
        def __init__(self):
            self.tables = FakeTables()
            self.records = FakeRecords()

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return FakePod()

    return FakeClient(), captured


def _patch(monkeypatch, client, output="pretty"):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output=output,
        full=False,
    )
    monkeypatch.setattr(data, "run_with_client", lambda ctx, fn: fn(client, state))


# ---------------------------------------------------------------------------
# Tables tests
# ---------------------------------------------------------------------------


def test_tables_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "tables", "list"])

    assert result.exit_code == 0, result.stdout
    assert "my_table" in result.stdout


def test_tables_list_json_output(monkeypatch):
    client, captured = _make_client_and_captured()
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="json",
        full=False,
    )
    monkeypatch.setattr(data, "run_with_client", lambda ctx, fn: fn(client, state))

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "tables", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert payload["items"][0]["name"] == "my_table"


def test_tables_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["tables", "get", "my_table", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured["table"] == "my_table"


def test_tables_create_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["tables", "create", "my_table", "--data", '{"columns": []}', "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["request"]["name"] == "my_table"


def test_tables_add_column_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        [
            "tables",
            "add-column",
            "my_table",
            "score",
            "--type",
            "INTEGER",
            "--pod",
            "pod-1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["add_column_table"] == "my_table"
    assert captured["add_column_request"]["column"]["name"] == "score"


def test_tables_delete_with_yes(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["tables", "delete", "my_table", "--yes", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("deleted") == "my_table"


def test_tables_delete_requires_yes(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    # CliRunner is non-interactive (stdin is not a TTY), so --yes is required.
    result = runner.invoke(app, ["tables", "delete", "my_table", "--pod", "pod-1"])

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Records tests
# ---------------------------------------------------------------------------


def test_records_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "records", "list", "my_table"])

    assert result.exit_code == 0, result.stdout
    assert captured["records_table"] == "my_table"


def test_records_list_json_output(monkeypatch):
    client, captured = _make_client_and_captured()
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="json",
        full=False,
    )
    monkeypatch.setattr(data, "run_with_client", lambda ctx, fn: fn(client, state))

    result = runner.invoke(
        app, ["--json", "--pod", "pod-1", "records", "list", "my_table"]
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload
    assert payload["items"][0]["id"] == "rec-1"


def test_records_create_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["records", "create", "my_table", "--data", '{"score": 42}', "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["records_create"]["table"] == "my_table"


def test_records_delete_with_yes(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        ["records", "delete", "my_table", "rec-1", "--yes", "--pod", "pod-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["records_deleted"]["table"] == "my_table"
    assert captured["records_deleted"]["id"] == "rec-1"


def test_records_delete_requires_yes(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    # CliRunner is non-interactive (stdin is not a TTY), so --yes is required.
    result = runner.invoke(
        app, ["records", "delete", "my_table", "rec-1", "--pod", "pod-1"]
    )

    assert result.exit_code != 0
