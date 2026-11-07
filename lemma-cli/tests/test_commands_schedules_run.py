"""`lemma schedules run`: one run of a time schedule now, through the real SDK.

The double is the HTTP boundary -- the SDK's own client, given a mocked
transport -- so the request asserted on is the one the command actually sends.
"""

from __future__ import annotations

import json

import httpx
import lemma_sdk
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app

runner = CliRunner()

_POD = "00000000-0000-0000-0000-0000000000aa"
_SCHEDULE = "00000000-0000-0000-0000-0000000000b1"
_RUN = "00000000-0000-0000-0000-0000000000c1"
_PERSON = "00000000-0000-0000-0000-0000000000d1"
_AT = "2026-01-01T09:00:00Z"


def _server(monkeypatch, handler) -> list[httpx.Request]:
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
    return seen


def _run_started(request: httpx.Request) -> httpx.Response:
    del request
    return httpx.Response(
        202,
        json={
            "id": _RUN,
            "schedule_id": _SCHEDULE,
            "user_id": _PERSON,
            "source_event_id": "manual:00000000-0000-0000-0000-0000000000e1",
            "status": "PROCESSING",
            "attempts": 0,
            "target_kind": "AGENT",
            "target_run_id": "00000000-0000-0000-0000-0000000000f1",
            "payload": {},
            "metadata": {"manual": True, "started_by_user_id": _PERSON},
            "llm_output": {},
            "created_at": _AT,
            "updated_at": _AT,
        },
    )


def _refused_as_paused(request: httpx.Request) -> httpx.Response:
    del request
    return httpx.Response(
        409,
        json={
            "message": "This schedule is paused. Resume it before running it now.",
            "code": "SCHEDULE_INACTIVE",
            "request_id": None,
            "details": None,
        },
    )


def test_schedules_run_starts_one_run_of_the_schedule(monkeypatch):
    seen = _server(monkeypatch, _run_started)

    result = runner.invoke(app, ["schedules", "run", _SCHEDULE, "--pod", _POD])

    assert result.exit_code == 0, result.output
    [request] = seen
    assert (request.method, request.url.path) == (
        "POST",
        f"/pods/{_POD}/schedules/{_SCHEDULE}/runs",
    )
    assert request.content == b""
    assert _RUN in result.stdout
    # Where to follow it, as a command that runs -- on stderr, not in the result.
    hint = f"lemma schedules runs list {_SCHEDULE} --pod {_POD}"
    assert hint in " ".join(result.stderr.split())
    assert "runs list" not in result.stdout


def test_schedules_run_json_output_is_only_the_run(monkeypatch):
    _server(monkeypatch, _run_started)

    result = runner.invoke(
        app, ["--json", "schedules", "run", _SCHEDULE, "--pod", _POD]
    )

    assert result.exit_code == 0, result.output
    started = json.loads(result.stdout)
    assert (started["id"], started["status"]) == (_RUN, "PROCESSING")
    assert result.stderr == ""


def test_schedules_run_says_why_the_server_refused(monkeypatch):
    _server(monkeypatch, _refused_as_paused)

    result = runner.invoke(app, ["schedules", "run", _SCHEDULE, "--pod", _POD])

    assert result.exit_code == 1
    assert "paused" in result.stderr
    assert result.stdout == ""
