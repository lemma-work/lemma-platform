"""The update check and `lemma update`.

The check is deliberately quiet: it never runs in front of a command, it reads
the version off the server the command already dialed, and it prints one line on
stderr once per released version. These tests pin all four of those.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner
from typer.main import get_group

from lemma_cli.cli_core import errors as errors_mod
from lemma_cli.cli_core import update as update_mod
from lemma_cli.cli_core import versions as versions_mod
from lemma_cli.cli_core.app import _invoked_command, app

runner = CliRunner()


@pytest.fixture
def config_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setenv("LEMMA_CONFIG_FILE", str(path))
    return path


def _updatable() -> update_mod.InstallKind:
    return update_mod.InstallKind("installed", True, "")


def _installed_version(monkeypatch, version: str) -> None:
    monkeypatch.setattr(versions_mod, "cli_version", lambda: version)


# --- version comparison ---------------------------------------------------


@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("0.7.3", "0.7.2", True),
        ("0.8.0", "0.7.9", True),
        ("1.0.0", "0.9.9", True),
        ("0.7.2", "0.7.2", False),
        ("0.7.1", "0.7.2", False),
        # Anything unparseable never produces a notice: a pre-release, or the
        # "unknown" a source checkout reports, must not nag.
        ("0.7.3rc1", "0.7.2", False),
        ("0.7.3", "unknown", False),
        ("", "0.7.2", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert update_mod.is_newer(candidate, current) is expected


# --- the notice -----------------------------------------------------------


def test_notice_names_the_version_and_the_command(config_path, monkeypatch, capsys):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    _installed_version(monkeypatch, "0.7.2")
    update_mod._write_block({"latest_version": "0.7.3"})

    update_mod.notify_if_available()

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean so --json remains pipeable
    assert "0.7.3" in captured.err
    assert "lemma update" in captured.err


def test_notice_prints_once_per_released_version(config_path, monkeypatch, capsys):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    _installed_version(monkeypatch, "0.7.2")
    update_mod._write_block({"latest_version": "0.7.3"})

    update_mod.notify_if_available()
    assert "0.7.3" in capsys.readouterr().err

    update_mod.notify_if_available()
    assert capsys.readouterr().err == ""

    # A newer release speaks up again.
    update_mod._write_block({"latest_version": "0.7.4"})
    update_mod.notify_if_available()
    assert "0.7.4" in capsys.readouterr().err


def test_no_notice_when_already_current(config_path, monkeypatch, capsys):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    _installed_version(monkeypatch, "0.7.3")
    update_mod._write_block({"latest_version": "0.7.3"})

    update_mod.notify_if_available()

    assert capsys.readouterr().err == ""


def test_no_notice_where_update_cannot_help(config_path, monkeypatch, capsys):
    """PIP_PREFIX means the image owns this install, so `lemma update` would
    shadow it rather than replace it — advertising the command would be
    advertising a no-op."""
    monkeypatch.setenv("PIP_PREFIX", "/workspace/.python")
    _installed_version(monkeypatch, "0.7.2")
    update_mod._write_block({"latest_version": "0.7.3"})

    update_mod.notify_if_available()

    assert capsys.readouterr().err == ""


def test_env_var_disables_the_check(config_path, monkeypatch, capsys):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setenv(update_mod.DISABLE_ENV, "0")
    _installed_version(monkeypatch, "0.7.2")
    update_mod._write_block({"latest_version": "0.7.3"})

    update_mod.notify_if_available()

    assert capsys.readouterr().err == ""


# --- the background check -------------------------------------------------


def test_check_now_records_the_server_version(config_path, monkeypatch):
    monkeypatch.setattr(
        update_mod, "fetch_server_api_version", lambda url, **kw: ("0.9.1", None)
    )

    update_mod.check_now("https://api.example.com")

    block = update_mod._read_block()
    assert block["latest_version"] == "0.9.1"
    assert block["last_checked"] > 0


def test_a_failed_check_still_costs_only_one_attempt(config_path, monkeypatch):
    monkeypatch.setattr(
        update_mod,
        "fetch_server_api_version",
        lambda url, **kw: (None, "connection refused"),
    )

    update_mod.check_now("https://api.example.com")

    block = update_mod._read_block()
    assert "latest_version" not in block
    assert block["last_checked"] > 0  # an unreachable server is not re-dialed


def test_check_writes_alongside_the_stored_session(config_path, monkeypatch):
    """The timestamp is a top-level key in the file the login session lives in,
    so writing it must leave everything else in there intact."""
    from lemma_sdk.config import load_config, save_config

    save_config(
        config_path,
        {
            "active_server": "lemma-cloud",
            "servers": {"lemma-cloud": {"auth": {"email": "a@b.c"}, "defaults": {}}},
        },
    )
    monkeypatch.setattr(
        update_mod, "fetch_server_api_version", lambda url, **kw: ("0.9.1", None)
    )

    update_mod.check_now("https://api.example.com")

    stored = load_config(config_path)
    assert stored["servers"]["lemma-cloud"]["auth"]["email"] == "a@b.c"
    assert stored[update_mod.CONFIG_KEY]["latest_version"] == "0.9.1"
    # Not underscore-prefixed: save_config strips those as in-memory-only state.
    assert not update_mod.CONFIG_KEY.startswith("_")


def test_background_check_is_skipped_inside_the_interval(config_path, monkeypatch):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(errors_mod, "_dialed_base_url", "https://api.example.com")
    update_mod._write_block({"last_checked": time.time()})
    started: list[str] = []
    monkeypatch.setattr(update_mod, "check_now", lambda url: started.append(url))

    update_mod.maybe_check_in_background()

    assert started == []


def test_background_check_needs_a_dialed_server(config_path, monkeypatch):
    """`lemma --help` and every offline command must touch no network."""
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(errors_mod, "_dialed_base_url", None)
    started: list[str] = []
    monkeypatch.setattr(update_mod, "check_now", lambda url: started.append(url))

    update_mod.maybe_check_in_background()

    assert started == []


def test_background_check_runs_once_the_interval_has_passed(config_path, monkeypatch):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(errors_mod, "_dialed_base_url", "https://api.example.com")
    update_mod._write_block(
        {"last_checked": time.time() - update_mod.CHECK_INTERVAL_SECONDS - 1}
    )
    started: list[str] = []
    monkeypatch.setattr(update_mod, "check_now", lambda url: started.append(url))

    update_mod.maybe_check_in_background()
    # The check runs on a daemon thread; give it a moment to be scheduled.
    for _ in range(200):
        if started:
            break
        time.sleep(0.005)

    assert started == ["https://api.example.com"]


# --- install classification ----------------------------------------------


def test_install_kind_refuses_under_pip_prefix(monkeypatch):
    monkeypatch.setenv("PIP_PREFIX", "/workspace/.python")
    kind = update_mod.install_kind()
    assert kind.kind == "overlay"
    assert not kind.can_update
    assert "shadow" in kind.reason


def test_install_kind_refuses_a_source_checkout(monkeypatch, tmp_path):
    import lemma_cli

    monkeypatch.delenv("PIP_PREFIX", raising=False)
    checkout = tmp_path / "repo" / "lemma_cli" / "__init__.py"
    checkout.parent.mkdir(parents=True)
    checkout.write_text("")
    monkeypatch.setattr(lemma_cli, "__file__", str(checkout))

    kind = update_mod.install_kind()

    assert kind.kind == "checkout"
    assert not kind.can_update


def test_install_kind_accepts_a_site_packages_install(monkeypatch, tmp_path):
    import lemma_cli

    monkeypatch.delenv("PIP_PREFIX", raising=False)
    installed = tmp_path / "venv" / "site-packages" / "lemma_cli" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("")
    monkeypatch.setattr(lemma_cli, "__file__", str(installed))

    kind = update_mod.install_kind()

    assert kind.kind == "installed"
    assert kind.can_update


# --- lemma update ---------------------------------------------------------


def _invoke(args: list[str], tmp_path: Path):
    return runner.invoke(app, ["--config-file", str(tmp_path / "c.json"), *args])


def test_update_refuses_in_an_overlaid_image(monkeypatch, tmp_path):
    monkeypatch.setenv("PIP_PREFIX", "/workspace/.python")

    result = _invoke(["update"], tmp_path)

    assert result.exit_code == 1, result.output
    flat = " ".join(result.stderr.split())
    assert "shadow" in flat
    assert "Rebuild or update the image" in flat
    # No `uv tool install` suggestion: running it here would install the second
    # copy the message just explained is the problem.
    assert "uv tool install" not in flat
    assert result.stdout == ""


def test_update_runs_uv_tool_install(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(update_mod, "_find_uv", lambda: "/usr/local/bin/uv")
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "subprocess.run", lambda command, **kw: (calls.append(command), Completed())[1]
    )

    result = _invoke(["--json", "update"], tmp_path)

    assert result.exit_code == 0, result.output
    assert calls == [
        ["/usr/local/bin/uv", "tool", "install", "--force", "lemma-terminal"]
    ]
    assert json.loads(result.stdout)["action"] == "upgraded"


def test_update_pins_an_explicit_version(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(update_mod, "_find_uv", lambda: "/usr/local/bin/uv")
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "subprocess.run", lambda command, **kw: (calls.append(command), Completed())[1]
    )

    result = _invoke(["--json", "update", "--version", "9.9.9"], tmp_path)

    assert result.exit_code == 0, result.output
    assert calls[0][-1] == "lemma-terminal==9.9.9"


def test_update_short_circuits_when_the_version_already_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    ran: list[object] = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a))

    result = _invoke(
        ["--json", "update", "--version", versions_mod.cli_version()], tmp_path
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "already_current"
    assert ran == []


def test_update_failure_names_the_manual_command(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(update_mod, "_find_uv", lambda: None)

    result = _invoke(["update"], tmp_path)

    assert result.exit_code == 1, result.output
    flat = " ".join(result.stderr.split())
    assert "uv is not installed" in flat
    assert "uv tool install --force lemma-terminal" in flat


def test_update_reports_a_failed_uv_run_without_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(update_mod, "_find_uv", lambda: "/usr/local/bin/uv")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "No solution found when resolving tool dependencies"

    monkeypatch.setattr("subprocess.run", lambda command, **kw: Completed())

    result = _invoke(["update"], tmp_path)

    assert result.exit_code == 1, result.output
    flat = " ".join(result.stderr.split())
    assert "No solution found" in flat
    assert "uv tool install --force lemma-terminal" in flat


# --- telemetry dimension --------------------------------------------------


def test_invoked_command_knows_every_registered_command():
    """A command missing from `_invoked_command`'s allowlist is reported as
    `None` by telemetry, which is how `doctor`, `schema`, `feedback`, `get` and
    `describe` all went unmeasured."""
    for name in get_group(app).commands:
        assert _invoked_command([name]) == name, (
            f"{name!r} is registered but unknown to _invoked_command — telemetry "
            "would report it as None"
        )


def test_invoked_command_drops_an_unknown_token():
    assert _invoked_command(["--json", "update"]) == "update"
    assert _invoked_command(["/etc/passwd"]) is None
