from __future__ import annotations

import json

import pytest

from app.modules.web_login.domain.entities import WebLoginKind, WebLoginSecret
from app.modules.web_login.services.injection import (
    capture_command,
    current_totp,
    inject_web_login,
    looks_like_session_state,
    new_state_path,
)

STATE = json.dumps({"cookies": [{"name": "sid", "value": "s3cr3t-session-value"}]})


class _FakeSession:
    """Records every command and file write, so the test can look for leaks."""

    def __init__(self, *, load_ok: bool = True) -> None:
        self.commands: list[str] = []
        self.writes: list[tuple[str, bytes]] = []
        self._load_ok = load_ok

    async def exec_command(self, *, cmd, timeout=None, **kwargs):
        self.commands.append(cmd)
        if "state load" in cmd and not self._load_ok:
            return {"success": False, "exit_code": 1}
        return {"success": True, "exit_code": 0}

    async def write_file(self, path, content):
        self.writes.append((path, content))


@pytest.mark.asyncio
async def test_the_session_never_appears_in_a_command() -> None:
    """Argv is world-readable through /proc and recorded by execve auditing, so
    the session has to travel as a file."""
    session = _FakeSession()

    outcome = await inject_web_login(
        session, WebLoginSecret(state=STATE), kind=WebLoginKind.SESSION
    )

    assert outcome.injected is True
    assert all("s3cr3t-session-value" not in cmd for cmd in session.commands)
    assert session.writes[0][1] == STATE.encode()


@pytest.mark.asyncio
async def test_it_is_written_under_tmp_and_removed_afterwards() -> None:
    """/tmp dies with the sandbox; /workspace is a durable volume that would
    outlive the reason the session was injected."""
    session = _FakeSession()

    await inject_web_login(
        session, WebLoginSecret(state=STATE), kind=WebLoginKind.SESSION
    )

    path = session.writes[0][0]
    assert path.startswith("/tmp/")
    assert any(cmd.startswith("chmod 600 ") for cmd in session.commands)
    assert any(cmd.startswith("rm -f ") and path in cmd for cmd in session.commands)


@pytest.mark.asyncio
async def test_the_file_is_removed_even_when_loading_fails() -> None:
    session = _FakeSession(load_ok=False)

    outcome = await inject_web_login(
        session, WebLoginSecret(state=STATE), kind=WebLoginKind.SESSION
    )

    assert outcome.injected is False
    assert any(cmd.startswith("rm -f ") for cmd in session.commands)


@pytest.mark.asyncio
async def test_a_password_only_item_says_what_is_missing() -> None:
    """Typing a password into a page is a separate act, and the person's."""
    session = _FakeSession()

    outcome = await inject_web_login(
        session,
        WebLoginSecret(username="a@b.test", password="hunter2"),
        kind=WebLoginKind.CREDENTIAL,
    )

    assert outcome.injected is False
    assert "sign in once" in outcome.reason
    assert session.writes == []
    assert all("hunter2" not in cmd for cmd in session.commands)


@pytest.mark.asyncio
async def test_an_outcome_carries_nothing_worth_leaking() -> None:
    session = _FakeSession()
    outcome = await inject_web_login(
        session,
        WebLoginSecret(state=STATE, password="hunter2", totp_seed="JBSWY3DP"),
        kind=WebLoginKind.SESSION,
    )
    rendered = repr(outcome)
    assert "hunter2" not in rendered
    assert "JBSWY3DP" not in rendered
    assert "s3cr3t-session-value" not in rendered


def test_a_code_is_generated_without_the_seed_leaving() -> None:
    code, reason = current_totp(
        WebLoginSecret(totp_seed="JBSWY3DPEHPK3PXP"), at=1111111111
    )
    assert code is not None and code.isdigit() and len(code) == 6
    assert "JBSWY3DPEHPK3PXP" not in reason


def test_a_site_with_no_seed_is_told_so() -> None:
    code, reason = current_totp(WebLoginSecret(state=STATE), at=1111111111)
    assert code is None
    assert "No second-factor seed" in reason


def test_an_unusable_seed_does_not_produce_a_wrong_code() -> None:
    """A silently wrong code fails at the site as a wrong password."""
    code, reason = current_totp(
        WebLoginSecret(totp_seed="not base32 !!"), at=1111111111
    )
    assert code is None
    assert "not usable" in reason


def test_an_expiring_code_is_withheld() -> None:
    """Two seconds left produces a login that fails as a wrong secret."""
    # 1111111139 sits one second before the window closes.
    code, reason = current_totp(
        WebLoginSecret(totp_seed="JBSWY3DPEHPK3PXP"), at=1111111139
    )
    assert code is None
    assert "about to expire" in reason


def test_captured_output_is_checked_before_it_is_stored() -> None:
    """Otherwise an error message becomes somebody's saved login: it looks fine
    in the list and fails every time it is used."""
    assert looks_like_session_state(STATE) is True
    assert looks_like_session_state('{"origins": []}') is False
    assert looks_like_session_state('{"origins": [{"origin": "x"}]}') is True
    assert looks_like_session_state("Error: browser not running") is False
    assert looks_like_session_state("{}") is False
    assert looks_like_session_state("[]") is False


def test_capture_and_injection_agree_on_where_state_lives() -> None:
    path = new_state_path()
    assert path.startswith("/tmp/")
    assert path in capture_command(path)
