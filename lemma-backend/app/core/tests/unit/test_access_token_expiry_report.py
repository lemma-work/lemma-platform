"""Why a 401 happened, when the reason is not the ordinary one.

An access token expiring is routine: the client refreshes and moves on, and
saying so on every request would be noise. A token that is expired by *hours*
is not routine — it means the clock that signed it and the clock reading it
disagree, and no refresh fixes that, because the replacement comes from the same
wrong clock.

A desktop install sat in exactly that state for days. The backend logged
hundreds of 401s and not one line saying why, so the cause had to be found by
minting a token by hand and comparing its `iat` with the wall clock. This is
what makes that a log line instead.
"""

from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest

from app.core import security


def _token(expiry: float) -> str:
    def segment(payload: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return raw.rstrip("=")

    return f"{segment({'kid': 'd-1'})}.{segment({'exp': expiry})}.signature"


def _connection(*, cookie: str | None = None, header: str | None = None):
    return SimpleNamespace(
        cookies={"sAccessToken": cookie} if cookie else {},
        headers={"authorization": header} if header else {},
    )


@pytest.fixture(autouse=True)
def _report_again():
    """The throttle is process-local, so each test starts from a clean slate."""
    security._last_skew_report = -security.CLOCK_SKEW_REPORT_INTERVAL_SECONDS
    yield


def _events(caplog) -> list[dict]:
    """structlog hands the whole event to the stdlib record as a dict."""
    return [record.msg for record in caplog.records if isinstance(record.msg, dict)]


def test_a_token_expired_by_hours_is_reported_with_how_far_off_it_is(caplog):
    connection = _connection(cookie=_token(time.time() - 41_250))

    security._report_expired_access_token(connection)

    events = _events(caplog)
    assert [event["event"] for event in events] == [
        "identity.session.access_token_expiry_implausible.degraded"
    ]
    assert events[0]["expired_by_seconds"] > 41_000


def test_an_ordinary_expiry_says_nothing(caplog):
    connection = _connection(cookie=_token(time.time() - 30))

    security._report_expired_access_token(connection)

    assert _events(caplog) == []


def test_a_bearer_token_is_read_too_so_api_clients_are_not_invisible(caplog):
    connection = _connection(header=f"Bearer {_token(time.time() - 41_250)}")

    security._report_expired_access_token(connection)

    assert len(_events(caplog)) == 1


def test_an_unreadable_token_is_not_worth_an_exception(caplog):
    for value in ("", "not-a-jwt", "a.b", "a.!!!.c"):
        security._report_expired_access_token(_connection(cookie=value))

    assert _events(caplog) == []


def test_a_loop_of_expired_requests_is_reported_once_per_window(caplog):
    """The state this reports is a loop — every request in it carries the same
    expired token. Saying so on each one is the log flood the branch exists to
    stop, arriving from the other side."""
    connection = _connection(cookie=_token(time.time() - 41_250))

    for _ in range(50):
        security._report_expired_access_token(connection)

    assert len(_events(caplog)) == 1
