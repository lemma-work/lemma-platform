"""`make dev-clean` against real repositories: what goes, and what never does.

Every test builds a throwaway origin, a clone and its worktrees in a temporary
directory and runs the script as `make` does, in a subprocess from inside the
checkout. The clock is moved forward with `--now` rather than backdating files,
so "idle" means the same thing it does on a real machine.

The rules worth a test are the ones that lose work if they are wrong: a dry run
that writes, a dirty or unpushed worktree removed, the current worktree
removed out from under the shell running the command.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "dev_disk_hygiene.py"
DAY = 86400

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.test",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.test",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **GIT_ENV, **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def commit(cwd: Path, name: str, content: str, *, days_ago: float = 0) -> str:
    (cwd / name).write_text(content)
    git(cwd, "add", name)
    stamp = f"@{int(time.time() - days_ago * DAY)} +0000"
    git(
        cwd,
        "commit",
        "-q",
        "-m",
        f"change {name}",
        env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )
    return git(cwd, "rev-parse", "HEAD").strip()


def hygiene(cwd: Path, *args: str, days_later: float = 0) -> str:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--no-gh",
            "--no-docker",
            "--no-caches",
            "--now",
            str(time.time() + days_later * DAY),
            *args,
        ],
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    main = tmp_path / "main"
    git(tmp_path, "clone", "-q", str(origin), str(main))
    git(main, "checkout", "-q", "-b", "main")
    commit(main, "README", "hello\n", days_ago=60)
    # As in the real repository: build output is ignored, not untracked.
    commit(main, ".gitignore", "target/\nnode_modules/\n.venv/\n", days_ago=60)
    git(main, "push", "-q", "-u", "origin", "main")
    return main


def add_worktree(main: Path, name: str) -> Path:
    path = main.parent / name
    git(main, "worktree", "add", "-q", "-b", name, str(path), "main")
    return path


def squash_merge(main: Path, worktree: Path, branch: str) -> None:
    """Land a branch the way GitHub's squash merge does: one new commit."""
    git(main, "fetch", "-q", "origin")
    git(main, "merge", "-q", "--squash", f"origin/{branch}")
    git(main, "commit", "-q", "-m", f"{branch} (squashed)")
    git(main, "push", "-q", "origin", "main")
    git(main, "fetch", "-q", "origin")


def merged_worktree(main: Path, name: str) -> Path:
    worktree = add_worktree(main, name)
    commit(worktree, f"{name}.txt", "work\n")
    git(worktree, "push", "-q", "-u", "origin", name)
    squash_merge(main, worktree, name)
    return worktree


def worktree_paths(main: Path) -> set[str]:
    return {
        line.split(" ", 1)[1]
        for line in git(main, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    }


def test_a_dry_run_changes_nothing(checkout: Path) -> None:
    merged = merged_worktree(checkout, "landed")
    target = merged / "desktop" / "target" / "debug"
    target.mkdir(parents=True)
    (target / "artifact").write_text("x")
    before = (
        worktree_paths(checkout),
        git(checkout, "for-each-ref"),
        sorted(p.name for p in merged.iterdir()),
    )

    output = hygiene(checkout, days_later=45)

    assert "remove worktree" in output
    assert "branch landed" in output
    assert "Dry run: nothing was changed" in output
    after = (
        worktree_paths(checkout),
        git(checkout, "for-each-ref"),
        sorted(p.name for p in merged.iterdir()),
    )
    assert after == before
    assert (target / "artifact").exists()


def test_a_merged_clean_idle_worktree_is_removed(checkout: Path) -> None:
    merged = merged_worktree(checkout, "landed")

    output = hygiene(checkout, "--apply", days_later=3)

    assert "merged by squash" in output or "merged by ancestor" in output
    assert str(merged.resolve()) not in {
        str(Path(p).resolve()) for p in worktree_paths(checkout)
    }
    assert not merged.exists()


def test_a_worktree_that_is_still_in_use_is_kept(checkout: Path) -> None:
    merged = merged_worktree(checkout, "landed")

    hygiene(checkout, "--apply", days_later=1)

    assert merged.exists()


def test_uncommitted_changes_keep_a_merged_worktree(checkout: Path) -> None:
    merged = merged_worktree(checkout, "landed")
    (merged / "notes.txt").write_text("not committed\n")

    output = hygiene(checkout, "--apply", days_later=3)

    assert merged.exists()
    assert "uncommitted changes" in output


def test_commits_on_no_remote_keep_a_merged_worktree(checkout: Path) -> None:
    # Landed by squash, but the branch itself was never pushed: the patch is
    # on main, the commits are nowhere else, and only a person can say they
    # are disposable.
    worktree = add_worktree(checkout, "never-pushed")
    commit(worktree, "local.txt", "only here\n")
    git(checkout, "merge", "-q", "--squash", "never-pushed")
    git(checkout, "commit", "-q", "-m", "never-pushed (squashed)")
    git(checkout, "push", "-q", "origin", "main")

    output = hygiene(checkout, "--apply", days_later=3)

    assert worktree.exists()
    assert "1 commit(s) are on no remote" in output


def test_an_unmerged_worktree_is_kept(checkout: Path) -> None:
    worktree = add_worktree(checkout, "in-progress")
    commit(worktree, "wip.txt", "wip\n")
    git(worktree, "push", "-q", "-u", "origin", "in-progress")

    hygiene(checkout, "--apply", days_later=10)

    assert worktree.exists()


def test_the_current_worktree_is_never_removed(checkout: Path) -> None:
    merged = merged_worktree(checkout, "landed")

    output = hygiene(merged, "--apply", days_later=10)

    assert merged.exists()
    assert "the current worktree" in output


def test_old_merged_branches_are_deleted_and_the_rest_kept(checkout: Path) -> None:
    branches = {
        # name: (days since its commit, pushed, squash-merged into main)
        "old-landed": (40, True, True),
        "old-landed-remote-deleted": (40, True, True),
        "new-landed": (5, True, True),
        "old-unmerged": (40, True, False),
        "old-local": (40, False, False),
    }
    for name, (days_ago, pushed, _) in branches.items():
        git(checkout, "checkout", "-q", "-b", name, "main")
        commit(checkout, f"{name}.txt", "x\n", days_ago=days_ago)
        if pushed:
            git(checkout, "push", "-q", "-u", "origin", name)
        git(checkout, "checkout", "-q", "main")
    for name, (_, _, merged) in branches.items():
        if merged:
            git(checkout, "merge", "-q", "--squash", name)
            git(checkout, "commit", "-q", "-m", f"{name} (squashed)")
    git(checkout, "push", "-q", "origin", "main")
    # GitHub deletes the head branch after the merge. Without `gh` to say the
    # pull request carried these commits, a patch match is not proof they
    # were ever pushed, so the branch stays.
    git(checkout, "push", "-q", "origin", "--delete", "old-landed-remote-deleted")

    output = hygiene(checkout, "--apply")

    left = set(
        git(checkout, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    )
    assert "old-landed" not in left
    assert {
        "main",
        "old-landed-remote-deleted",
        "new-landed",
        "old-unmerged",
        "old-local",
    } <= left
    assert (
        "branch old-landed-remote-deleted: merged, but 1 commit(s) are on no remote"
        in output
    )


def test_an_old_branch_merged_by_ancestry_is_deleted(checkout: Path) -> None:
    git(checkout, "checkout", "-q", "-b", "fast-forwarded", "main")
    commit(checkout, "ff.txt", "x\n", days_ago=40)
    git(checkout, "push", "-q", "-u", "origin", "fast-forwarded")
    git(checkout, "checkout", "-q", "main")
    git(checkout, "merge", "-q", "--ff-only", "fast-forwarded")
    git(checkout, "push", "-q", "origin", "main")

    hygiene(checkout, "--apply")

    branches = set(
        git(checkout, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    )
    assert "fast-forwarded" not in branches
    assert "main" in branches


def test_rust_output_goes_from_an_idle_worktree_but_not_the_current_one(
    checkout: Path,
) -> None:
    idle = add_worktree(checkout, "idle")
    commit(idle, "wip.txt", "unmerged, so the worktree itself stays\n")
    git(idle, "push", "-q", "-u", "origin", "idle")
    for worktree in (checkout, idle):
        (worktree / "desktop" / "target" / "debug").mkdir(parents=True)
        (worktree / "desktop" / "target" / "debug" / "app").write_text("binary")

    hygiene(checkout, "--apply", days_later=4)

    assert not (idle / "desktop" / "target").exists()
    assert (checkout / "desktop" / "target" / "debug" / "app").exists()
    assert idle.exists()


def test_the_sweep_keeps_each_crates_newest_build_and_anything_recent(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from dev_disk_hygiene import stale_artifacts

    deps = tmp_path / "target" / "debug" / "deps"
    deps.mkdir(parents=True)
    old = time.time() - 30 * DAY

    def artifact(name: str, stamp: float) -> Path:
        path = deps / name
        path.write_text("x")
        os.utime(path, (stamp, stamp))
        return path

    superseded = artifact("libtokio-0000000000000001.rlib", old)
    newest = artifact("libtokio-0000000000000002.rlib", old + DAY)
    only_copy = artifact("libserde-00000000000000aa.rlib", old)
    recent = artifact("libhyper-00000000000000b1.rlib", time.time())
    artifact("libhyper-00000000000000b2.rlib", time.time())

    stale = stale_artifacts(tmp_path / "target", time.time() - 7 * DAY)

    assert stale == [superseded]
    assert newest not in stale and only_copy not in stale and recent not in stale
