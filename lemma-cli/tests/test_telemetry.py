"""Telemetry must not be able to cost anyone their login.

It writes to ``~/.lemma/config.json`` — the file that holds the stored auth
session — so it has to write it the way every other writer does: under
``config_lock``, through ``save_config``'s atomic replace, and never by
rewriting the whole file from a read that may have failed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_cli.cli_core import telemetry
from lemma_sdk.config import load_config, save_config


@pytest.fixture
def config_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setenv("LEMMA_CONFIG_FILE", str(path))
    return path


def _with_session(path: Path) -> None:
    save_config(
        path,
        {
            "active_server": "lemma-cloud",
            "servers": {
                "lemma-cloud": {
                    "base_url": "https://api.example.com",
                    "auth": {"email": "a@b.c", "refresh_token": "r-1"},
                    "defaults": {"pod_id": "pod-1"},
                }
            },
        },
    )


def test_writing_telemetry_state_preserves_the_stored_session(config_path):
    _with_session(config_path)

    telemetry.set_enabled(False)

    stored = load_config(config_path)
    server = stored["servers"]["lemma-cloud"]
    assert server["auth"] == {"email": "a@b.c", "refresh_token": "r-1"}
    assert server["defaults"] == {"pod_id": "pod-1"}
    assert stored["telemetry"]["enabled"] is False


def test_install_id_is_minted_once_and_kept(config_path):
    _with_session(config_path)

    first = telemetry.install_id()
    second = telemetry.install_id()

    assert first == second
    assert load_config(config_path)["telemetry"]["install_id"] == first


def test_a_corrupt_config_is_left_alone_rather_than_replaced(config_path):
    """The old writer read the file, got ``{}`` on any error, and wrote that
    back — turning a transient read failure into a wiped config."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{not json")

    telemetry.set_enabled(False)  # must not raise

    assert config_path.read_text() == "{not json"


def test_the_write_takes_the_config_lock(config_path, monkeypatch):
    """One writer, one lock: the token-refresh path in ``state.py`` takes
    ``config_lock`` around its read-modify-write, and this file has exactly one
    correct way to be written."""
    import contextlib

    import lemma_sdk.config as sdk_config

    _with_session(config_path)
    locked: list[Path] = []
    real_lock = sdk_config.config_lock

    @contextlib.contextmanager
    def spy(path):
        locked.append(path)
        with real_lock(path):
            yield

    monkeypatch.setattr(sdk_config, "config_lock", spy)

    telemetry.set_enabled(False)

    assert locked == [config_path]


def test_a_write_failure_is_never_fatal(config_path, monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("lemma_sdk.config.save_config", explode)

    telemetry.set_enabled(True)  # must not raise


def test_reported_cli_version_is_the_real_one(config_path):
    """The published distribution is ``lemma-terminal``; looking up
    ``lemma-cli`` made every event carry ``cli_version: "unknown"``, which is
    the one dimension this telemetry exists to record."""
    from lemma_cli.cli_core.versions import cli_version

    assert telemetry._cli_version() == cli_version()
    assert telemetry._cli_version() != "unknown"


def test_status_reports_the_config_path(config_path):
    _with_session(config_path)

    status = telemetry.status()

    assert status["config_path"] == str(config_path)
    assert json.loads(config_path.read_text())["active_server"] == "lemma-cloud"
