#!/usr/bin/env python3
"""The fast local loop: lint, or fix, what this branch changed.

`make lint` and `make fix` both land here. By default the scope is every file
that differs from the merge base with `origin/main`, committed or not, plus
untracked files; `--all` widens it to the whole repository, and `--staged`
narrows it to the index, which is what the pre-commit hook asks for.

Each language runs the tool CI runs, the way CI runs it, so a clean `make
lint` predicts the matching CI step rather than approximating it:

    python    ruff check + ruff format, per project, with that project's
              config and paths (the same ones `make quality` uses)
    frontend  eslint, and tsc for each package whose sources changed
    rust      cargo fmt, and clippy on the crates that changed
    shell     shellcheck
    ci        actionlint (with shellcheck over `run:` blocks) and the CI
              aggregator check
    docker    hadolint
    docs      typos, over every changed text file
    config    yamllint, and a parse of every changed TOML and JSON file

`make quality` stays the full pre-PR gate; this is the subset worth running on
every save. `--fast` drops the two slow steps, tsc and clippy, for the
pre-commit hook.

Files are passed to ruff with `--force-exclude`. Naming a file on ruff's
command line otherwise overrides the project's `exclude`, which is how a
generated file like the backend's event catalog gets reformatted and breaks
its freshness gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

# Pinned so a lint run means the same thing on every machine and in CI; each
# is fetched once by uv and cached.
RUFF = os.environ.get("RUFF", "uvx ruff@0.15.22")
SHELLCHECK = ["uvx", "--from", "shellcheck-py==0.11.0.1", "shellcheck"]
ACTIONLINT = [
    "uvx",
    "--from",
    "actionlint-py==1.7.12.24",
    "--with",
    "shellcheck-py==0.11.0.1",
    "actionlint",
]
HADOLINT = ["uvx", "--from", "hadolint-bin==2.15.1", "hadolint"]
TYPOS = ["uvx", "typos==1.50.3"]
YAMLLINT = ["uvx", "yamllint==1.38.0"]

GROUPS = ("python", "frontend", "rust", "shell", "ci", "docker", "docs", "config")

JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
# JSON dialects that allow comments, which a strict parse would reject.
JSONC = re.compile(
    r"(^|/)(tsconfig[^/]*|jsconfig[^/]*|\.vscode/[^/]*|devcontainer)\.json$"
)


# ── Scope ─────────────────────────────────────────────────────────────────────


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _names(output: str) -> list[str]:
    return [name for name in output.split("\0") if name]


def changed_files(base: str, staged: bool) -> list[str]:
    """Paths that exist and differ from the merge base (or the index)."""
    if staged:
        names = _names(git("diff", "--cached", "--name-only", "--diff-filter=d", "-z"))
    else:
        try:
            merge_base = git("merge-base", base, "HEAD").strip()
        except subprocess.CalledProcessError:
            print(f"  (no merge base with {base}; comparing against HEAD)")
            merge_base = "HEAD"
        # `git diff <commit>` compares against the working tree, so this is
        # committed, staged and unstaged work in one list.
        names = _names(git("diff", "--name-only", "--diff-filter=d", "-z", merge_base))
        names += _names(git("ls-files", "--others", "--exclude-standard", "-z"))
    return sorted({name for name in names if (REPO_ROOT / name).is_file()})


def tracked_files() -> list[str]:
    return [
        name for name in _names(git("ls-files", "-z")) if (REPO_ROOT / name).is_file()
    ]


@dataclass
class Scope:
    files: list[str]
    everything: bool

    def matching(self, predicate: Callable[[str], bool]) -> list[str]:
        return [path for path in self.files if predicate(path)]

    def touched(self, *config_files: str) -> bool:
        """A changed lint config re-checks everything it governs."""
        return self.everything or any(path in self.files for path in config_files)


# ── Running ───────────────────────────────────────────────────────────────────


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    ran: int = 0

    def run(self, label: str, command: Sequence[str], cwd: Path = REPO_ROOT) -> bool:
        self.ran += 1
        shown = shlex.join(command)
        if len(shown) > 160:
            shown = shown[:157] + "..."
        relative = cwd.relative_to(REPO_ROOT)
        where = f"(in {relative}) " if str(relative) != "." else ""
        print(f"→ {label}: {where}{shown}", flush=True)
        started = time.monotonic()
        try:
            status = subprocess.run(list(command), cwd=cwd, check=False).returncode
        except FileNotFoundError as error:
            print(f"  ✗ {error.filename} is not installed")
            status = 127
        if status != 0:
            self.failures.append(label)
            print(f"  ✗ {label} failed ({time.monotonic() - started:.1f}s)")
            return False
        return True

    def fail(self, label: str, why: str) -> None:
        self.failures.append(label)
        print(f"  ✗ {label}: {why}")


def _in(path: str, root: str) -> bool:
    return root in ("", ".") or path == root or path.startswith(root.rstrip("/") + "/")


def _relative(paths: Iterable[str], root: str) -> list[str]:
    if root in ("", "."):
        return list(paths)
    return [path[len(root.rstrip("/")) + 1 :] for path in paths]


# ── Python ────────────────────────────────────────────────────────────────────


def _make_variable(makefile: Path, name: str) -> list[str]:
    match = re.search(rf"^{name}\s*[:?]?=\s*(.+)$", makefile.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"{name} is not defined in {makefile}")
    return match.group(1).split()


@dataclass(frozen=True)
class RuffProject:
    root: str
    lint_paths: tuple[str, ...]
    # None: linted but not format-checked, because `make format-check` does
    # not cover it and a local gate stricter than CI's is a surprise, not a help.
    format_paths: tuple[str, ...] | None
    ruff: tuple[str, ...]
    check_args: tuple[str, ...] = ()
    format_args: tuple[str, ...] = ()
    all_excludes: tuple[str, ...] = ()


def ruff_projects() -> list[RuffProject]:
    ruff = tuple(shlex.split(RUFF))
    backend_makefile = REPO_ROOT / "lemma-backend" / "Makefile"
    return [
        # The backend runs its own locked ruff, exactly as its Makefile does.
        RuffProject(
            "lemma-backend",
            tuple(_make_variable(backend_makefile, "LINT_PATHS")),
            tuple(_make_variable(backend_makefile, "FORMAT_PATHS")),
            ("uv", "run", "--quiet", "ruff"),
        ),
        RuffProject("lemma-cli", (".",), (".",), ruff),
        RuffProject(
            "lemma-python",
            (".",),
            (".",),
            ruff,
            format_args=("--exclude", "lemma_sdk/openapi_client"),
        ),
        RuffProject("lemma-stack", (".",), (".",), ruff),
        RuffProject("lemma-pod-bundle", (".",), (".",), ruff),
        RuffProject("tests/scenarios", (".",), (".",), ruff),
        # Everything else: the repository's own scripts and their tests, the
        # desktop tooling. No project config governs them, so ruff's defaults
        # apply; E402 is off because a script that imports a sibling has to
        # put its directory on sys.path first.
        RuffProject(
            ".",
            ("scripts", "tests", "desktop"),
            None,
            ruff,
            check_args=("--isolated", "--ignore", "E402"),
            all_excludes=("tests/scenarios",),
        ),
    ]


def _owner(path: str, projects: Sequence[RuffProject]) -> RuffProject | None:
    candidates = [project for project in projects if _in(path, project.root)]
    if not candidates:
        return None
    # Longest root wins, so tests/scenarios owns its files and not the tooling.
    return max(candidates, key=lambda project: len(project.root))


def lint_python(scope: Scope, report: Report, fix: bool) -> None:
    projects = ruff_projects()
    by_project: dict[RuffProject, list[str]] = {}
    if not scope.everything:
        for path in scope.matching(lambda p: p.endswith((".py", ".pyi"))):
            owner = _owner(path, projects)
            if owner is not None:
                by_project.setdefault(owner, []).append(path)
    for project in projects:
        name = "tooling" if project.root == "." else project.root
        cwd = REPO_ROOT / project.root
        if scope.everything:
            lint_targets = list(project.lint_paths)
            format_targets = list(project.format_paths or ())
            excludes = [
                arg
                for exclude in project.all_excludes
                for arg in ("--exclude", exclude)
            ]
            force: list[str] = []
        else:
            files = by_project.get(project, [])
            if not files:
                continue
            relative = _relative(files, project.root)
            lint_targets = [
                p for p in relative if any(_in(p, root) for root in project.lint_paths)
            ]
            format_targets = [
                p
                for p in relative
                if any(_in(p, root) for root in project.format_paths or ())
            ]
            excludes = []
            force = ["--force-exclude"]
        ruff = list(project.ruff)
        if lint_targets:
            report.run(
                f"python · {name} · ruff check",
                ruff
                + [
                    "check",
                    *force,
                    *(["--fix"] if fix else []),
                    *project.check_args,
                    *excludes,
                    *lint_targets,
                ],
                cwd,
            )
        if format_targets:
            report.run(
                f"python · {name} · ruff format",
                ruff
                + [
                    "format",
                    *force,
                    *([] if fix else ["--check"]),
                    *project.format_args,
                    *format_targets,
                ],
                cwd,
            )


# ── Frontend ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NodePackage:
    root: str
    eslint_paths: tuple[str, ...] | None  # None: no eslint in this package
    typecheck: tuple[str, ...]


NODE_PACKAGES = (
    NodePackage(
        "lemma-frontend",
        ("src", "server", "scripts", "tests", "server.mjs"),
        ("npx", "--no-install", "tsc", "--noEmit"),
    ),
    NodePackage("lemma-harness", (".",), ("npm", "run", "--silent", "typecheck")),
    NodePackage(
        "lemma-typescript",
        None,
        ("npx", "--no-install", "tsc", "--noEmit", "-p", "tsconfig.test.json"),
    ),
)


def lint_frontend(scope: Scope, report: Report, fix: bool, fast: bool) -> None:
    sources = scope.matching(lambda p: p.endswith(JS_SUFFIXES))
    sdk_built = (REPO_ROOT / "lemma-typescript" / "dist" / "index.js").is_file()
    for package in NODE_PACKAGES:
        mine = [path for path in sources if _in(path, package.root)]
        if not scope.everything and not mine:
            continue
        cwd = REPO_ROOT / package.root
        label = f"frontend · {package.root}"
        if not (cwd / "node_modules").is_dir():
            report.fail(
                label, f"node_modules is missing; run 'npm ci' in {package.root}"
            )
            continue
        if package.eslint_paths is not None:
            if scope.everything:
                targets = list(package.eslint_paths)
            else:
                targets = [
                    p
                    for p in _relative(mine, package.root)
                    if any(_in(p, root) for root in package.eslint_paths)
                ]
            if targets:
                report.run(
                    f"{label} · eslint",
                    [
                        "npx",
                        "--no-install",
                        "eslint",
                        "--no-warn-ignored",
                        *(["--fix"] if fix else []),
                        *targets,
                    ],
                    cwd,
                )
        if fix or fast:
            continue
        # The apps resolve `lemma-sdk` to the SDK's build output, so their
        # types cannot be checked until it exists.
        if package.root != "lemma-typescript" and not sdk_built:
            report.run(
                "frontend · lemma-typescript · build (the apps type-check against it)",
                ["npm", "run", "--silent", "build"],
                REPO_ROOT / "lemma-typescript",
            )
            sdk_built = True
        report.run(f"{label} · tsc", list(package.typecheck), cwd)


# ── Rust ──────────────────────────────────────────────────────────────────────


def desktop_crates() -> dict[str, str]:
    """Workspace member directory (repo-relative) -> package name."""
    desktop = REPO_ROOT / "desktop"
    manifest = tomllib.loads((desktop / "Cargo.toml").read_text())
    crates = {"desktop": manifest["package"]["name"]}
    for member in manifest["workspace"]["members"]:
        member_manifest = tomllib.loads((desktop / member / "Cargo.toml").read_text())
        crates[f"desktop/{member}"] = member_manifest["package"]["name"]
    return crates


def lint_rust(scope: Scope, report: Report, fix: bool, fast: bool) -> None:
    sources = scope.matching(
        lambda p: (
            p.startswith("desktop/") and p.endswith((".rs", "Cargo.toml", "Cargo.lock"))
        )
    )
    if not scope.everything and not sources:
        return
    desktop = REPO_ROOT / "desktop"
    report.run(
        "rust · cargo fmt",
        ["cargo", "fmt", "--all", *([] if fix else ["--check"])],
        desktop,
    )
    if fix or fast:
        return
    crates = desktop_crates()
    # The root manifest holds the profiles and the shared dependency versions,
    # and the lockfile is the workspace's: either one changes every crate.
    if scope.everything or {"desktop/Cargo.toml", "desktop/Cargo.lock"} & set(sources):
        selection = ["--workspace"]
        packages = set(crates.values())
    else:
        packages = set()
        for path in sources:
            owner = max((root for root in crates if _in(path, root)), key=len)
            packages.add(crates[owner])
        selection = [arg for package in sorted(packages) for arg in ("-p", package)]
    if crates["desktop"] in packages:
        # tauri-build resolves the sidecars at build-script time, so the app
        # crate does not compile, even for clippy, until they exist.
        report.run(
            "rust · sidecars",
            ["make", "--no-print-directory", "_desktop-ensure-sidecars"],
        )
    report.run(
        "rust · clippy",
        [
            "cargo",
            "clippy",
            *selection,
            "--locked",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
        desktop,
    )


# ── Everything else ───────────────────────────────────────────────────────────


def _is_shell(path: str) -> bool:
    return path.endswith(".sh") or path.startswith(".githooks/")


def lint_shell(scope: Scope, report: Report) -> None:
    files = scope.matching(_is_shell)
    if files:
        report.run(f"shell · shellcheck ({len(files)} files)", SHELLCHECK + files)


def lint_ci(scope: Scope, report: Report) -> None:
    def is_workflow(path: str) -> bool:
        return path.startswith(".github/workflows/") and path.endswith(
            (".yml", ".yaml")
        )

    if scope.touched(".github/actionlint.yaml"):
        workflows: list[str] = []  # actionlint with no arguments checks them all
    else:
        workflows = scope.matching(is_workflow)
        if not workflows:
            return
    report.run(
        f"ci · actionlint ({len(workflows) or 'all'} workflows)", ACTIONLINT + workflows
    )
    report.run(
        "ci · every job is aggregated and bounded",
        [
            "uv",
            "run",
            "--quiet",
            "--no-project",
            "--with",
            "pyyaml",
            "python",
            "scripts/check_ci_aggregators.py",
        ],
    )


def lint_docker(scope: Scope, report: Report) -> None:
    def is_dockerfile(path: str) -> bool:
        return Path(path).name.startswith("Dockerfile")

    if scope.touched(".hadolint.yaml"):
        files = [path for path in tracked_files() if is_dockerfile(path)]
    else:
        files = scope.matching(is_dockerfile)
    if files:
        report.run(f"docker · hadolint ({len(files)} files)", HADOLINT + files)


def lint_docs(scope: Scope, report: Report) -> None:
    if scope.touched(".typos.toml"):
        report.run("docs · typos (whole repository)", TYPOS + ["--format", "brief"])
        return
    if scope.files:
        report.run(
            f"docs · typos ({len(scope.files)} files)",
            TYPOS + ["--format", "brief", "--force-exclude", *scope.files],
        )


def lint_config(scope: Scope, report: Report) -> None:
    if scope.touched(".yamllint.yaml"):
        yaml_files = [p for p in tracked_files() if p.endswith((".yml", ".yaml"))]
    else:
        yaml_files = scope.matching(lambda p: p.endswith((".yml", ".yaml")))
    if yaml_files:
        report.run(
            f"config · yamllint ({len(yaml_files)} files)",
            YAMLLINT + ["--strict", "-f", "parsable", *yaml_files],
        )

    structured = scope.matching(
        lambda p: p.endswith((".toml", ".json")) and not JSONC.search(p)
    )
    if not structured:
        return
    report.ran += 1
    print(f"→ config · parse ({len(structured)} TOML/JSON files)")
    broken = []
    for path in structured:
        text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        try:
            if path.endswith(".toml"):
                tomllib.loads(text)
            else:
                json.loads(text)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
            broken.append(f"{path}: {error}")
    for problem in broken:
        print(f"  {problem}")
    if broken:
        report.failures.append("config · parse")


# ── Entry point ───────────────────────────────────────────────────────────────


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "groups",
        nargs="*",
        metavar="GROUP",
        help=f"any of: {', '.join(GROUPS)} (default: all)",
    )
    parser.add_argument(
        "--fix", action="store_true", help="run the fixers instead of the checks"
    )
    parser.add_argument(
        "--all", action="store_true", help="the whole repository, not just what changed"
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="only what is staged (the pre-commit hook)",
    )
    parser.add_argument("--fast", action="store_true", help="skip tsc and clippy")
    parser.add_argument("--base", default=os.environ.get("BASE", "origin/main"))
    args = parser.parse_args(argv)
    unknown = sorted(set(args.groups) - set(GROUPS))
    if unknown:
        parser.error(
            f"unknown group(s) {', '.join(unknown)}; choose from {', '.join(GROUPS)}"
        )
    return args


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    groups = args.groups or list(GROUPS)
    if args.fix:
        # Only the groups with a fixer that is safe to run unattended.
        groups = [group for group in groups if group in ("python", "frontend", "rust")]

    if args.all:
        scope = Scope(tracked_files(), everything=True)
        print(f"Scope: the whole repository ({len(scope.files)} files)")
    else:
        scope = Scope(changed_files(args.base, args.staged), everything=False)
        what = "staged" if args.staged else f"changed against {args.base}"
        print(f"Scope: {len(scope.files)} file(s) {what}")
        if not scope.files:
            print("Nothing to do.")
            return 0

    report = Report()
    started = time.monotonic()
    for group in groups:
        if group == "python":
            lint_python(scope, report, args.fix)
        elif group == "frontend":
            lint_frontend(scope, report, args.fix, args.fast)
        elif group == "rust":
            lint_rust(scope, report, args.fix, args.fast)
        elif group == "shell":
            lint_shell(scope, report)
        elif group == "ci":
            lint_ci(scope, report)
        elif group == "docker":
            lint_docker(scope, report)
        elif group == "docs":
            lint_docs(scope, report)
        elif group == "config":
            lint_config(scope, report)

    elapsed = time.monotonic() - started
    verb = "fix" if args.fix else "lint"
    if report.failures:
        print(
            f"\n✗ {verb}: {len(report.failures)} of {report.ran} step(s) failed in {elapsed:.0f}s:"
        )
        for label in report.failures:
            print(f"    {label}")
        return 1
    if report.ran == 0:
        print(f"✓ {verb}: none of the changed files has a {verb}er here.")
    else:
        print(f"✓ {verb}: {report.ran} step(s) passed in {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
