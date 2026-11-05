from __future__ import annotations

from pathlib import Path

import pytest

from lemma_cli.cli_core import state as cli_state
from lemma_sdk.errors import LemmaAPIError


def _state(
    config_path: Path, config: dict, base_url: str = "https://api.example.test"
) -> cli_state.CliState:
    root_config = {
        "active_server": "lemma-cloud",
        "servers": {"lemma-cloud": config},
    }
    return cli_state.CliState(
        config_path=config_path,
        config=config,
        root_config=root_config,
        server="lemma-cloud",
        server_source="config",
        server_read_only=False,
        base_url=base_url,
        auth_url=None,
        token=None,
        timeout=5.0,
        no_verify_ssl=False,
        output="json",
    )


def test_refresh_and_retry_reuses_tokens_rotated_by_another_process(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.json"
    stale_config = {
        "auth": {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
        }
    }
    fresh_config = {
        "auth": {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
        },
        "defaults": {},
    }
    cli_state.save_config(
        config_path,
        {"active_server": "lemma-cloud", "servers": {"lemma-cloud": fresh_config}},
    )
    state = _state(config_path, stale_config)
    calls = {"fn": 0, "refresh": 0}

    def fake_refresh(**kwargs):
        calls["refresh"] += 1
        raise AssertionError("refresh should not reuse the stale token")

    def fn():
        calls["fn"] += 1
        if calls["fn"] == 1:
            raise LemmaAPIError(status_code=401, message="expired")
        return cli_state.get_access_token_from_config(state.config)

    monkeypatch.setattr(cli_state, "refresh_cli_session", fake_refresh)

    assert cli_state.refresh_and_retry(state, fn) == "new-access"
    assert calls == {"fn": 2, "refresh": 0}
    assert state.config == fresh_config


def test_refresh_and_retry_persists_rotated_refresh_token(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    initial_config = {
        "auth": {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
        },
        "defaults": {"org_id": "org_123"},
    }
    cli_state.save_config(
        config_path,
        {"active_server": "lemma-cloud", "servers": {"lemma-cloud": initial_config}},
    )
    state = _state(config_path, initial_config)
    calls = {"fn": 0}

    def fake_refresh(**kwargs):
        assert kwargs["base_url"] == "https://api.example.test"
        assert kwargs["refresh_token"] == "old-refresh"
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "access_token_expires_at": 123,
            "session_handle": "session",
            "user_id": "user",
        }

    def fn():
        calls["fn"] += 1
        if calls["fn"] == 1:
            raise LemmaAPIError(status_code=401, message="expired")
        return cli_state.get_refresh_token_from_config(state.config)

    monkeypatch.setattr(cli_state, "refresh_cli_session", fake_refresh)

    assert cli_state.refresh_and_retry(state, fn) == "new-refresh"
    saved = cli_state.load_config(config_path)
    saved_context = saved["servers"]["lemma-cloud"]
    assert saved_context["auth"]["access_token"] == "new-access"
    assert saved_context["auth"]["refresh_token"] == "new-refresh"
    assert saved_context["refresh_token"] == "new-refresh"
    assert saved_context["defaults"] == {"org_id": "org_123"}


def test_refresh_auth_session_can_be_used_before_long_lived_commands(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.json"
    initial_config = {
        "auth": {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
        },
        "defaults": {},
    }
    cli_state.save_config(
        config_path,
        {"active_server": "lemma-cloud", "servers": {"lemma-cloud": initial_config}},
    )
    state = _state(config_path, initial_config)

    def fake_refresh(**kwargs):
        assert kwargs["refresh_token"] == "old-refresh"
        return {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "access_token_expires_at": 123,
            "session_handle": "session",
            "user_id": "user",
        }

    monkeypatch.setattr(cli_state, "refresh_cli_session", fake_refresh)

    assert cli_state.refresh_auth_session(state) is True
    assert cli_state.get_access_token_from_config(state.config) == "fresh-access"
    saved = cli_state.load_config(config_path)
    saved_context = saved["servers"]["lemma-cloud"]
    assert saved_context["auth"]["access_token"] == "fresh-access"
    assert saved_context["refresh_token"] == "fresh-refresh"


class _RefreshServer:
    """A loopback stand-in for the server's refresh route.

    The real `refresh_cli_session` posts to it, so these tests cross the HTTP
    boundary instead of replacing a function inside the module under test.
    """

    def __init__(self) -> None:
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
                outer.hits += 1
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.dumps(
                    {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "access_token_expires_at": 123,
                        "session_handle": "session",
                        "user_id": "user",
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def refresh_server():
    server = _RefreshServer()
    yield server
    server.close()


def _expired_then_pod(state: cli_state.CliState, calls: dict):
    from lemma_cli.cli_core.context import selected_org, selected_pod

    def fn():
        calls["fn"] += 1
        if calls["fn"] == 1:
            raise LemmaAPIError(status_code=401, message="expired")
        return selected_org(state), selected_pod(state)

    return fn


def test_pod_and_org_flags_survive_a_token_refresh(tmp_path, refresh_server):
    # `lemma --org O --pod P ...` puts the flags under `_runtime`, which is not
    # on disk. The refresh used to replace state.config with the reloaded
    # config, so the retried call ran against the saved default pod, or none.
    config_path = tmp_path / "config.json"
    saved_config = {
        "auth": {"access_token": "old-access", "refresh_token": "old-refresh"},
        "defaults": {"org_id": "org-default", "pod_id": "pod-default"},
    }
    cli_state.save_config(
        config_path,
        {"active_server": "lemma-cloud", "servers": {"lemma-cloud": saved_config}},
    )
    state = _state(
        config_path,
        {
            **saved_config,
            "_runtime": {"org": "org-flag", "pod": "pod-flag", "conversation": None},
        },
        base_url=refresh_server.base_url,
    )
    calls = {"fn": 0}

    result = cli_state.refresh_and_retry(state, _expired_then_pod(state, calls))

    assert result == ("org-flag", "pod-flag")
    assert calls["fn"] == 2
    assert refresh_server.hits == 1
    assert cli_state.get_access_token_from_config(state.config) == "new-access"
    saved = cli_state.load_config(config_path)
    saved_server = saved["servers"]["lemma-cloud"]
    assert saved_server["defaults"] == {
        "org_id": "org-default",
        "pod_id": "pod-default",
    }
    assert "_runtime" not in config_path.read_text()


def test_pod_flag_survives_tokens_rotated_by_another_process(tmp_path, refresh_server):
    config_path = tmp_path / "config.json"
    fresh_config = {
        "auth": {"access_token": "new-access", "refresh_token": "new-refresh"},
        "defaults": {"pod_id": "pod-default"},
    }
    cli_state.save_config(
        config_path,
        {"active_server": "lemma-cloud", "servers": {"lemma-cloud": fresh_config}},
    )
    state = _state(
        config_path,
        {
            "auth": {"access_token": "old-access", "refresh_token": "old-refresh"},
            "defaults": {"pod_id": "pod-default"},
            "_runtime": {"org": "org-flag", "pod": "pod-flag"},
        },
        base_url=refresh_server.base_url,
    )

    calls = {"fn": 0}

    result = cli_state.refresh_and_retry(state, _expired_then_pod(state, calls))

    assert result == ("org-flag", "pod-flag")
    assert cli_state.get_access_token_from_config(state.config) == "new-access"
    # The tokens on disk were already fresh, so nothing asked the server.
    assert refresh_server.hits == 0
    assert "_runtime" not in config_path.read_text()
