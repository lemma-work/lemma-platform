"""The update check and `lemma update`.

The check is deliberately quiet: it never runs in front of a command, it reads
the version off the server the command already dialed, and it prints one line on
stderr at most once a day. These tests pin all four of those.
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


@pytest.fixture(autouse=True)
def no_suggestion_from_the_server(monkeypatch):
    """Start every test as if no response in this process named a release."""
    monkeypatch.setattr("lemma_sdk.transport._suggested_cli", None)


def _server_suggests(monkeypatch, version: str) -> None:
    monkeypatch.setattr("lemma_sdk.transport._suggested_cli", version)


@pytest.fixture
def installed(monkeypatch, tmp_path) -> None:
    """A real `uv tool` install, where `lemma update` can act and a notice is due."""
    import lemma_cli

    monkeypatch.delenv("PIP_PREFIX", raising=False)
    location = tmp_path / "venv" / "site-packages" / "lemma_cli" / "__init__.py"
    monkeypatch.setattr(lemma_cli, "__file__", str(location))


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


def test_notice_prints_at_most_once_a_day(config_path, installed, monkeypatch, capsys):
    _installed_version(monkeypatch, "0.7.2")
    update_mod._write_block({"latest_version": "0.7.3"})

    update_mod.notify_if_available()
    assert "0.7.3" in capsys.readouterr().err

    update_mod.notify_if_available()
    assert capsys.readouterr().err == ""

    # A day later, still behind: say it again.
    update_mod._write_block(
        {"last_notified": time.time() - update_mod.NOTICE_INTERVAL_SECONDS - 1}
    )
    update_mod.notify_if_available()
    assert "0.7.3" in capsys.readouterr().err


def test_the_servers_header_is_enough_for_a_notice(
    config_path, installed, monkeypatch, capsys
):
    """A response to this very command named a newer release: no background
    check has to have run first."""
    _installed_version(monkeypatch, "0.7.2")
    _server_suggests(monkeypatch, "0.8.0")

    update_mod.notify_if_available()

    assert "lemma 0.8.0 is available" in capsys.readouterr().err
    # Remembered, so the next invocation knows without asking again.
    assert update_mod._read_block()["latest_version"] == "0.8.0"


def test_the_newer_of_header_and_stored_version_wins(
    config_path, installed, monkeypatch, capsys
):
    _installed_version(monkeypatch, "0.7.2")
    update_mod._write_block({"latest_version": "0.9.0"})
    _server_suggests(monkeypatch, "0.8.0")

    update_mod.notify_if_available()

    assert "lemma 0.9.0 is available" in capsys.readouterr().err


def test_a_header_no_newer_than_this_cli_says_nothing(
    config_path, installed, monkeypatch, capsys
):
    _installed_version(monkeypatch, "0.8.0")
    _server_suggests(monkeypatch, "0.8.0")

    update_mod.notify_if_available()

    assert capsys.readouterr().err == ""


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


def _uv_reporting(calls: list[list[str]], installed: str | None):
    """A `subprocess.run` that records commands, and whose `uv tool list`
    reports ``installed`` (or nothing, when None)."""

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def run(command, **kw):
        calls.append(command)
        if command[1:3] == ["tool", "list"] and installed is not None:
            return Completed(f"lemma-terminal v{installed}\n- lemma\n")
        return Completed("")

    return run


@pytest.mark.parametrize(
    ("installed", "action"),
    [
        (None, "upgraded"),
        ("99.0.0", "upgraded"),
        # A server can suggest a release minutes before its packages reach
        # PyPI. The unpinned install then reinstalls this version, and that is
        # not an upgrade.
        ("current", "no_newer_release"),
    ],
)
def test_update_runs_uv_tool_install_and_reports_the_result(
    monkeypatch, tmp_path, installed, action
):
    if installed == "current":
        installed = versions_mod.cli_version()
    monkeypatch.setattr(update_mod, "install_kind", _updatable)
    monkeypatch.setattr(update_mod, "_find_uv", lambda: "/usr/local/bin/uv")
    calls: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", _uv_reporting(calls, installed))

    result = _invoke(["--json", "update"], tmp_path)

    assert result.exit_code == 0, result.output
    assert calls == [
        ["/usr/local/bin/uv", "tool", "install", "--force", "lemma-terminal"],
        ["/usr/local/bin/uv", "tool", "list"],
    ]
    payload = json.loads(result.stdout)
    assert payload["action"] == action
    assert payload["installed"] == installed


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


# --- what the CLI tells the server it is -----------------------------------


def test_the_cli_declares_itself_with_its_own_version(monkeypatch):
    _installed_version(monkeypatch, "0.7.2")
    environ: dict[str, str] = {}

    versions_mod.declare_client(environ)

    assert environ == {"LEMMA_CLIENT": "lemma-cli", "LEMMA_CLIENT_VERSION": "0.7.2"}


def test_a_caller_that_named_itself_keeps_its_name_and_version(monkeypatch):
    _installed_version(monkeypatch, "0.7.2")
    environ = {"LEMMA_CLIENT": "lemma-desktop"}

    versions_mod.declare_client(environ)

    assert environ == {"LEMMA_CLIENT": "lemma-desktop"}


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


# --- where the server's version is read from ------------------------------


class _Response:
    """Just enough of `urlopen`'s context manager for `fetch_server_api_version`."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _server(monkeypatch, routes: dict[str, dict]) -> list[str]:
    """Serve `routes`; 404 anything else. Returns the list of URLs dialed."""
    import urllib.error
    import urllib.request

    dialed: list[str] = []

    def _urlopen(url, **_kwargs):
        dialed.append(url)
        for path, payload in routes.items():
            if url.endswith(path):
                return _Response(payload)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return dialed


def test_version_comes_from_health_without_an_openapi_document(monkeypatch):
    """Production serves no OpenAPI document, which is every real deployment."""
    dialed = _server(monkeypatch, {"/health": {"status": "ok", "api_version": "0.7.2"}})

    assert update_mod.fetch_server_api_version("https://api.example.com") == (
        "0.7.2",
        None,
    )
    assert dialed == ["https://api.example.com/health"]


def test_a_server_older_than_the_health_field_falls_back_to_openapi(monkeypatch):
    dialed = _server(
        monkeypatch,
        {
            "/health": {"status": "ok", "loop_lag_seconds": 0.0},
            "/openapi.json": {"info": {"version": "0.7.1"}},
        },
    )

    assert update_mod.fetch_server_api_version("https://api.example.com") == (
        "0.7.1",
        None,
    )
    assert dialed == [
        "https://api.example.com/health",
        "https://api.example.com/openapi.json",
    ]


def test_a_server_that_answers_neither_reports_why_the_first_failed(monkeypatch):
    _server(monkeypatch, {})

    version, error = update_mod.fetch_server_api_version("https://api.example.com")

    assert version is None
    assert error is not None and "404" in error
