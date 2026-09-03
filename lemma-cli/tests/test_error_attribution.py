"""Errors must be attributed to what actually went wrong.

Two paths used to report the wrong cause: name resolution turned any failure into
"not found", and `auth print-token` turned a failed refresh into a token that
401s somewhere else entirely.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from lemma_cli.cli_core import context as context_mod
from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import system as system_mod
from lemma_sdk.errors import (
    LemmaAPIError,
    LemmaAuthError,
    LemmaNotFoundError,
    LemmaServerError,
)

runner = CliRunner()


def _client(get_exc: Exception, listed: list[dict]) -> SimpleNamespace:
    def get(_selector):
        raise get_exc

    return SimpleNamespace(
        orgs=SimpleNamespace(get=get, list=lambda limit=200: {"items": listed})
    )


# --- name resolution ------------------------------------------------------


def test_a_404_falls_through_to_the_slug_scan():
    client = _client(
        LemmaNotFoundError(status_code=404, message="no"),
        [{"id": "org-1", "slug": "acme", "name": "Acme"}],
    )

    assert context_mod.resolve_org(client, "acme")["id"] == "org-1"


def test_a_missing_slug_still_reports_not_found():
    client = _client(LemmaNotFoundError(status_code=404, message="no"), [])

    with pytest.raises((typer.Exit, SystemExit)):
        context_mod.resolve_org(client, "acme")


@pytest.mark.parametrize(
    "exc",
    [
        LemmaAuthError(status_code=401, message="session expired"),
        LemmaServerError(status_code=500, message="boom"),
        LemmaAPIError(status_code=502, message="bad gateway"),
        ConnectionResetError("reset by peer"),
    ],
)
def test_a_real_failure_is_not_reported_as_not_found(exc):
    """`except Exception: pass` turned an expired session into "Organization not
    found: acme", which sends the user hunting for a typo. Anything that is not
    404/403 must reach run_with_client's handler, which names the status."""
    client = _client(exc, [{"id": "org-1", "slug": "acme"}])

    with pytest.raises(type(exc)):
        context_mod.resolve_org(client, "acme")


# --- auth print-token -----------------------------------------------------


def _jwt(exp: int) -> str:
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


def test_print_token_refuses_to_hand_out_an_expired_token(monkeypatch, tmp_path):
    """The consumer is an app dev server seeding localStorage; a stale token
    there surfaces as an unexplained 401 in the app being developed, with
    nothing pointing back at this command."""
    monkeypatch.setattr(
        system_mod, "resolve_token", lambda *a, **k: _jwt(int(time.time()) - 3600)
    )

    def failed_refresh(_state):
        raise LemmaAPIError(status_code=401, message="refresh token revoked")

    monkeypatch.setattr(system_mod, "refresh_auth_session", failed_refresh)

    result = runner.invoke(
        app, ["--config-file", str(tmp_path / "c.json"), "auth", "print-token"]
    )

    assert result.exit_code == 1, result.output
    assert "lemma auth login" in " ".join(result.stderr.split())
    assert result.stdout == ""


def test_print_token_still_prints_a_valid_token_when_refresh_fails(
    monkeypatch, tmp_path
):
    token = _jwt(int(time.time()) + 3600)
    monkeypatch.setattr(system_mod, "resolve_token", lambda *a, **k: token)

    def failed_refresh(_state):
        raise LemmaAPIError(status_code=503, message="temporarily unavailable")

    monkeypatch.setattr(system_mod, "refresh_auth_session", failed_refresh)

    result = runner.invoke(
        app,
        ["--config-file", str(tmp_path / "c.json"), "auth", "print-token", "--refresh"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == token
