"""Shell defaults for `gh` in a workspace.

The credential itself is not in this script, and that is the point: `gh` reads
it from `GH_CONFIG_DIR`, written by the credential bridge, so the token never
enters the environment where an ordinary `env` would print it into a tool
result. What this file has to get right is pointing `gh` at that directory and
keeping the CLI non-interactive.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "sandbox-images/templates/workspace-github/lemma-profile.sh"
)


def _environment(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["/bin/sh", "-c", f"{SCRIPT.read_text()}\nenv"],
        capture_output=True,
        text=True,
        check=True,
        cwd=home,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def test_gh_is_pointed_at_the_directory_the_bridge_writes(tmp_path: Path) -> None:
    """This is the whole mechanism: the bridge writes hosts.yml here, so `gh`
    ends up authenticated as the account `git` is already using."""
    assert _environment(tmp_path)["GH_CONFIG_DIR"] == "/tmp/lemma-gh"


def test_the_token_never_enters_the_environment(tmp_path: Path) -> None:
    """A shell profile that exported GH_TOKEN would put the raw credential in
    every process the agent starts, so `env` -- an entirely ordinary agent
    command -- would write it into the tool result and the transcript."""
    environment = _environment(tmp_path)

    assert "GH_TOKEN" not in environment
    assert "GITHUB_TOKEN" not in environment


def test_the_credential_lives_outside_the_durable_volume(tmp_path: Path) -> None:
    """Session-scoped, like git's credential file. /workspace survives a
    workspace recreation and must not accumulate credentials."""
    config_dir = _environment(tmp_path)["GH_CONFIG_DIR"]

    assert config_dir.startswith("/tmp/")


def test_the_update_notifier_and_pager_are_disabled(tmp_path: Path) -> None:
    """Nobody is watching this shell: a version check adds latency and stray
    stderr, and a pager waits forever for a keypress that never comes."""
    environment = _environment(tmp_path)

    assert environment["GH_NO_UPDATE_NOTIFIER"] == "1"
    assert environment["GH_PAGER"] == "cat"
