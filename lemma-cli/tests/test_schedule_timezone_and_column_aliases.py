"""What a first task needs from the CLI without exploring `--help`.

A cron's zone is a flag, and the reply says when the schedule will fire. A
column typed `TIMESTAMP` or `NUMBER` is sent as the type it means rather than
refused by the generated enum before any request is made. And a schedule's
always-null `account_id` is not printed for an agent to take as a field to fill.
"""

from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from lemma_cli.cli_app.enums import canonical_column, canonical_columns
from lemma_cli.cli_core.app import app

from .test_deprecated_account_flags import _NOW, _POD, _server

runner = CliRunner()
_SCHEDULE_ID = "00000000-0000-0000-0000-0000000000c1"


def _schedule_echo(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content) if request.content else {}
    return httpx.Response(
        201,
        json={
            "id": "00000000-0000-0000-0000-0000000000c1",
            "pod_id": _POD,
            "user_id": "00000000-0000-0000-0000-0000000000c2",
            "agent_id": None,
            "workflow_id": None,
            "agent_name": "POD_DEFAULT",
            "name": "digest",
            "account_id": None,
            "config": sent.get("config") or {},
            "connector_trigger_id": None,
            "filter_instruction": None,
            "filter_output_schema": None,
            "is_active": True,
            "is_internal": False,
            "paused_by_failures": False,
            "schedule_type": "TIME",
            "visibility": "POD",
            "next_fire_at": "2026-10-27T08:00:00Z",
            "next_fire_at_local": "2026-10-27T09:00:00+01:00",
            "created_at": _NOW,
            "updated_at": _NOW,
        },
    )


def _create(*extra: str):
    return runner.invoke(
        app,
        [
            "--pod",
            _POD,
            "schedules",
            "create",
            "--agent",
            "POD_DEFAULT",
            "--instruction",
            "digest",
            "--cron",
            "0 9 * * *",
            *extra,
        ],
    )


def test_timezone_is_a_flag_and_the_reply_says_when_it_fires(monkeypatch):
    seen = _server(monkeypatch, _schedule_echo)

    result = _create("--timezone", "Europe/Berlin")

    assert result.exit_code == 0, result.output
    assert json.loads(seen[0].content)["config"] == {
        "cron": "0 9 * * *",
        "timezone": "Europe/Berlin",
    }
    flat = " ".join(result.stderr.split())
    assert "next fire: 2026-10-27T08:00:00" in flat
    assert "2026-10-27T09:00:00+01:00 in Europe/Berlin" in flat


def test_without_a_timezone_the_reply_says_the_cron_is_utc(monkeypatch):
    _server(monkeypatch, _schedule_echo)

    result = _create()

    assert result.exit_code == 0, result.output
    assert "read in UTC" in " ".join(result.stderr.split())


def test_the_dead_account_id_is_not_printed(monkeypatch):
    _server(monkeypatch, _schedule_echo)

    result = runner.invoke(
        app,
        ["--output", "json", "--pod", _POD, "schedules", "get", _SCHEDULE_ID],
    )

    assert result.exit_code == 0, result.output
    assert "account_id" not in result.stdout


def test_common_column_type_spellings_are_sent_as_ours():
    assert canonical_columns(
        [
            {"name": "at", "type": "TIMESTAMP"},
            {"name": "amount", "type": "number"},
            {"name": "note", "type": "text"},
            {"name": "big", "type": "BIGINT"},
        ]
    ) == [
        {"name": "at", "type": "DATETIME"},
        {"name": "amount", "type": "FLOAT"},
        {"name": "note", "type": "TEXT"},
        {"name": "big", "type": "BIGINT"},
    ]
    assert canonical_column({"name": "x"}) == {"name": "x"}


def test_tables_create_sends_the_canonical_type(monkeypatch):
    def created(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"ok": True})

    seen = _server(monkeypatch, created)

    result = runner.invoke(
        app,
        [
            "--pod",
            _POD,
            "tables",
            "create",
            "events",
            "--data",
            json.dumps({"columns": [{"name": "at", "type": "timestamp"}]}),
        ],
    )

    assert seen, result.output
    sent = json.loads(seen[0].content)
    assert sent["columns"] == [
        {"name": "at", "type": "DATETIME", **_column_defaults(sent["columns"][0])}
    ]


def _column_defaults(column: dict) -> dict:
    """Whatever optional keys the generated model fills in, unchanged."""
    return {k: v for k, v in column.items() if k not in {"name", "type"}}
