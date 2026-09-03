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
        orgs=SimpleNamespace(
            get=get,
            list=lambda *, limit=200, page_token=None: {"items": listed},
        )
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


# --- what to do next ------------------------------------------------------


def test_the_error_boundary_prints_the_status_once():
    """`LemmaAPIError.__str__` already renders `[401] CODE: message`, so the
    boundary's own `({status})` prefix printed it twice."""
    from lemma_cli.cli_core.errors import report_cli_error

    exc = LemmaAuthError(
        status_code=401, message="session expired", code="AUTH_EXPIRED"
    )

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        assert report_cli_error(exc)

    assert buffer.getvalue().count("401") == 1


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        (401, "lemma auth login"),
        (403, "permission"),
        (404, "lemma config show"),
        (409, "already exists"),
        (429, "Wait"),
    ],
)
def test_both_error_paths_name_a_next_step(status, hint):
    """These are the messages users paste into support. The envelope's code and
    request_id say what happened; without a next step the user has to infer what
    to try, and the two paths used to disagree about whether to offer one."""
    import contextlib
    import io

    from lemma_cli.cli_core.errors import report_cli_error
    from lemma_cli.cli_core.state import humanize_error
    from lemma_sdk.errors import api_error

    exc = api_error(status, "nope")

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        report_cli_error(exc)

    assert hint in buffer.getvalue()
    assert hint in humanize_error(exc)


def test_a_status_with_no_obvious_next_step_gets_no_advice():
    """A guessed instruction is worse than none: a 500 is not the user's to fix."""
    from lemma_cli.cli_core.state import humanize_error
    from lemma_sdk.errors import api_error

    assert humanize_error(api_error(500, "boom")) == str(api_error(500, "boom"))


def test_a_rate_limit_message_does_not_claim_the_command_was_an_export():
    """`humanize_error` runs for every command, and its 429 branch used to
    explain the export/import daily cap whatever the user had actually run."""
    from lemma_cli.cli_core.state import humanize_error
    from lemma_sdk.errors import api_error

    out = humanize_error(api_error(429, "slow down", retry_after=30))

    assert "export" not in out
    assert "30" in out


# --- name resolution past the first page ----------------------------------


def _paged_orgs(pages: list[list[dict]]):
    """An `orgs` facade that answers `list` a page at a time, like the API."""
    seen: list[str | None] = []

    def _list(*, limit: int = 100, page_token: str | None = None):
        seen.append(page_token)
        index = int(page_token) if page_token else 0
        return {
            "items": pages[index],
            "limit": limit,
            "next_page_token": str(index + 1) if index + 1 < len(pages) else None,
        }

    def get(_selector):
        raise LemmaNotFoundError(status_code=404, message="no")

    return SimpleNamespace(orgs=SimpleNamespace(get=get, list=_list)), seen


def test_a_slug_past_the_first_page_still_resolves():
    """A single page of 200 made the 201st organization unreachable by name, and
    the failure was "not found" rather than "there are more"."""
    client, seen = _paged_orgs(
        [
            [{"id": f"org-{n}", "slug": f"filler-{n}"} for n in range(200)],
            [{"id": "org-target", "slug": "acme"}],
        ]
    )

    assert context_mod.resolve_org(client, "acme")["id"] == "org-target"
    assert seen == [None, "1"]


def test_a_lookup_that_ran_out_of_pages_says_how_far_it_looked(capsys):
    """The walk is bounded, so past the bound the honest answer is "searched N",
    not a confident "not found"."""
    client, _seen = _paged_orgs(
        [[{"id": f"org-{n}", "slug": f"filler-{n}"} for n in range(200)]] * 9
    )

    with pytest.raises((typer.Exit, SystemExit)):
        context_mod.resolve_org(client, "acme")

    assert "searched" in " ".join(capsys.readouterr().err.split())
