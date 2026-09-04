"""What the server records when authentication fails for a reason nobody expected.

`verify_auth` runs on every authenticated request, and its catch-all arm turned
anything it did not recognise into a bare 401 with one `logger.debug` line and
no traceback. Production runs at `LOG_LEVEL=INFO`, which drops that record
before it is formatted -- so a SuperTokens core outage, a JWKS fetch failure or a
misconfigured key presented as an indistinguishable stream of 401s with nothing
on the server saying why, and every client responded by throwing away a perfectly
good session and asking for a new one.

A token that is simply not valid is not that: it is the normal answer to a
normal request, and saying so per request would bury the case that matters.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from supertokens_python.recipe.session.exceptions import UnauthorisedError

from app.core import security


def _connection():
    return SimpleNamespace(
        scope={"type": "http", "method": "GET"},
        url=SimpleNamespace(path="/api/v1/pods"),
        cookies={},
        headers={},
        state=SimpleNamespace(),
    )


def _errors(caplog) -> list[dict]:
    return [
        record.msg
        for record in caplog.records
        if record.levelno >= logging.WARNING and isinstance(record.msg, dict)
    ]


async def test_an_unexpected_auth_failure_is_reported_with_its_traceback(
    caplog, monkeypatch
):
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(side_effect=RuntimeError("supertokens core unreachable")),
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HTTPException) as raised:
            await security.verify_auth(_connection())

    assert raised.value.status_code == 401
    events = _errors(caplog)
    assert [event["event"] for event in events] == [
        "security.auth_dependency.unexpected_failure.degraded"
    ]
    assert "supertokens core unreachable" in events[0]["error_traceback"]


async def test_the_cause_survives_so_the_traceback_points_at_the_fault(monkeypatch):
    cause = RuntimeError("jwks fetch failed")
    monkeypatch.setattr(security, "get_session", AsyncMock(side_effect=cause))

    with pytest.raises(HTTPException) as raised:
        await security.verify_auth(_connection())

    assert raised.value.__cause__ is cause


async def test_an_ordinary_invalid_token_is_not_an_incident(caplog, monkeypatch):
    """The routine answer stays routine; otherwise the log is all 401s."""
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(side_effect=UnauthorisedError("no session")),
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HTTPException) as raised:
            await security.verify_auth(_connection())

    assert raised.value.status_code == 401
    assert _errors(caplog) == []
