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
from lemma_sdk import config
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

    _with_session(config_path)
    locked: list[Path] = []
    real_lock = config.config_lock

    @contextlib.contextmanager
    def spy(path):
        locked.append(path)
        with real_lock(path):
            yield

    monkeypatch.setattr(config, "config_lock", spy)

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


# --- first-run notice -----------------------------------------------------


@pytest.fixture
def reporting(monkeypatch) -> list[dict]:
    """A CLI built with an ingestion key compiled in — the only case that sends
    anything. Delivery is captured rather than performed."""
    monkeypatch.setenv(telemetry.TELEMETRY_KEY_ENV, "phc-test-key")
    sent: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", sent.append)
    return sent


def test_the_first_reported_command_says_what_is_being_sent(
    config_path, reporting, capsys
):
    """Telemetry that starts arriving without the user having been told is the
    version that becomes a public complaint. The notice is printed once, on the
    first invocation that would actually report, and names the opt-out."""
    _with_session(config_path)

    telemetry.record_command("pods", exit_status="ok")

    err = " ".join(capsys.readouterr().err.split())
    assert "lemma telemetry off" in err
    assert "anonymous" in err.lower()
    assert len(reporting) == 1


def test_the_notice_is_printed_once_not_on_every_command(
    config_path, reporting, capsys
):
    _with_session(config_path)

    telemetry.record_command("pods", exit_status="ok")
    capsys.readouterr()
    telemetry.record_command("agents", exit_status="ok")

    assert capsys.readouterr().err == ""


def test_nothing_is_printed_when_nothing_is_being_sent(config_path, capsys):
    """No ingestion key compiled in — the case for every self-hosted and locally
    built CLI — means no reporting, so there is nothing to disclose."""
    _with_session(config_path)

    telemetry.record_command("pods", exit_status="ok")

    assert capsys.readouterr().err == ""


def test_opting_out_stops_the_notice_too(config_path, reporting, capsys):
    _with_session(config_path)
    telemetry.set_enabled(False)

    telemetry.record_command("pods", exit_status="ok")

    assert capsys.readouterr().err == ""
    assert reporting == []
