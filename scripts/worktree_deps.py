#!/usr/bin/env python3
"""Install a worktree's dependencies without paying for them again.

Every worktree used to run its own `npm ci`, and a full one is about 900 MB of
`node_modules` copied out of the npm cache -- per worktree, so a dozen agents
at once filled a laptop. Most of those installs are identical: the worktree's
`package-lock.json` is the same as one some other worktree already installed.

So for each npm project this looks through every worktree of the clone for an
installed `node_modules` that matches this worktree's lockfile, and copies it
with a copy-on-write clone (`cp -c` on APFS, `--reflink` on Linux). A clone
shares disk blocks with its source until either side changes a file: copying
`lemma-typescript/node_modules` measured 5 MB on disk against 196 MB for
`npm ci`. Only when no worktree matches does it run `npm ci`, and that
worktree then becomes the source for the next one.

"Matches" is checked on content, not timestamps. npm records what it actually
installed in `node_modules/.package-lock.json`; an install matches when every
package there has the version, resolved URL and integrity the lockfile names,
and every package the lockfile requires is there. The main checkout's install
is often stale -- nobody re-runs `npm ci` there after a pull -- so its copy is
not trusted just for being the main checkout.

Python projects are left to `uv sync`, which already clones from the uv cache
(363 MB by `du`, 35 MB on disk for `lemma-cli/.venv`). Copying a virtualenv
between worktrees would be wrong besides: its scripts and its editable install
carry the absolute path of the worktree that made it.

`make worktree-deps` runs this; `ONLY=backend,frontend` limits it to what the
task needs. Standard library only, so `uv run --no-project` runs it.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

NPM_PROJECTS = ("lemma-typescript", "lemma-frontend")
# Component name -> (directory, extra `uv sync` arguments), as `make init` does.
UV_PROJECTS: dict[str, tuple] = {
    "backend": ("lemma-backend", ["--extra", "local"]),
    "cli": ("lemma-cli", []),
    "python": ("lemma-python", []),
}
# The frontend imports the SDK through a `file:` link, so it needs both.
COMPONENTS: dict[str, list[str]] = {
    "backend": ["backend"],
    "cli": ["cli"],
    "python": ["python"],
    "typescript": ["lemma-typescript"],
    "frontend": ["lemma-typescript", "lemma-frontend"],
}
LOCK_FIELDS = ("version", "resolved", "integrity")


# ── Matching an install to a lockfile ─────────────────────────────────────────


def _packages(path: Path) -> dict | None:
    try:
        packages = json.loads(path.read_text()).get("packages")
    except (OSError, ValueError):
        return None
    return packages if isinstance(packages, dict) else None


def _installed_key(key: str) -> bool:
    return key.startswith("node_modules/")


def install_matches(lockfile: Path, node_modules: Path) -> bool:
    """Whether `node_modules` holds exactly what `lockfile` asks for."""
    wanted = _packages(lockfile)
    installed = _packages(node_modules / ".package-lock.json")
    if wanted is None or installed is None:
        return False
    # Only what npm installed: a `file:` dependency's own entry ("../lemma-
    # typescript") describes linked source, and npm rewrites its version from
    # that package.json, so the lockfile's copy of it can lag without harm.
    wanted = {key: value for key, value in wanted.items() if _installed_key(key)}
    installed = {key: value for key, value in installed.items() if _installed_key(key)}
    for key, entry in installed.items():
        if key not in wanted:
            return False
        if any(entry.get(name) != wanted[key].get(name) for name in LOCK_FIELDS):
            return False
    # Optional packages for another platform are in the lockfile, not installed.
    return all(
        key in installed or entry.get("optional") or entry.get("peer")
        for key, entry in wanted.items()
    )


def worktree_roots(root: Path) -> list[Path]:
    """Every worktree of this clone, the main checkout first."""
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [
        Path(line.split(" ", 1)[1])
        for line in listing.splitlines()
        if line.startswith("worktree ")
    ]


def find_donor(root: Path, project: str, worktrees: list[Path]) -> Path | None:
    lockfile = root / project / "package-lock.json"
    for worktree in worktrees:
        if worktree.resolve() == root.resolve():
            continue
        candidate = worktree / project / "node_modules"
        if candidate.is_dir() and install_matches(lockfile, candidate):
            return candidate
    return None


# ── Doing it ──────────────────────────────────────────────────────────────────


def clone_command(source: Path, target: Path) -> list[str]:
    if platform.system() == "Darwin":
        return ["cp", "-c", "-R", str(source), str(target)]
    return ["cp", "-a", "--reflink=auto", str(source), str(target)]


def run(command: list[str], cwd: Path, dry_run: bool) -> bool:
    print(f"  $ {' '.join(command)}  (in {cwd.name})", flush=True)
    if dry_run:
        return True
    return subprocess.run(command, cwd=cwd, check=False).returncode == 0


def npm_project(
    root: Path, project: str, worktrees: list[Path], dry_run: bool, force: bool
) -> bool:
    directory = root / project
    node_modules = directory / "node_modules"
    lockfile = directory / "package-lock.json"
    print(f"→ {project}")
    if not force and node_modules.is_dir() and install_matches(lockfile, node_modules):
        print("  already installed and matches package-lock.json")
        ok = True
    else:
        donor = None if force else find_donor(root, project, worktrees)
        if donor is not None:
            print(f"  cloning the matching install in {donor.parent.parent}")
            ok = True
            if node_modules.exists():
                ok = run(["rm", "-rf", "node_modules"], directory, dry_run)
            ok = ok and run(clone_command(donor, node_modules), directory, dry_run)
        else:
            print("  no worktree has a matching install; installing")
            ok = run(["npm", "ci", "--no-audit", "--no-fund"], directory, dry_run)
    # The frontend imports the SDK's build output, not its source.
    if ok and project == "lemma-typescript" and not (directory / "dist").is_dir():
        ok = run(["npm", "run", "build", "--silent"], directory, dry_run)
    return ok


def uv_project(root: Path, name: str, dry_run: bool) -> bool:
    directory, extra = UV_PROJECTS[name]
    print(f"→ {directory}")
    return run(["uv", "sync", "--quiet", *extra], root / directory, dry_run)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--only",
        default="",
        help=f"comma-separated subset of: {', '.join(COMPONENTS)} (default: all)",
    )
    parser.add_argument(
        "--force", action="store_true", help="run `npm ci` even if a clone would do"
    )
    parser.add_argument("--dry-run", action="store_true", help="print, change nothing")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    names = [name.strip() for name in args.only.split(",") if name.strip()] or list(
        COMPONENTS
    )
    unknown = [name for name in names if name not in COMPONENTS]
    if unknown:
        print(
            f"unknown component(s): {', '.join(unknown)}; choose from {', '.join(COMPONENTS)}",
            file=sys.stderr,
        )
        return 2
    wanted = {part for name in names for part in COMPONENTS[name]}
    root = args.root.resolve()
    worktrees = worktree_roots(root)

    ok = True
    for project in NPM_PROJECTS:
        if project in wanted:
            ok = npm_project(root, project, worktrees, args.dry_run, args.force) and ok
    for name in UV_PROJECTS:
        if name in wanted:
            ok = uv_project(root, name, args.dry_run) and ok
    print("✓ dependencies ready" if ok else "✗ some installs failed; see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
