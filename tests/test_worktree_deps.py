"""`make worktree-deps`: when a worktree may borrow another's node_modules.

The rule worth a test is the one that ships a wrong install if it is wrong:
an install is reused only when it holds exactly what this worktree's lockfile
asks for. A stale install -- the main checkout's, typically -- must not be.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from worktree_deps import find_donor, install_matches, main

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.test",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.test",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

LEFT_PAD = {
    "version": "1.3.0",
    "resolved": "https://r/left-pad-1.3.0.tgz",
    "integrity": "sha512-a",
}
FSEVENTS = {
    "version": "2.3.3",
    "resolved": "https://r/fsevents.tgz",
    "integrity": "sha512-b",
    "optional": True,
}


def lockfile(path: Path, packages: dict) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    body = {"lockfileVersion": 3, "packages": {"": {"name": "app"}, **packages}}
    (path / "package-lock.json").write_text(json.dumps(body))
    return path / "package-lock.json"


def installed(project: Path, packages: dict) -> Path:
    node_modules = project / "node_modules"
    node_modules.mkdir(parents=True, exist_ok=True)
    (node_modules / ".package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": packages})
    )
    return node_modules


def test_an_install_of_exactly_the_lockfile_matches(tmp_path: Path) -> None:
    lock = lockfile(tmp_path, {"node_modules/left-pad": LEFT_PAD})
    assert install_matches(
        lock, installed(tmp_path, {"node_modules/left-pad": LEFT_PAD})
    )


def test_an_optional_package_for_another_platform_may_be_absent(tmp_path: Path) -> None:
    lock = lockfile(
        tmp_path, {"node_modules/left-pad": LEFT_PAD, "node_modules/fsevents": FSEVENTS}
    )
    assert install_matches(
        lock, installed(tmp_path, {"node_modules/left-pad": LEFT_PAD})
    )


def test_a_stale_missing_or_extra_install_does_not_match(tmp_path: Path) -> None:
    lock = lockfile(tmp_path, {"node_modules/left-pad": LEFT_PAD})
    older = {**LEFT_PAD, "version": "1.2.0", "integrity": "sha512-old"}

    assert not install_matches(
        lock, installed(tmp_path / "a", {"node_modules/left-pad": older})
    )
    assert not install_matches(lock, installed(tmp_path / "b", {}))
    assert not install_matches(
        lock,
        installed(
            tmp_path / "c",
            {"node_modules/left-pad": LEFT_PAD, "node_modules/extra": LEFT_PAD},
        ),
    )
    assert not install_matches(lock, tmp_path / "never-installed")


def test_the_donor_is_a_matching_worktree_never_a_stale_one(tmp_path: Path) -> None:
    here, stale, good = tmp_path / "here", tmp_path / "stale", tmp_path / "good"
    wanted = {"node_modules/left-pad": LEFT_PAD}
    lockfile(here / "app", wanted)
    installed(
        stale / "app", {"node_modules/left-pad": {**LEFT_PAD, "version": "0.1.0"}}
    )
    installed(good / "app", wanted)

    assert find_donor(here, "app", [here, stale, good]) == good / "app" / "node_modules"
    assert find_donor(here, "app", [here, stale]) is None


def test_cloning_copies_a_matching_install_into_a_new_worktree(tmp_path: Path) -> None:
    def git(cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            env={**os.environ, **GIT_ENV},
            check=True,
            capture_output=True,
        )

    main_checkout = tmp_path / "main"
    main_checkout.mkdir()
    git(main_checkout, "init", "-q", "-b", "main")
    wanted = {"node_modules/left-pad": LEFT_PAD}
    lockfile(main_checkout / "lemma-frontend", wanted)
    (main_checkout / ".gitignore").write_text("node_modules/\n")
    git(main_checkout, "add", ".")
    git(main_checkout, "commit", "-q", "-m", "init")
    donor = installed(main_checkout / "lemma-frontend", wanted)
    (donor / "left-pad").mkdir()
    (donor / "left-pad" / "index.js").write_text("module.exports = 1\n")
    worktree = tmp_path / "wt"
    git(main_checkout, "worktree", "add", "-q", "-b", "wt", str(worktree))

    # Only the frontend's own project: the SDK is not in this fake checkout.
    import worktree_deps

    original = worktree_deps.COMPONENTS
    worktree_deps.COMPONENTS = {"frontend": ["lemma-frontend"]}
    try:
        assert main(["--only", "frontend", "--root", str(worktree)]) == 0
    finally:
        worktree_deps.COMPONENTS = original

    cloned = worktree / "lemma-frontend" / "node_modules"
    assert (cloned / "left-pad" / "index.js").read_text() == "module.exports = 1\n"
    assert install_matches(worktree / "lemma-frontend" / "package-lock.json", cloned)


def test_a_linked_local_package_is_not_compared(tmp_path: Path) -> None:
    link = {"node_modules/lemma-sdk": {"resolved": "../lemma-typescript", "link": True}}
    lock = lockfile(tmp_path, {**link, "../lemma-typescript": {"version": "0.10.0"}})
    install = installed(
        tmp_path, {**link, "../lemma-typescript": {"version": "0.10.1"}}
    )
    assert install_matches(lock, install)
