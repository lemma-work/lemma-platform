"""Account flags from before pod connectors: hidden, still served, warned about.

A schedule and a surface used to name the account they run as. Both now name a
pod connector, but live scripts and agents still pass `--account`; the server
maps it to the pod connector serving that account and says it is deprecated.
The CLI sends it, hides the flag, and prints one line saying what replaces it.
"""

from __future__ import annotations

import json
import warnings

import httpx
import lemma_sdk
import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.deprecation import show_sdk_deprecations
from lemma_sdk.errors import LemmaDeprecationWarning

runner = CliRunner()


def _flat(text: str) -> str:
    return " ".join(text.split())


_POD = "00000000-0000-0000-0000-0000000000aa"
_ACCOUNT = "11111111-2222-3333-4444-555555555555"
_NOW = "2026-10-24T00:00:00Z"


def _server(monkeypatch, handler) -> list[httpx.Request]:
    """Run the real CLI and the real SDK against ``handler`` as the server.

    The double is the HTTP boundary -- the SDK's own client, given a mocked
    transport -- so the request each flag produces is the one actually sent.
    """
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    class MockedLemma(lemma_sdk.Lemma):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._transport.generated.set_httpx_client(
                httpx.Client(
                    transport=httpx.MockTransport(record),
                    base_url=str(kwargs.get("base_url") or "https://api.example.test"),
                )
            )

    monkeypatch.setattr(lemma_sdk, "Lemma", MockedLemma)
    monkeypatch.setenv("LEMMA_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("LEMMA_TOKEN", "test-token")
    monkeypatch.setattr("lemma_sdk.transport._warned_deprecations", set())
    return seen


_SCHEDULE_NOTICE = "schedule.create.account_id is deprecated: name the pod connector"


def _schedule_created(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content)
    return httpx.Response(
        201,
        json={
            "id": "00000000-0000-0000-0000-0000000000c1",
            "pod_id": _POD,
            "user_id": "00000000-0000-0000-0000-0000000000c2",
            "agent_id": None,
            "workflow_id": None,
            "name": None,
            "config": sent.get("config") or {},
            "connector_trigger_id": None,
            "filter_instruction": None,
            "filter_output_schema": None,
            "is_active": True,
            "is_internal": False,
            "paused_by_failures": False,
            "schedule_type": sent["schedule_type"],
            "visibility": "POD",
            "created_at": _NOW,
            "updated_at": _NOW,
        },
        headers=(
            {"X-Lemma-Deprecation": _SCHEDULE_NOTICE, "Deprecation": "true"}
            if "account_id" in sent
            else {}
        ),
    )


def _create_webhook_schedule(*extra: str):
    return runner.invoke(
        app,
        [
            "--pod",
            _POD,
            "schedules",
            "create",
            "--agent",
            "triage",
            "--webhook-source",
            "composio",
            *extra,
        ],
    )


def test_schedule_create_account_is_sent_deprecated_and_warns(monkeypatch):
    seen = _server(monkeypatch, _schedule_created)

    with pytest.warns(LemmaDeprecationWarning, match="name the pod connector"):
        result = _create_webhook_schedule(
            "--connector-trigger", "gmail:new_message", "--account", _ACCOUNT
        )

    assert result.exit_code == 0, result.output
    [request] = seen
    assert request.url.path == f"/pods/{_POD}/schedules"
    # The server turns it into the pod connector serving that account.
    assert json.loads(request.content)["account_id"] == _ACCOUNT
    assert "warning: schedules create --account is deprecated" in _flat(result.stderr)
    assert "--connector" in result.stderr


def test_schedule_account_id_in_the_data_is_accepted_and_warns(monkeypatch):
    seen = _server(monkeypatch, _schedule_created)

    with pytest.warns(LemmaDeprecationWarning):
        result = _create_webhook_schedule(
            "--data", json.dumps({"account_id": _ACCOUNT})
        )

    assert result.exit_code == 0, result.output
    assert json.loads(seen[0].content)["account_id"] == _ACCOUNT
    assert "account_id in a schedule's --data is deprecated" in _flat(result.stderr)


def test_schedule_create_with_a_connector_sends_no_account_and_no_warning(
    monkeypatch,
):
    seen = _server(monkeypatch, _schedule_created)

    with warnings.catch_warnings():
        warnings.simplefilter("error", LemmaDeprecationWarning)
        result = _create_webhook_schedule("--connector", "gmail")

    assert result.exit_code == 0, result.output
    sent = json.loads(seen[0].content)
    assert sent["pod_connector"] == "gmail"
    assert "account_id" not in sent
    assert "deprecated" not in result.stderr


def test_surface_upsert_account_is_sent_deprecated_and_warns(monkeypatch):
    def surface(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "00000000-0000-0000-0000-0000000000d1",
                "pod_id": _POD,
                "name": "slack",
                "platform": "SLACK",
                "config": {},
            },
            headers={
                "X-Lemma-Deprecation": "agent.surface.update.account_id is deprecated"
            },
        )

    seen = _server(monkeypatch, surface)

    with pytest.warns(LemmaDeprecationWarning):
        result = runner.invoke(
            app,
            ["--pod", _POD, "surfaces", "upsert", "slack", "--account-id", _ACCOUNT],
        )

    assert result.exit_code == 0, result.output
    [request] = seen
    assert request.url.path == f"/pods/{_POD}/surfaces/slack"
    assert json.loads(request.content) == {"account_id": _ACCOUNT}
    assert "warning: surfaces upsert --account is deprecated" in _flat(result.stderr)


def test_deprecated_account_flags_are_hidden_from_help():
    for command in (
        ["schedules", "create", "--help"],
        ["surfaces", "upsert", "--help"],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
        assert "--account" not in result.output


def test_sdk_deprecation_warnings_print_as_one_line(monkeypatch, capsys):
    """The SDK raises the server's deprecation header as a warning; the CLI
    lets it through, as one `warning:` line, and hands every other warning to
    the handler that was there before."""
    others: list[str] = []
    monkeypatch.setattr(
        warnings,
        "showwarning",
        lambda message, *args, **kwargs: others.append(str(message)),
    )
    show_sdk_deprecations()

    warnings.showwarning(
        LemmaDeprecationWarning("connector.operation.execute is deprecated: x"),
        LemmaDeprecationWarning,
        "transport.py",
        1,
    )
    warnings.showwarning(UserWarning("something else"), UserWarning, "other.py", 7)

    assert capsys.readouterr().err == (
        "warning: connector.operation.execute is deprecated: x\n"
    )
    assert others == ["something else"]
