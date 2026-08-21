from __future__ import annotations

import pytest
import typer

from lemma_cli.cli_core import state as state_mod
from lemma_sdk.errors import LemmaAPIError


# ---------------------------------------------------------------------------
# humanize_error()
# ---------------------------------------------------------------------------


def test_humanize_error_with_key_error():
    result = state_mod.humanize_error(KeyError("name"))
    assert "name" in result
    assert "Missing required field" in result


def test_humanize_error_with_lemma_api_error():
    exc = LemmaAPIError(status_code=400, message="Bad request")
    result = state_mod.humanize_error(exc)
    assert "Bad request" in result
    assert "400" in result


def test_humanize_error_with_lemma_api_error_with_field_details():
    exc = LemmaAPIError(
        status_code=422,
        message="Validation error",
        details={"detail": [{"loc": ["body", "name"], "msg": "field required"}]},
    )
    result = state_mod.humanize_error(exc)
    assert "Validation error" in result
    assert "field required" in result


def test_humanize_error_with_simple_exception():
    result = state_mod.humanize_error(ValueError("bad value"))
    assert "bad value" in result


def test_humanize_error_key_error_no_args():
    result = state_mod.humanize_error(KeyError())
    assert "Missing required field" in result


# ---------------------------------------------------------------------------
# update_config() — read-only server guard
# ---------------------------------------------------------------------------


def test_update_config_fails_on_read_only_server(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    state = state_mod.CliState(
        config_path=config_path,
        config={},
        root_config=None,
        server="env",
        server_source="env",
        server_read_only=True,
        base_url=None,
        auth_url=None,
        token=None,
        timeout=5.0,
        no_verify_ssl=False,
        output="pretty",
    )
    with pytest.raises((typer.Exit, SystemExit)) as exc_info:
        state_mod.update_config(state, lambda cfg: cfg)
    exc = exc_info.value
    if isinstance(exc, typer.Exit):
        assert exc.exit_code != 0


# ---------------------------------------------------------------------------
# refresh_and_retry() — skips refresh when explicit token is set
# ---------------------------------------------------------------------------


def test_refresh_and_retry_skips_refresh_when_explicit_token(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    state = state_mod.CliState(
        config_path=config_path,
        config={},
        root_config=None,
        server="lemma-cloud",
        server_source="config",
        server_read_only=False,
        base_url=None,
        auth_url=None,
        token="explicit-token",  # explicit token — no refresh should happen
        timeout=5.0,
        no_verify_ssl=False,
        output="pretty",
    )

    # A fn that raises 401 on the first call, succeeds on the second.
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        if calls["count"] == 1:
            raise LemmaAPIError(status_code=401, message="expired")
        return "ok"

    # Because token is set, can_refresh is False, so the 401 is re-raised.
    with pytest.raises(LemmaAPIError) as exc_info:
        state_mod.refresh_and_retry(state, fn)
    assert exc_info.value.status_code == 401
    # fn was only called once (no retry after the 401)
    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# refresh_auth_session() — skips refresh when explicit token is set
# ---------------------------------------------------------------------------


def test_refresh_auth_session_skips_when_explicit_token(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    state = state_mod.CliState(
        config_path=config_path,
        config={},
        root_config=None,
        server="lemma-cloud",
        server_source="config",
        server_read_only=False,
        base_url=None,
        auth_url=None,
        token="explicit-token",
        timeout=5.0,
        no_verify_ssl=False,
        output="pretty",
    )
    # With an explicit token, no refresh should be attempted.
    result = state_mod.refresh_auth_session(state)
    assert result is False
