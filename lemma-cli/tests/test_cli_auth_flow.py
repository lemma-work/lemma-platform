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


# ---------------------------------------------------------------------------
# auth login — say where you are sending them, and give them time to get there
# ---------------------------------------------------------------------------


def _login_runner():
    from typer.testing import CliRunner

    return CliRunner()


_SESSION = {"access_token": "at", "refresh_token": "rt", "email": "a@b.c"}


def _flow_result(login_url="https://auth.example.com/cli/login?state=x"):
    from lemma_sdk.auth import LoginFlowResult

    return LoginFlowResult(
        session=dict(_SESSION), login_url=login_url, browser_opened=True
    )


def _auth_url() -> str:
    """The auth URL `login` will actually use. A configured auth_url wins over
    whatever /auth/cli/info reports, and a fresh config gets the shipped default."""
    from lemma_sdk.config import DEFAULT_AUTH_URL

    return DEFAULT_AUTH_URL


def test_login_announces_the_auth_url_before_it_blocks(monkeypatch, tmp_path):
    """The CLI used to open a browser and go silent. On a headless box, over
    SSH, or in a container it printed nothing at all and then timed out."""
    from lemma_cli.cli_core.app import app
    from lemma_cli.cli_core.commands import system as system_mod

    order: list[str] = []
    monkeypatch.setattr(system_mod, "fetch_cli_auth_info", lambda **kw: {})

    def fake_flow(**kwargs):
        order.append(f"flow(timeout={kwargs['timeout']})")
        return _flow_result()

    monkeypatch.setattr(system_mod, "run_login_flow", fake_flow)

    result = _login_runner().invoke(
        app,
        ["--config-file", str(tmp_path / "c.json"), "auth", "login", "--no-init"],
    )

    assert result.exit_code == 0, result.output
    announced = " ".join(result.stderr.split())
    assert _auth_url() in announced
    assert "300s" in announced
    # 300s, not the 60s HTTP --timeout: a first login is email + SSO + 2FA.
    assert order == [f"flow(timeout={system_mod.LOGIN_WAIT_SECONDS})"]


def test_login_wait_is_separate_from_the_http_timeout(monkeypatch, tmp_path):
    from lemma_cli.cli_core.app import app
    from lemma_cli.cli_core.commands import system as system_mod

    seen: dict[str, float] = {}

    def fake_info(**kwargs):
        seen["info_timeout"] = kwargs["timeout"]
        return {}

    def fake_flow(**kwargs):
        seen["flow_timeout"] = kwargs["timeout"]
        return _flow_result()

    monkeypatch.setattr(system_mod, "fetch_cli_auth_info", fake_info)
    monkeypatch.setattr(system_mod, "run_login_flow", fake_flow)

    result = _login_runner().invoke(
        app,
        [
            "--config-file",
            str(tmp_path / "c.json"),
            "--timeout",
            "5",
            "auth",
            "login",
            "--no-init",
            "--wait",
            "120",
        ],
    )

    assert result.exit_code == 0, result.output
    # The reachability probe keeps the short HTTP budget so an unreachable
    # server fails in seconds instead of hanging for the whole login wait.
    assert seen["info_timeout"] == 5.0
    assert seen["flow_timeout"] == 120.0


def test_login_timeout_message_names_the_flag_and_the_url(monkeypatch, tmp_path):
    from lemma_sdk.auth import LoginTimeoutError

    from lemma_cli.cli_core.app import app
    from lemma_cli.cli_core.commands import system as system_mod

    monkeypatch.setattr(system_mod, "fetch_cli_auth_info", lambda **kw: {})

    def timed_out(**kwargs):
        raise LoginTimeoutError(
            "Timed out waiting for browser login after 300 seconds."
        )

    monkeypatch.setattr(system_mod, "run_login_flow", timed_out)

    result = _login_runner().invoke(
        app, ["--config-file", str(tmp_path / "c.json"), "auth", "login"]
    )

    assert result.exit_code == 1, result.output
    flat = " ".join(result.stderr.split())
    assert "--wait" in flat
    assert _auth_url() in flat


def test_login_fails_fast_when_the_server_is_unreachable(monkeypatch, tmp_path):
    """The probe runs before the long wait exactly so this does not take 300s."""
    from lemma_cli.cli_core.app import app
    from lemma_cli.cli_core.commands import system as system_mod

    def unreachable(**kwargs):
        raise ValueError("Unable to load CLI auth info from https://api.lemma.work")

    monkeypatch.setattr(system_mod, "fetch_cli_auth_info", unreachable)
    monkeypatch.setattr(
        system_mod,
        "run_login_flow",
        lambda **kw: pytest.fail("must not block on an unreachable server"),
    )

    result = _login_runner().invoke(
        app, ["--config-file", str(tmp_path / "c.json"), "auth", "login"]
    )

    assert result.exit_code == 1, result.output
    assert "Unable to load CLI auth info" in " ".join(result.stderr.split())
