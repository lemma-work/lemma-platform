"""The shell profile that lets `gh` use the credential the bridge writes.

`gh` does not read git's credential file, so without this an agent gets a
working `git push` next to a `gh` that says it is not logged in. The script
runs in every `bash -lc` the sandbox executes, so its failure modes are shell
failure modes: a missing file, a file it cannot parse, a token containing
characters that would break the surrounding command.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "sandbox-images/templates/workspace-github/lemma-profile.sh"
)


def _environment(credentials: str | None, tmp_path: Path) -> dict[str, str]:
    """Source the script with a credential file in place, report what it set."""
    home = tmp_path / "home"
    home.mkdir()
    script = SCRIPT.read_text()
    # The script reads a fixed absolute path; point it at the fixture instead
    # of writing to the real /tmp of whatever machine runs the tests.
    credentials_path = tmp_path / ".git-credentials"
    script = script.replace("/tmp/.git-credentials", str(credentials_path))
    if credentials is not None:
        credentials_path.write_text(credentials)

    result = subprocess.run(
        ["/bin/sh", "-c", f"{script}\nenv"],
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


def test_gh_gets_the_token_git_is_already_using(tmp_path: Path) -> None:
    environment = _environment(
        "https://x-access-token:ghu_secrettoken@github.com\n", tmp_path
    )

    assert environment["GH_TOKEN"] == "ghu_secrettoken"


def test_no_credential_file_leaves_gh_unauthenticated(tmp_path: Path) -> None:
    """A workspace with no connected GitHub account is the normal case. It must
    not export an empty GH_TOKEN, which `gh` treats as a configured-but-broken
    credential rather than as absent."""
    environment = _environment(None, tmp_path)

    assert "GH_TOKEN" not in environment


def test_an_unparseable_credential_file_leaves_gh_unauthenticated(
    tmp_path: Path,
) -> None:
    environment = _environment("https://someone@gitlab.example.com\n", tmp_path)

    assert "GH_TOKEN" not in environment


def test_only_the_github_entry_is_used(tmp_path: Path) -> None:
    """git's store helper keeps one line per host. Picking the wrong line would
    send another host's token to GitHub."""
    environment = _environment(
        "https://user:gitlabtoken@gitlab.example.com\n"
        "https://x-access-token:ghu_thegithubone@github.com\n",
        tmp_path,
    )

    assert environment["GH_TOKEN"] == "ghu_thegithubone"


@pytest.mark.parametrize("noise", ["$(id)", "`id`", "a b", "a'b", 'a"b'])
def test_a_token_is_never_re_evaluated_by_the_shell(
    noise: str, tmp_path: Path
) -> None:
    """The token is attacker-influenced only in the sense that it comes from
    outside; still, it reaches the environment through a shell, so it must be
    carried as data and never as something the shell expands again."""
    environment = _environment(
        f"https://x-access-token:{noise}@github.com\n", tmp_path
    )

    assert environment["GH_TOKEN"] == noise


def test_the_update_notifier_and_pager_are_disabled(tmp_path: Path) -> None:
    """Nobody is watching this shell: a version check adds latency and stray
    stderr, and a pager waits forever for a keypress that never comes."""
    environment = _environment(None, tmp_path)

    assert environment["GH_NO_UPDATE_NOTIFIER"] == "1"
    assert environment["GH_PAGER"] == "cat"
