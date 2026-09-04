"""Two rules the CLI states and used not to keep.

1. Diagnostics go to stderr, results to stdout, so `--output json` is pipeable
   even when the command fails (``cli_core/state.py``).
2. `lemma config show` is the tool for "why is this hitting the wrong pod", so
   it must name the project env files that were loaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_lemma_env(monkeypatch):
    """`load_project_env` writes os.environ directly; restore it fully."""
    import os

    saved = {k: v for k, v in os.environ.items() if k.startswith("LEMMA_")}
    for key in list(saved):
        monkeypatch.delenv(key, raising=False)
    yield
    for key in [k for k in os.environ if k.startswith("LEMMA_")]:
        del os.environ[key]
    os.environ.update(saved)


def _repo(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _invoke(args: list[str], tmp_path: Path, cwd: Path | None = None):
    cfg = tmp_path / "config.json"
    if cwd is not None:
        import os

        previous = Path.cwd()
        os.chdir(cwd)
        try:
            return runner.invoke(app, ["--config-file", str(cfg), *args])
        finally:
            os.chdir(previous)
    return runner.invoke(app, ["--config-file", str(cfg), *args])


# --- diagnostics on stderr ------------------------------------------------


def test_json_output_stays_parseable_when_a_command_fails(tmp_path, monkeypatch):
    """The failure funnel is `fail()`; it used to print on stdout, so a `| jq`
    or a `json.load` broke on exactly the runs that needed reading."""
    from lemma_cli.cli_core.commands import agents

    monkeypatch.setattr(agents, "run_with_client", lambda ctx, fn: None)

    result = _invoke(["--json", "agents", "delete", "nope"], tmp_path)

    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "--yes" in result.stderr
    # An empty stdout is a legitimate "no payload"; anything printed there must
    # still parse. This is the assertion a scripted consumer actually makes.
    assert json.loads(result.stdout or "null") is None


def test_a_committed_token_is_reported_and_ignored(tmp_path):
    """The loader already computed `token_in_committed_file`; nothing rendered
    it, so the CLI silently switched servers and then blamed an env var the
    user had never set."""
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_TOKEN=committed-oops\n")

    result = _invoke(["config", "show"], tmp_path, cwd=root)

    assert result.exit_code == 0, result.output
    flat = " ".join(result.stderr.split())
    assert "LEMMA_TOKEN" in flat
    assert ".lemma.env" in flat
    # The warning is a diagnostic, so `config show`'s own output stays on stdout.
    assert "server" in result.stdout


# --- config show ----------------------------------------------------------


def test_config_show_names_the_loaded_env_files(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    (root / ".lemma.local.env").write_text("LEMMA_POD_ID=pod_local\n")

    result = _invoke(["config", "show"], tmp_path, cwd=root)

    assert result.exit_code == 0, result.output
    flat = " ".join(result.stdout.split())
    assert "none found" not in flat
    assert ".lemma.env" in flat
    assert ".lemma.local.env" in flat
    assert "LEMMA_POD_ID" in flat
    # the folder is named too, so "which .lemma.env" is answerable
    assert str(root) in "".join(result.stdout.split())


def test_config_show_says_none_found_without_a_project_file(tmp_path):
    plain = _repo(tmp_path)

    result = _invoke(["config", "show"], tmp_path, cwd=plain)

    assert result.exit_code == 0, result.output
    assert "(none found)" in " ".join(result.stdout.split())


def test_config_show_json_still_carries_the_loader_summary(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")

    result = _invoke(["--json", "config", "show"], tmp_path, cwd=root)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["project_env"]["files"] == [".lemma.env"]
    assert payload["project_env"]["project_dir"] == str(root)


# --- doctor: CLI/SDK pairing ----------------------------------------------


def _doctor_without_a_server(monkeypatch):
    """`doctor` with the server lookup stubbed out: the pairing check is local."""
    from lemma_cli.cli_core.commands import system

    monkeypatch.setattr(
        system, "_fetch_server_api_version", lambda _state: (None, "unreachable")
    )


def test_doctor_reports_an_sdk_that_does_not_match_this_cli(tmp_path, monkeypatch):
    """The CLI reaches into generated SDK request classes, so a CLI pinned to an
    older release paired with a newer `lemma-sdk` fails as an AttributeError
    traceback. `doctor` already knew both versions and never compared them."""
    from lemma_cli.cli_core import versions

    _doctor_without_a_server(monkeypatch)
    monkeypatch.setattr(versions, "sdk_dist_version", lambda: "9.9.9")

    result = _invoke(["--json", "doctor"], tmp_path)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["sdk_pairing"] == "version_mismatch"
    assert versions.cli_version() != "9.9.9"


def test_doctor_says_nothing_is_wrong_when_the_pairing_matches(tmp_path, monkeypatch):
    _doctor_without_a_server(monkeypatch)

    result = _invoke(["--json", "doctor"], tmp_path)

    assert json.loads(result.stdout)["sdk_pairing"] == "in_sync"
