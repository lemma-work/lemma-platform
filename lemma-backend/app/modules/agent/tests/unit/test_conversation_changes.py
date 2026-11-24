"""Reading a repository conversation's changes: the command and its parser.

The parser is checked on git's own output formats, and the command against a
real checkout laid out as a sandbox lays one out (``local_git``), so a rename,
a binary file and an untracked file are what git says they are rather than
what a fixture guessed.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from app.modules.agent.domain.conversation_changes import (
    LIST_BYTES,
    NOT_CHECKED_OUT_EXIT,
    ChangedFile,
    ChangesOutputError,
    changes_command,
    checkout_dir,
    parse_changes,
    parse_name_status,
    parse_numstat,
)
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.tests.local_git import LocalGit, local_git
from sandbox_runtime.paths import WORKSPACE_ROOT

_REPO = ProjectRepo(owner="acme", repo="web")


def _part(name: str, value: bytes) -> str:
    return f"{name} {base64.b64encode(value).decode()}"


def _meta(**values: str) -> bytes:
    fields = {
        "branch": "",
        "start": "",
        "base": "",
        "head": "",
        "ahead": "0",
        "dirty": "0",
        "more": "0",
    }
    fields.update(values)
    return "".join(f"{key}={value}\n" for key, value in fields.items()).encode()


def _output(*parts: str) -> str:
    return "\n".join(["lemma-changes 1", *parts]) + "\n"


def _z(*records: bytes) -> bytes:
    """Records as ``-z`` prints them, each ending in a NUL.

    Joined here rather than written as one literal: a NUL escape written next
    to a digit reads as an octal escape, which a formatter joining two
    literals produces without a word.
    """
    return b"".join(record + b"\x00" for record in records)


# -- git's formats ------------------------------------------------------------


def test_numstat_reads_plain_entries_renames_and_binary_files() -> None:
    records = [
        "3\t1\tsrc/app.py",
        "0\t0\t",
        "old name.txt",
        "new name.txt",
        "-\t-\tlogo.png",
    ]

    entries = parse_numstat(records)

    assert entries == [
        ("src/app.py", None, 3, 1, False),
        ("new name.txt", "old name.txt", 0, 0, False),
        ("logo.png", None, 0, 0, True),
    ]


def test_numstat_stops_at_a_rename_cut_before_its_paths() -> None:
    assert parse_numstat(["1\t1\ta.py", "2\t0\t", "gone.txt"]) == [
        ("a.py", None, 1, 1, False)
    ]


def test_name_status_reads_every_status_and_both_paths_of_a_rename() -> None:
    records = ["M", "a.py", "R087", "old.py", "new.py", "D", "gone.py", "A", "b.py"]
    records += ["C100", "base.py", "copy.py", "T", "link"]

    assert parse_name_status(records) == {
        "a.py": ("modified", None),
        "new.py": ("renamed", "old.py"),
        "gone.py": ("deleted", None),
        "b.py": ("added", None),
        "copy.py": ("added", "base.py"),
        "link": ("modified", None),
    }


def test_parse_merges_counts_with_statuses_and_lists_untracked_last() -> None:
    stdout = _output(
        _part(
            "meta",
            _meta(
                branch="lemma/abc12345",
                start="origin/main",
                base="b" * 40,
                head="h" * 40,
                ahead="2",
                dirty="1",
            ),
        ),
        _part(
            "numstat",
            _z(b"4\t2\ta.py", b"1\t0\t", b"old.py", b"new.py", b"0\t9\tgone.py"),
        ),
        _part(
            "status",
            _z(b"M", b"a.py", b"R091", b"old.py", b"new.py", b"D", b"gone.py"),
        ),
        _part("untracked", _z(b"notes.md", b"3\t0", b"blob.bin", b"-\t-")),
    )

    changes = parse_changes(stdout, patch_bytes=None)

    assert changes.state == "ready"
    assert changes.branch == "lemma/abc12345"
    assert changes.base_ref == "origin/main"
    assert changes.base_sha == "b" * 40
    assert changes.head_sha == "h" * 40
    assert changes.ahead == 2
    assert changes.uncommitted is True
    assert changes.files == (
        ChangedFile(path="a.py", status="modified", additions=4, deletions=2),
        ChangedFile(path="new.py", status="renamed", additions=1, old_path="old.py"),
        ChangedFile(path="gone.py", status="deleted", deletions=9),
        ChangedFile(path="notes.md", status="untracked", additions=3),
        ChangedFile(path="blob.bin", status="untracked", binary=True),
    )
    assert (changes.additions, changes.deletions) == (8, 11)
    assert changes.patch is None
    assert changes.truncated is False


def test_without_a_base_the_answer_names_none_and_counts_no_commits() -> None:
    """A start point never fetched, or a ref since deleted: the files are the
    uncommitted work, and nothing claims a base it did not find."""
    stdout = _output(
        _part("meta", _meta(branch="lemma/abc12345", head="h" * 40, ahead="5")),
        _part("numstat", b""),
        _part("status", b""),
        _part("untracked", b""),
    )

    changes = parse_changes(stdout, patch_bytes=None)

    assert (changes.base_ref, changes.base_sha, changes.ahead) == (None, None, 0)
    assert changes.files == ()
    assert changes.uncommitted is False


def test_a_cut_list_drops_its_partial_record_and_says_so() -> None:
    whole = _z(b"1\t1\tkept.py")
    filler = b"x" * (LIST_BYTES + 10)
    stdout = _output(
        _part("meta", _meta()),
        _part("numstat", whole + filler),
        _part("status", _z(b"M", b"kept.py")),
        _part("untracked", b""),
    )

    changes = parse_changes(stdout, patch_bytes=None)

    assert [changed.path for changed in changes.files] == ["kept.py"]
    assert changes.truncated is True


def test_more_untracked_than_listed_is_truncated() -> None:
    stdout = _output(_part("meta", _meta(more="1")), _part("untracked", b""))

    assert parse_changes(stdout, patch_bytes=None).truncated is True


def test_the_patch_is_cut_at_its_cap() -> None:
    stdout = _output(_part("meta", _meta()), _part("patch", b"a" * 2049))

    changes = parse_changes(stdout, patch_bytes=2048)

    assert changes.patch == "a" * 2048
    assert changes.truncated is True


def test_whatever_a_login_shell_prints_first_is_passed_over() -> None:
    stdout = "Welcome to the sandbox\n" + _output(_part("meta", _meta()))

    assert parse_changes(stdout, patch_bytes=None).state == "ready"


@pytest.mark.parametrize(
    "stdout",
    ["", "something else\n", _output("meta not-base64!"), _output()],
)
def test_output_that_is_not_the_commands_is_refused(stdout: str) -> None:
    with pytest.raises(ChangesOutputError):
        parse_changes(stdout, patch_bytes=None)


# -- where the checkout is ----------------------------------------------------


def test_the_checkout_is_the_conversations_worktree() -> None:
    cwd = f"{WORKSPACE_ROOT}/worktrees/acme/web/abc12345"

    assert checkout_dir(_REPO, cwd, run_workdir=None) == cwd


def test_a_conversation_from_before_worktrees_reads_the_clone() -> None:
    for cwd in (f"{WORKSPACE_ROOT}/repos/acme/web", f"{WORKSPACE_ROOT}/c/x/y"):
        assert (
            checkout_dir(_REPO, cwd, run_workdir=None)
            == f"{WORKSPACE_ROOT}/repos/acme/web"
        )


def test_a_shared_machine_reads_the_runs_own_checkout() -> None:
    run_dir = f"{WORKSPACE_ROOT}/runs/agent/conversation"

    assert (
        checkout_dir(_REPO, "ignored", run_workdir=run_dir)
        == f"{run_dir}/repos/acme/web"
    )


def test_the_command_names_nothing_that_writes() -> None:
    command = changes_command(directory="/w", ref="main", patch_bytes=1024)

    assert "GIT_OPTIONAL_LOCKS=0" in command
    assert "--no-optional-locks" in command
    assert "--no-ext-diff" in command and "--no-textconv" in command
    assert "core.fsmonitor=false" in command
    for writing in (
        " fetch",
        " pull",
        " add ",
        " commit",
        " reset",
        " checkout",
        " stash",
        "update-index",
        "hash-object -w",
    ):
        assert writing not in command


# -- against a real checkout --------------------------------------------------


@pytest.fixture
def git(tmp_path: Path) -> LocalGit:
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    return local_git(tmp_path)


def _seed_origin(git: LocalGit) -> None:
    """Files on origin's main for the conversation's branch to change."""
    scratch = git.root / "seed"
    git.run("clone", "-q", str(git.origin), str(scratch))
    (scratch / "keep.txt").write_text("a\nb\nc\n")
    (scratch / "gone.txt").write_text("x\n")
    (scratch / "moved.txt").write_text("".join(f"line {n}\n" for n in range(40)))
    git.run("-C", str(scratch), "add", ".")
    git.run("-C", str(scratch), "commit", "-q", "-m", "seed")
    git.run("-C", str(scratch), "push", "-q", "origin", "main")


def _read(
    git: LocalGit, directory: Path, *, ref: str | None = None, patch_bytes=None
) -> subprocess.CompletedProcess[str]:
    command = changes_command(
        directory=str(directory), ref=ref, patch_bytes=patch_bytes
    )
    return subprocess.run(
        ["bash", "-c", command], env=git.env, capture_output=True, text=True
    )


def _index_digest(git: LocalGit) -> str:
    """Every index the clone holds -- its own and each worktree's."""
    digest = hashlib.sha256()
    for index in sorted((git.clone / ".git").rglob("index")):
        digest.update(index.read_bytes())
    return digest.hexdigest()


def test_a_real_checkout_reads_committed_staged_and_untracked_work(
    git: LocalGit,
) -> None:
    _seed_origin(git)
    code, worktree = git.step("abc12345")
    assert code == 0
    base = git.run("-C", str(worktree), "rev-parse", "origin/main")
    # Committed on the branch, not pushed: an edit, a delete and a rename.
    (worktree / "keep.txt").write_text("a\nB\nc\nd\n")
    git.run("-C", str(worktree), "rm", "-q", "gone.txt")
    git.run("-C", str(worktree), "mv", "moved.txt", "renamed.txt")
    git.run("-C", str(worktree), "commit", "-qam", "work")
    # Then edited again and left uncommitted, plus files git does not track.
    with (worktree / "keep.txt").open("a") as handle:
        handle.write("uncommitted\n")
    (worktree / "untracked file.txt").write_text("new\nfile\n")
    (worktree / "blob.bin").write_bytes(b"\x00\x01\x02\x00")
    before = _index_digest(git)

    done = _read(git, worktree, patch_bytes=64 * 1024)

    assert done.returncode == 0, done.stderr
    changes = parse_changes(done.stdout, patch_bytes=64 * 1024)
    assert changes.branch == "lemma/abc12345"
    assert changes.base_ref == "origin/main"
    assert changes.base_sha == base
    assert changes.head_sha == git.run("-C", str(worktree), "rev-parse", "HEAD")
    assert changes.ahead == 1
    assert changes.uncommitted is True
    by_path = {changed.path: changed for changed in changes.files}
    assert by_path["keep.txt"] == ChangedFile(
        path="keep.txt", status="modified", additions=3, deletions=1
    )
    assert by_path["gone.txt"] == ChangedFile(
        path="gone.txt", status="deleted", deletions=1
    )
    assert by_path["renamed.txt"] == ChangedFile(
        path="renamed.txt", status="renamed", old_path="moved.txt"
    )
    assert by_path["untracked file.txt"] == ChangedFile(
        path="untracked file.txt", status="untracked", additions=2
    )
    assert by_path["blob.bin"] == ChangedFile(
        path="blob.bin", status="untracked", binary=True
    )
    assert changes.patch is not None
    assert "+uncommitted" in changes.patch
    assert "b/untracked file.txt" in changes.patch
    assert changes.truncated is False
    # Read only: no index was refreshed or written.
    assert _index_digest(git) == before
    assert git.run("-C", str(worktree), "status", "--porcelain") != ""


def test_a_checkout_with_nothing_changed_is_ready_and_empty(git: LocalGit) -> None:
    code, worktree = git.step("abc12345")
    assert code == 0

    done = _read(git, worktree)

    assert done.returncode == 0, done.stderr
    changes = parse_changes(done.stdout, patch_bytes=None)
    assert changes.files == ()
    assert changes.ahead == 0
    assert changes.uncommitted is False
    assert changes.base_ref == "origin/main"


def test_a_conversation_on_a_ref_compares_against_that_ref(git: LocalGit) -> None:
    code, worktree = git.step("abc12345", ref="release")
    assert code == 0

    done = _read(git, worktree, ref="release")

    changes = parse_changes(done.stdout, patch_bytes=None)
    assert changes.base_ref == "origin/release"


def test_a_start_point_that_is_gone_shows_uncommitted_work_only(
    git: LocalGit,
) -> None:
    code, worktree = git.step("abc12345")
    assert code == 0
    (worktree / "draft.md").write_text("one\n")

    done = _read(git, worktree, ref="no-such-branch")

    changes = parse_changes(done.stdout, patch_bytes=None)
    assert changes.base_ref is None
    assert changes.base_sha is None
    assert [changed.path for changed in changes.files] == ["draft.md"]


def test_a_large_patch_is_cut_and_says_so(git: LocalGit) -> None:
    code, worktree = git.step("abc12345")
    assert code == 0
    (worktree / "big.txt").write_text("".join(f"row {n}\n" for n in range(2000)))

    done = _read(git, worktree, patch_bytes=1024)

    changes = parse_changes(done.stdout, patch_bytes=1024)
    assert changes.patch is not None and len(changes.patch.encode()) == 1024
    assert changes.truncated is True
    assert changes.files[0].additions == 2000


def test_a_directory_with_no_checkout_exits_as_not_checked_out(
    git: LocalGit,
) -> None:
    empty = git.root / "worktrees" / "acme" / "web" / "nothing"
    empty.mkdir(parents=True)

    assert _read(git, empty).returncode == NOT_CHECKED_OUT_EXIT
    assert _read(git, git.root / "missing").returncode == NOT_CHECKED_OUT_EXIT
