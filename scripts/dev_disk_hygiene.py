#!/usr/bin/env python3
"""Find, and with --apply remove, what local development leaves behind.

A checkout here grows three kinds of weight: build output (a Rust dev target
directory, node_modules, a backend virtualenv), git worktrees whose branch has
already landed, and local branches nobody will check out again. With many
worktrees -- several made by agents -- that adds up faster than anyone clears
it. This walks the main checkout and every worktree `git worktree list`
reports, prints what it would free and why, and changes nothing unless given
`--apply`. `make dev-clean` is the dry run, `make dev-clean-apply` the real one.

Everything it removes is rebuilt by the usual commands (`cargo build`,
`npm ci`, `uv sync`) or lives on a remote. Things that cannot be recovered --
uncommitted changes, commits that exist on no remote -- are the reasons it
keeps something, and it says so.

Idle time
    A worktree's idle time is how long since the newest of: its HEAD and HEAD
    reflog (any checkout, commit, rebase or reset), each file `git status`
    reports as changed or untracked (an editor or an agent at work), and the
    Rust target directory's top-level folders (a build). Each is one `stat`,
    so this is cheap on a worktree of any size, and each is something that
    changes when a person or an agent is actually using the worktree -- which
    a directory's own mtime, or a whole-tree walk for the newest file, is not.

Merged
    A branch counts as merged into origin/main when its tip is an ancestor of
    it, when GitHub reports a merged pull request whose head contains the tip
    (`gh`, if installed and authenticated), or when the branch's whole diff
    from its merge base matches the patch of one commit on main -- which is
    what a squash merge leaves behind.

Standard library only, so `uv run --no-project` runs it without an install.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator

DAY = 86400.0
PROTECTED_BRANCHES = {"main", "master"}
# Build output that `npm ci` / `uv sync` recreate, found this deep at most.
DEPENDENCY_DIRS = ("node_modules", ".venv")
DEPENDENCY_SEARCH_DEPTH = 3
SKIP_DESCENT = {".git", "node_modules", ".venv", "target", ".next", "dist", "build"}
LOCAL_IMAGE_REPOSITORIES = ("lemma-workspace", "lemma-function")
KEPT_IMAGE_TAG = "dev"
# `<crate>-<hash><rest>`: a 16-hex unit hash in deps, a 13-character base-36
# session hash in incremental.
_HASHED = re.compile(
    r"^(?:lib)?(?P<name>.+)-(?P<hash>[0-9a-f]{16}|[0-9a-z]{13})(?P<rest>(?:\..*)?)$"
)


# ── Shell ─────────────────────────────────────────────────────────────────────


def run(command: list[str], cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, result.stdout, result.stderr
        )
    return result.stdout


def git(repo: Path, *args: str, check: bool = True) -> str:
    # --no-optional-locks: `git status` otherwise refreshes the index on
    # disk, and a dry run must not write anything.
    return run(["git", "--no-optional-locks", "-C", str(repo), *args], check=check)


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def disk_usage(path: Path) -> int:
    """Bytes on disk under a path; `du` is far faster than walking in Python."""
    if not path.exists():
        return 0
    output = run(["du", "-sk", str(path)], check=False)
    try:
        return int(output.split()[0]) * 1024
    except (IndexError, ValueError):
        return 0


def mtime(path: Path) -> float:
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def ago(seconds: float) -> str:
    days = seconds / DAY
    return f"{days:.0f}d" if days >= 1 else f"{seconds / 3600:.0f}h"


# ── Worktrees ─────────────────────────────────────────────────────────────────


@dataclass
class Worktree:
    path: Path
    head: str = ""
    branch: str | None = None
    locked: str | None = None
    prunable: str | None = None
    is_main: bool = False


def list_worktrees(repo: Path) -> list[Worktree]:
    worktrees: list[Worktree] = []
    current: Worktree | None = None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = Worktree(Path(value), is_main=not worktrees)
            worktrees.append(current)
        elif current is None:
            continue
        elif key == "HEAD":
            current.head = value
        elif key == "branch":
            current.branch = value.removeprefix("refs/heads/")
        elif key == "locked":
            current.locked = value or "locked"
        elif key == "prunable":
            current.prunable = value or "prunable"
    return worktrees


def last_activity(worktree: Path) -> float:
    """The newest sign of use; see "Idle time" in the module docstring."""
    try:
        gitdir = Path(git(worktree, "rev-parse", "--absolute-git-dir").strip())
    except subprocess.CalledProcessError:
        return 0.0
    stamps = [mtime(gitdir / "HEAD"), mtime(gitdir / "logs" / "HEAD")]
    status = git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=normal",
        check=False,
    )
    entries = status.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        stamps.append(mtime(worktree / entry[3:]))
        if entry[0] in "RC":
            index += 1  # the rename's source path follows; it no longer exists
    target = worktree / "desktop" / "target"
    for sub in (
        "",
        "debug",
        "debug/deps",
        "debug/incremental",
        "release",
        "release/deps",
    ):
        stamps.append(mtime(target / sub))
    return max(stamps)


def is_dirty(worktree: Path) -> bool:
    return bool(git(worktree, "status", "--porcelain", check=False).strip())


def running_process_dirs() -> set[Path] | None:
    """Working directories of every process we can see, or None if unknown."""
    lsof = shutil.which("lsof")
    if lsof:
        output = run([lsof, "-w", "-a", "-d", "cwd", "-Fn"], check=False)
        return {Path(line[1:]) for line in output.splitlines() if line.startswith("n/")}
    proc = Path("/proc")
    if proc.is_dir():
        dirs = set()
        for entry in proc.iterdir():
            if entry.name.isdigit():
                try:
                    dirs.add(Path(os.readlink(entry / "cwd")))
                except OSError:
                    continue
        return dirs
    return None


def has_process_in(path: Path, process_dirs: set[Path] | None) -> bool:
    if process_dirs is None:
        return False
    resolved = path.resolve()
    return any(d == resolved or resolved in d.parents for d in process_dirs)


# ── Merged, and pushed ────────────────────────────────────────────────────────


class MergeOracle:
    """Answers "has this landed on main" three ways, cheapest first."""

    def __init__(self, repo: Path, base: str, use_gh: bool) -> None:
        self.repo = repo
        self.base = base
        self._main_patch_ids: set[str] | None = None
        self._pull_requests: dict[str, list[str]] | None = None if use_gh else {}

    def _is_ancestor(self, commit: str, of: str) -> bool:
        return (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "merge-base",
                    "--is-ancestor",
                    commit,
                    of,
                ],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )

    def merged_pull_request_heads(self) -> dict[str, list[str]]:
        if self._pull_requests is None:
            self._pull_requests = {}
            if shutil.which("gh"):
                output = run(
                    [
                        "gh",
                        "pr",
                        "list",
                        "--state",
                        "merged",
                        "--limit",
                        "1000",
                        "--json",
                        "headRefName,headRefOid",
                    ],
                    cwd=self.repo,
                    check=False,
                )
                try:
                    for pr in json.loads(output or "[]"):
                        self._pull_requests.setdefault(pr["headRefName"], []).append(
                            pr["headRefOid"]
                        )
                except (json.JSONDecodeError, KeyError, TypeError):
                    self._pull_requests = {}
        return self._pull_requests

    def _landed_by_pull_request(self, branch: str | None, tip: str) -> bool:
        if not branch:
            return False
        for head in self.merged_pull_request_heads().get(branch, []):
            if head == tip or self._is_ancestor(tip, head):
                return True
        return False

    def _patch_id(self, *diff_args: str) -> str:
        diff = subprocess.run(
            ["git", "-C", str(self.repo), *diff_args], capture_output=True, check=False
        ).stdout
        if not diff.strip():
            return ""
        output = subprocess.run(
            ["git", "-C", str(self.repo), "patch-id", "--stable"],
            input=diff,
            capture_output=True,
            check=False,
        ).stdout.decode()
        return output.split()[0] if output.strip() else ""

    def _main_patches(self) -> set[str]:
        """The patch id of every commit on main, computed once.

        One pass over the whole history costs seconds; one pass per branch,
        from each branch's own merge base, cost minutes with a few hundred
        branches.
        """
        if self._main_patch_ids is None:
            log = subprocess.Popen(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "log",
                    "-p",
                    "--no-merges",
                    "--format=commit %H",
                    self.base,
                ],
                stdout=subprocess.PIPE,
            )
            output = subprocess.run(
                ["git", "-C", str(self.repo), "patch-id", "--stable"],
                stdin=log.stdout,
                capture_output=True,
                check=False,
            ).stdout.decode()
            log.wait()
            self._main_patch_ids = {
                line.split()[0] for line in output.splitlines() if line.strip()
            }
        return self._main_patch_ids

    def how_merged(self, branch: str | None, tip: str) -> str | None:
        """ "ancestor", "pull request", "squash", or None when not merged."""
        if self._is_ancestor(tip, self.base):
            return "ancestor"
        if self._landed_by_pull_request(branch, tip):
            return "pull request"
        merge_base = run(
            ["git", "-C", str(self.repo), "merge-base", tip, self.base], check=False
        ).strip()
        if not merge_base:
            return None
        branch_patch = self._patch_id("diff", merge_base, tip)
        if branch_patch and branch_patch in self._main_patches():
            return "squash"
        return None

    def unpushed(self, tip: str, how: str | None) -> int:
        """Commits reachable from the tip that no remote has.

        A branch whose pull request was merged has none by definition, even
        once GitHub deletes its remote branch: the PR head was on the remote.
        A squash found only by patch comparison is not proof of that, so its
        commits still count.
        """
        if how in ("ancestor", "pull request"):
            return 0
        output = run(
            [
                "git",
                "-C",
                str(self.repo),
                "rev-list",
                "--count",
                tip,
                "--not",
                "--remotes",
            ],
            check=False,
        )
        return int(output.strip() or 0)


# ── Rust target sweep ─────────────────────────────────────────────────────────


def cargo_is_building(target: Path) -> bool:
    """Cargo holds an exclusive lock on this file for the length of a build."""
    for profile in ("debug", "release"):
        lock = target / profile / ".cargo-lock"
        if not lock.exists():
            continue
        try:
            with open(lock, "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            return True
    return False


def _size_on_disk(path: Path) -> int:
    if path.is_dir() and not path.is_symlink():
        return disk_usage(path)
    try:
        return path.lstat().st_blocks * 512
    except OSError:
        return 0


def stale_artifacts(target: Path, older_than: float) -> list[Path]:
    """Superseded compilations in `deps` and `incremental`, cargo-sweep style.

    Every time a crate is rebuilt with different features, flags or sources,
    cargo writes it under a new hash and keeps the old one. For each crate
    this keeps every file of its newest hash and returns the older hashes'
    files, but only those untouched for longer than the cutoff: a crate with
    one hash is never returned, so this cannot remove the only copy of
    anything.
    """
    stale: list[Path] = []
    profiles = (
        sorted(p for p in target.iterdir() if p.is_dir()) if target.is_dir() else []
    )
    for profile in profiles:
        for folder in ("deps", "incremental"):
            directory = profile / folder
            if not directory.is_dir():
                continue
            groups: dict[tuple[str, str], dict[str, list[Path]]] = {}
            for entry in directory.iterdir():
                match = _HASHED.match(entry.name)
                if not match:
                    continue
                rest = match.group("rest")
                # One unit's codegen objects each carry their own suffix;
                # they belong to the unit, not to a group of their own.
                kind = ".rcgu.o" if rest.endswith(".rcgu.o") else rest
                key = (match.group("name"), kind)
                groups.setdefault(key, {}).setdefault(match.group("hash"), []).append(
                    entry
                )
            for by_hash in groups.values():
                if len(by_hash) < 2:
                    continue
                newest = max(by_hash, key=lambda h: max(mtime(p) for p in by_hash[h]))
                for hash_, entries in by_hash.items():
                    if hash_ != newest:
                        stale.extend(p for p in entries if mtime(p) < older_than)
    return stale


def dependency_dirs(root: Path) -> Iterator[Path]:
    def walk(directory: Path, depth: int) -> Iterator[Path]:
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name in DEPENDENCY_DIRS:
                yield child
            elif (
                depth < DEPENDENCY_SEARCH_DEPTH
                and child.name not in SKIP_DESCENT
                and not child.name.startswith(".")
            ):
                yield from walk(child, depth + 1)

    for name in DEPENDENCY_DIRS:
        if (root / name).is_dir():
            yield root / name
    yield from (d for d in walk(root, 1) if d.parent != root)


# ── Docker ────────────────────────────────────────────────────────────────────

_DOCKER_SIZE = re.compile(r"^([\d.]+)\s*([kKMGT]?B)$")
_DOCKER_UNITS = {
    "B": 1,
    "kB": 1000,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
}


def docker_bytes(text: str) -> int:
    match = _DOCKER_SIZE.match(text.strip())
    if not match:
        return 0
    return int(float(match.group(1)) * _DOCKER_UNITS.get(match.group(2), 1))


def docker_created(text: str) -> float | None:
    """`docker image ls`'s CreatedAt, e.g. "2026-09-12 10:31:07 +0200 CEST"."""
    try:
        return datetime.strptime(
            " ".join(text.split()[:3]), "%Y-%m-%d %H:%M:%S %z"
        ).timestamp()
    except ValueError:
        return None


# ── Plan ──────────────────────────────────────────────────────────────────────


@dataclass
class Step:
    item: str
    size: int
    action: str
    execute: Callable[[], None]


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(
        self, item: str, size: int, action: str, execute: Callable[[], None]
    ) -> None:
        self.steps.append(Step(item, size, action, execute))

    def keep(self, item: str, reason: str) -> None:
        self.kept.append((item, reason))


def remove_tree(path: Path) -> Callable[[], None]:
    return lambda: shutil.rmtree(path, ignore_errors=False)


def remove_paths(paths: Iterable[Path]) -> Callable[[], None]:
    paths = list(paths)

    def execute() -> None:
        for path in paths:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    return execute


def command(*args: str, cwd: Path | None = None) -> Callable[[], None]:
    return lambda: print(run(list(args), cwd=cwd), end="")


def label(path: Path, home: Path) -> str:
    """A short name: worktrees by their path, the main checkout by its name."""
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    if relative.parts[:2] == (".claude", "worktrees"):
        return str(relative)
    return str(Path(home.name) / relative)


# ── Rules ─────────────────────────────────────────────────────────────────────


def plan_worktrees(
    args: argparse.Namespace,
    repo: Path,
    current: Path,
    worktrees: list[Worktree],
    oracle: MergeOracle | None,
    activity: dict[Path, float],
    process_dirs: set[Path] | None,
    plan: Plan,
) -> set[Path]:
    """Rule 3: worktrees whose work has landed. Returns the ones to remove."""
    removing: set[Path] = set()
    for worktree in worktrees:
        if worktree.is_main:
            continue
        name = label(worktree.path, repo)
        if worktree.prunable or not worktree.path.is_dir():
            continue  # `git worktree prune` handles a missing directory
        if worktree.path == current:
            plan.keep(name, "the current worktree")
            continue
        if worktree.locked:
            plan.keep(name, f"locked ({worktree.locked})")
            continue
        idle = args.now - activity.get(worktree.path, 0.0)
        if idle < args.worktree_idle_days * DAY:
            continue  # in use; not worth a line
        if oracle is None:
            continue
        how = oracle.how_merged(worktree.branch, worktree.head)
        if how is None:
            continue
        if is_dirty(worktree.path):
            plan.keep(name, "merged, but has uncommitted changes")
            continue
        unpushed = oracle.unpushed(worktree.head, how)
        if unpushed:
            plan.keep(
                name, f"merged ({how}), but {unpushed} commit(s) are on no remote"
            )
            continue
        if has_process_in(worktree.path, process_dirs):
            plan.keep(name, "merged, but a process is running in it")
            continue
        removing.add(worktree.path)
        plan.add(
            name,
            disk_usage(worktree.path),
            f"remove worktree ({worktree.branch or 'detached'}, merged by {how}, idle {ago(idle)})",
            # --force because a merged, clean worktree still holds ignored
            # build output, which `worktree remove` otherwise refuses over.
            command(
                "git",
                "-C",
                str(repo),
                "worktree",
                "remove",
                "--force",
                str(worktree.path),
            ),
        )
    prunable = git(
        repo, "worktree", "prune", "--dry-run", "--verbose", check=False
    ).strip()
    if prunable or removing:
        plan.add(
            "git worktree metadata",
            0,
            "prune",
            command("git", "-C", str(repo), "worktree", "prune"),
        )
    return removing


def plan_build_output(
    args: argparse.Namespace,
    repo: Path,
    current: Path,
    worktrees: list[Worktree],
    removing: set[Path],
    activity: dict[Path, float],
    process_dirs: set[Path] | None,
    plan: Plan,
) -> None:
    """Rules 1 and 2: build output in idle worktrees, stale Rust artifacts in any."""
    for worktree in worktrees:
        if worktree.path in removing or not worktree.path.is_dir():
            continue
        idle = args.now - activity.get(worktree.path, 0.0)
        in_use = worktree.path == current or has_process_in(worktree.path, process_dirs)
        target = worktree.path / "desktop" / "target"
        if target.is_dir():
            if not in_use and idle > args.rust_idle_days * DAY:
                plan.add(
                    label(target, repo),
                    disk_usage(target),
                    f"remove (idle {ago(idle)})",
                    remove_tree(target),
                )
            elif cargo_is_building(target):
                plan.keep(label(target, repo), "cargo is building in it")
            else:
                stale = stale_artifacts(target, args.now - args.artifact_age_days * DAY)
                if stale:
                    plan.add(
                        label(target, repo) + " (superseded deps/incremental)",
                        sum(_size_on_disk(p) for p in stale),
                        f"sweep {len(stale)} entries older than {args.artifact_age_days:g}d",
                        remove_paths(stale),
                    )
        if in_use or idle <= args.deps_idle_days * DAY:
            continue
        for directory in dependency_dirs(worktree.path):
            plan.add(
                label(directory, repo),
                disk_usage(directory),
                f"remove (idle {ago(idle)})",
                remove_tree(directory),
            )


def plan_branches(
    args: argparse.Namespace,
    repo: Path,
    worktrees: list[Worktree],
    removing: set[Path],
    oracle: MergeOracle | None,
    plan: Plan,
) -> None:
    """Rule 4: old local branches that landed or lost their remote."""
    current_branch = git(repo, "branch", "--show-current", check=False).strip()
    checked_out = {w.branch for w in worktrees if w.branch and w.path not in removing}
    base_branch = args.base.split("/", 1)[-1]
    listing = git(
        repo,
        "for-each-ref",
        "refs/heads",
        "--format=%(refname:short)%09%(objectname)%09%(committerdate:unix)%09%(upstream:track)",
    )
    for line in listing.splitlines():
        name, tip, stamp, track = (line.split("\t") + ["", "", "", ""])[:4]
        if (
            name in PROTECTED_BRANCHES
            or name == base_branch
            or name == current_branch
            or name in checked_out
        ):
            continue
        age = args.now - float(stamp or 0)
        if age < args.branch_age_days * DAY:
            continue
        gone = track.strip() == "[gone]"
        how = oracle.how_merged(name, tip) if oracle else None
        if not how and not gone:
            continue
        unpushed = (
            oracle.unpushed(tip, how)
            if oracle
            else int(
                run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "rev-list",
                        "--count",
                        tip,
                        "--not",
                        "--remotes",
                    ],
                    check=False,
                ).strip()
                or 0
            )
        )
        if unpushed:
            plan.keep(
                f"branch {name}",
                f"{'merged' if how else 'remote gone'}, but {unpushed} commit(s) are on no remote",
            )
            continue
        why = f"merged by {how}" if how else "remote branch gone"
        plan.add(
            f"branch {name}",
            0,
            f"delete ({why}, last commit {ago(age)} ago)",
            command("git", "-C", str(repo), "branch", "-D", name),
        )


def plan_git(args: argparse.Namespace, repo: Path, plan: Plan) -> None:
    """Rule 4's remote half, and rule 5."""
    stale = [
        line.split("] ", 1)[-1].strip()
        for line in git(
            repo, "remote", "prune", "--dry-run", "origin", check=False
        ).splitlines()
        if "[would prune]" in line
    ]
    if stale:
        plan.add(
            f"{len(stale)} remote-tracking ref(s) gone from origin",
            0,
            "prune",
            command("git", "-C", str(repo), "remote", "prune", "origin"),
        )
    common = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    )
    if args.git:

        def gc() -> None:
            run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "reflog",
                    "expire",
                    "--expire=30.days",
                    "--all",
                ]
            )
            run(["git", "-C", str(repo), "gc", "--prune=30.days.ago", "--quiet"])

        plan.add(
            label(common, repo),
            0,
            "reflog expire --expire=30.days, gc --prune=30.days.ago",
            gc,
        )
        plan.notes.append(
            f"{label(common, repo)} is {human(disk_usage(common))} before gc."
        )
    else:
        plan.notes.append(
            f"{label(common, repo)} is {human(disk_usage(common))}; GIT=1 with the apply target also runs git gc."
        )


def plan_caches(args: argparse.Namespace, plan: Plan) -> None:
    """Rule 6: the uv cache, optionally npm's, and local docker images."""
    if shutil.which("uv"):
        cache = Path(run(["uv", "cache", "dir"], check=False).strip() or "/nonexistent")
        plan.notes.append(f"uv cache is {human(disk_usage(cache))}.")
        plan.add(
            "uv cache",
            0,
            "uv cache prune (unused entries)",
            command("uv", "cache", "prune"),
        )
    if args.npm and shutil.which("npm"):
        plan.add(
            "npm cache",
            0,
            "npm cache verify (garbage-collects)",
            command("npm", "cache", "verify"),
        )
    if args.docker:
        plan_docker(args, plan)


def plan_docker(args: argparse.Namespace, plan: Plan) -> None:
    docker = shutil.which("docker")
    if not docker:
        return
    try:
        subprocess.run([docker, "info"], capture_output=True, check=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        plan.notes.append("docker is not reachable; skipped its images.")
        return
    in_use = set(
        run([docker, "ps", "--no-trunc", "--format", "{{.Image}}"], check=False).split()
    )
    running = run([docker, "ps", "-q"], check=False).split()
    if running:
        in_use |= set(
            run(
                [docker, "inspect", "--format", "{{.Image}}", *running], check=False
            ).split()
        )
    images = run(
        [
            docker,
            "image",
            "ls",
            "--no-trunc",
            "--format",
            "{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}",
        ],
        check=False,
    )
    cutoff = args.now - args.image_age_days * DAY
    for line in images.splitlines():
        repository, tag, image_id, created, size = (line.split("\t") + [""] * 5)[:5]
        reference = f"{repository}:{tag}"
        if image_id in in_use or reference in in_use:
            continue
        if repository == "<none>" and tag == "<none>":
            plan.add(
                f"docker dangling image {image_id[7:19]}",
                docker_bytes(size),
                "docker rmi",
                command(docker, "rmi", image_id),
            )
            continue
        if (
            repository.rsplit("/", 1)[-1] in LOCAL_IMAGE_REPOSITORIES
            and tag != KEPT_IMAGE_TAG
        ):
            created_at = docker_created(created)
            if created_at is not None and created_at < cutoff:
                plan.add(
                    f"docker {reference}",
                    docker_bytes(size),
                    f"docker rmi (older than {args.image_age_days:g}d)",
                    command(docker, "rmi", reference),
                )


# ── Entry point ───────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--apply", action="store_true", help="act; without it nothing changes"
    )
    parser.add_argument(
        "--git", action="store_true", help="also run git reflog expire and gc (slow)"
    )
    parser.add_argument(
        "--rust-idle-days",
        type=float,
        default=3,
        help="remove desktop/target idle this long (3)",
    )
    parser.add_argument(
        "--artifact-age-days",
        type=float,
        default=7,
        help="sweep superseded Rust artifacts older (7)",
    )
    parser.add_argument(
        "--deps-idle-days",
        type=float,
        default=14,
        help="remove node_modules/.venv idle this long (14)",
    )
    parser.add_argument(
        "--worktree-idle-days",
        type=float,
        default=2,
        help="remove merged worktrees idle this long (2)",
    )
    parser.add_argument(
        "--branch-age-days",
        type=float,
        default=30,
        help="delete branches whose last commit is older (30)",
    )
    parser.add_argument(
        "--image-age-days",
        type=float,
        default=7,
        help="remove local sandbox image tags older (7)",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="what merged means merged into (origin/main)",
    )
    parser.add_argument(
        "--no-fetch",
        dest="fetch",
        action="store_false",
        help="with --apply, do not fetch first",
    )
    parser.add_argument(
        "--no-gh",
        dest="gh",
        action="store_false",
        help="do not ask GitHub about merged PRs",
    )
    parser.add_argument(
        "--no-docker",
        dest="docker",
        action="store_false",
        help="leave docker images alone",
    )
    parser.add_argument(
        "--no-caches",
        dest="caches",
        action="store_false",
        help="leave the uv/npm caches alone",
    )
    parser.add_argument("--npm", action="store_true", help="also run npm cache verify")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="any directory inside the checkout",
    )
    # Tests move the clock instead of backdating every file they create.
    parser.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.now is None:
        args.now = time.time()
    return args


def print_plan(plan: Plan, apply: bool) -> None:
    rows = [
        (step.item, human(step.size) if step.size else "-", step.action)
        for step in plan.steps
    ]
    if rows:
        width = min(max(len(item) for item, _, _ in rows), 80)
        print(f"\n  {'ITEM':<{width}}  {'SIZE':>9}  ACTION")
        for item, size, action in rows:
            print(f"  {item[:width]:<{width}}  {size:>9}  {action}")
        total = sum(step.size for step in plan.steps)
        verb = "to free" if apply else "would be freed"
        print(f"  {'TOTAL':<{width}}  {human(total):>9}  {verb}")
    else:
        print("\n  Nothing to clean up.")
    if plan.kept:
        print("\n  Kept:")
        for item, reason in plan.kept:
            print(f"    {item}: {reason}")
    for note in plan.notes:
        print(f"\n  {note}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        current = Path(git(args.repo, "rev-parse", "--show-toplevel").strip()).resolve()
    except subprocess.CalledProcessError:
        print(f"{args.repo} is not inside a git checkout", file=sys.stderr)
        return 2

    if args.apply and args.fetch:
        print(f"Fetching {args.base.split('/', 1)[0]}…")
        git(current, "fetch", "--quiet", args.base.split("/", 1)[0], check=False)

    worktrees = list_worktrees(current)
    repo = worktrees[0].path.resolve() if worktrees else current
    for worktree in worktrees:
        worktree.path = (
            worktree.path.resolve() if worktree.path.exists() else worktree.path
        )

    has_base = bool(
        git(current, "rev-parse", "--verify", "--quiet", args.base, check=False).strip()
    )
    oracle = MergeOracle(current, args.base, args.gh) if has_base else None
    mode = "apply" if args.apply else "dry run"
    print(f"Disk hygiene ({mode}): {len(worktrees)} worktree(s) under {repo}")
    if not has_base:
        print(f"  {args.base} does not exist here, so nothing is judged merged.")
    elif not args.apply:
        fetched = mtime(
            Path(
                git(
                    current, "rev-parse", "--path-format=absolute", "--git-common-dir"
                ).strip()
            )
            / "FETCH_HEAD"
        )
        if fetched:
            print(
                f"  Using {args.base} as last fetched, {ago(args.now - fetched)} ago; the apply run fetches first."
            )

    process_dirs = running_process_dirs()
    if process_dirs is None:
        print(
            "  (cannot list running processes here; a worktree in use is judged by idle time alone)"
        )
    activity = {w.path: last_activity(w.path) for w in worktrees if w.path.is_dir()}

    plan = Plan()
    removing = plan_worktrees(
        args, repo, current, worktrees, oracle, activity, process_dirs, plan
    )
    plan_build_output(
        args, repo, current, worktrees, removing, activity, process_dirs, plan
    )
    plan_branches(args, current, worktrees, removing, oracle, plan)
    plan_git(args, current, plan)
    if args.caches:
        plan_caches(args, plan)

    print_plan(plan, args.apply)
    if not args.apply:
        print(
            "\n  Dry run: nothing was changed. `make dev-clean-apply` does the above."
        )
        return 0

    failures = 0
    for step in plan.steps:
        print(f"→ {step.item}: {step.action}", flush=True)
        try:
            step.execute()
        except (OSError, subprocess.CalledProcessError) as error:
            failures += 1
            detail = getattr(error, "stderr", "") or str(error)
            print(f"  ✗ {detail.strip()}")
    print(
        f"\n{'✗' if failures else '✓'} {len(plan.steps) - failures} of {len(plan.steps)} step(s) done."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
