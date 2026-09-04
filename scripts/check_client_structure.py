#!/usr/bin/env python3
"""Measure size, complexity and untyped escapes in the shipped client packages.

`make quality` runs twenty-odd gates and fifteen of them begin `cd
lemma-backend`. The two packages users actually install -- `lemma-terminal` and
`lemma-sdk` -- got three: `ruff format --check`, a bare `ruff check`, and a
lockfile freshness sweep. Nothing measured how big a file had become, how
branchy a function had become, or how much of the public surface had opted out
of the type system, so all three grew without a number attached. They grew a
long way: the CLI carries nine files over the backend's 600-line ceiling and a
command function with a complexity of 103, against a backend worst case of 56.

This is the backend's `lemma-backend/scripts/check_architecture.py` ratchet
pointed at the client tree, and deliberately only the part of it that transfers:

* **oversized files** -- same 600-line ceiling;
* **complex functions** -- same complexity of 15;
* **untyped escapes** -- same rule, `Any` and bare containers in annotations.

What is left behind, so that its absence is a decision rather than an omission:
`forbidden_imports`, `composition_deep_imports`, `core_module_imports` and
`module_cycles` all encode the backend's modular-monolith layering -- a module
may only be reached through its published contracts, and `app/core` may not
depend on a module. The clients have no such architecture to protect. A CLI is
a command tree over an SDK, and `lemma_cli.cli_core.commands.pods` importing
`lemma_cli.cli_core.io` is the design, not a breach of it. Applying the rule
here would have produced a large baseline of violations that nobody intends to
ever fix, which is the fastest way to teach people that a gate means nothing.

Written as a sibling rather than by parameterising the backend's checker: the
thresholds and the counting rules are shared, but the *inputs* are not. The
backend's checker walks one package under one root and buckets by
`app/modules/<name>`; this one walks two packages under two project roots, with
their own vendored and generated trees to exclude, and buckets by client
package. Those differences are most of the file. The two constants and the two
AST visitors below are duplicated from that checker on purpose and must stay in
step with it -- a client tree measured against a *different* definition of
"too complex" would report numbers that cannot be compared to the backend's,
which is the one comparison that makes these numbers mean anything.

This gate is advisory today. The baseline it records is the honest size of the
debt, not a target anybody has agreed to; `--advisory` prints growth and exits
0 so that recording it cannot block work that has nothing to do with it. Drop
that flag from the Makefile to arm the ratchet.

Usage::

    cd lemma-cli && uv run python ../scripts/check_client_structure.py
    cd lemma-cli && uv run python ../scripts/check_client_structure.py --snapshot
    cd lemma-cli && uv run python ../scripts/check_client_structure.py --update-baseline

Run it through `uv` from a client project, never a bare `python3`: these
packages are Python 3.14 and macOS's system interpreter reports valid PEP 758
syntax as a SyntaxError.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "client-structure-baseline.json"

# Duplicated from lemma-backend/scripts/check_architecture.py. Same numbers on
# purpose: a 700-line file is no more readable for being in the CLI.
MAX_FILE_LINES = 600
MAX_COMPLEXITY = 15
# `ast.Match` is 3.10+, and this is the half check_script_portability.py cannot
# see: it parses every script under an old grammar but says plainly that it
# "makes no claim about behaviour", so an attribute reference passes the gate
# and crashes at runtime.
_MATCH_NODES: tuple[type[ast.AST], ...] = (ast.Match,) if hasattr(ast, "Match") else ()

#: The packages that are published and installed, keyed by the prefix their
#: metrics are bucketed under. Only the shipped package of each project: tests,
#: codegen scripts and `setup.py` are not what a user runs.
TREES = (
    ("cli", Path("lemma-cli") / "lemma_cli"),
    ("sdk", Path("lemma-python") / "lemma_sdk"),
)

#: Trees that are in the working copy but are not this repository's source.
#:
#: `lemma_cli/skills/` and `lemma-cli/lemma_pod_bundle/` are gitignored copies
#: that `setup.py` mirrors in at build time -- the canonical sources are
#: `lemma-skills/` and `lemma-pod-bundle/`, and measuring the copy would count
#: the same code twice and make the numbers depend on whether anyone had run a
#: build. `lemma_sdk/openapi_client/` is 795 files generated from the OpenAPI
#: spec; it is already excluded from ruff and the formatter for the same reason,
#: and a metric on generated code is a metric on the generator's template.
EXCLUDED_DIRS = frozenset({"__pycache__", "openapi_client", "skills", "tests", "build"})


def _python_files(package: Path) -> list[Path]:
    return sorted(
        path
        for path in package.rglob("*.py")
        if not EXCLUDED_DIRS.intersection(path.relative_to(package).parts)
    )


def _bucket(prefix: str, package: Path, path: Path) -> str:
    """Name the bucket a file's metrics are counted under.

    The containing package, at most two levels deep: `cli_core/io.py` is
    `cli:cli_core` and `cli_core/commands/pods.py` is `cli:cli_core/commands`.
    Two levels rather than the backend's one because the CLI's weight is not
    spread evenly through `cli_core` -- it is concentrated in the eighteen
    files under `cli_core/commands`, and a single `cli:cli_core` bucket would
    average that away into the twenty-four small helpers beside it.

    Keyed by package rather than by path depth, so a file moving between
    directories inside its own package does not churn the baseline.
    """
    parts = path.relative_to(package).parts[:-1]
    if not parts:
        return prefix
    return f"{prefix}:{'/'.join(parts[:2])}"


# Bare containers say "a collection of something" and stop there, which is the
# same abdication as `Any` wearing a different word.
_UNPARAMETERISED = frozenset({"dict", "list", "tuple", "set", "frozenset"})


class _UntypedEscapes(ast.NodeVisitor):
    """Count annotations that opt out of the type system.

    This matters more here than it does in the backend. `lemma-python` ships a
    `py.typed` marker, which tells every consumer's type checker to trust these
    annotations -- so an `Any` in the SDK is not a hole in our own checking,
    it is a hole in theirs, in a package they cannot edit.

    Only annotations. An `Any` in a comment, a string, or a `cast` the code
    immediately narrows is not the thing being discouraged.
    """

    def __init__(self) -> None:
        self.count = 0

    def _inspect(self, annotation: ast.expr | None) -> None:
        """Walk an annotation, counting only what actually gives up.

        `dict[str, int]` must not count: walking naively would see the `dict`
        inside the subscript and read a fully specified container as an escape,
        inflating the baseline with the very thing the rule asks for. So a
        subscript's own name is skipped and only its parameters are examined --
        `dict[str, Any]` counts once, for the `Any`.
        """
        if annotation is None:
            return
        if isinstance(annotation, ast.Subscript):
            self._inspect(annotation.slice)
            return
        if isinstance(annotation, ast.Tuple):
            for element in annotation.elts:
                self._inspect(element)
            return
        if isinstance(annotation, ast.BinOp):  # `X | Y`
            self._inspect(annotation.left)
            self._inspect(annotation.right)
            return
        if isinstance(annotation, ast.Name):
            if annotation.id == "Any" or annotation.id in _UNPARAMETERISED:
                self.count += 1
            return
        if isinstance(annotation, ast.Attribute) and annotation.attr == "Any":
            self.count += 1
            return
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            # A stringised annotation. Parsing it keeps `"dict"` from hiding
            # behind quotes, and a fragment that will not parse is not one this
            # check should have an opinion about.
            try:
                self._inspect(ast.parse(annotation.value, mode="eval").body)
            except SyntaxError:
                return

    def visit_arg(self, node: ast.arg) -> None:
        self._inspect(node.annotation)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._inspect(node.annotation)
        self.generic_visit(node)

    def _visit_function(self, node: Any) -> None:
        self._inspect(node.returns)
        self.generic_visit(node)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


class _FunctionComplexity(ast.NodeVisitor):
    """Score every function the way the backend's ratchet scores one."""

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.complex: dict[str, int] = {}

    def _visit_function(self, node: Any) -> None:
        self.scope.append(node.name)
        key = f"{self.relative_path}:{'.'.join(self.scope)}"
        score = 1
        for child in ast.walk(node):
            if isinstance(
                child,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.IfExp,
                    ast.ExceptHandler,
                    ast.comprehension,
                )
                + _MATCH_NODES,
            ):
                score += 1
            elif isinstance(child, ast.BoolOp):
                score += max(1, len(child.values) - 1)
        if score > MAX_COMPLEXITY:
            self.complex[key] = score
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def snapshot() -> dict[str, Any]:
    oversized: dict[str, int] = {}
    complex_functions: dict[str, tuple[str, int]] = {}
    untyped_escapes: dict[str, tuple[str, int]] = {}

    for prefix, package in TREES:
        absolute = ROOT / package
        if not absolute.is_dir():
            raise SystemExit(f"client package missing: {package}")
        for path in _python_files(absolute):
            relative = path.relative_to(ROOT).as_posix()
            bucket = _bucket(prefix, absolute, path)
            text = path.read_text(encoding="utf-8")

            line_count = len(text.splitlines())
            if line_count > MAX_FILE_LINES:
                oversized[relative] = line_count

            tree = ast.parse(text, filename=str(path))

            complexity = _FunctionComplexity(relative)
            complexity.visit(tree)
            for key, score in complexity.complex.items():
                complex_functions[key] = (bucket, score)

            escapes = _UntypedEscapes()
            escapes.visit(tree)
            if escapes.count:
                untyped_escapes[relative] = (bucket, escapes.count)

    return {
        "oversized_files": dict(sorted(oversized.items())),
        "complex_functions": _aggregate(complex_functions),
        "untyped_escapes": _aggregate(untyped_escapes),
    }


def _aggregate(values: dict[str, tuple[str, int]]) -> dict[str, int]:
    """Keep the ratchet reviewable while retaining per-package growth signals.

    Same shape as the backend baseline -- `<bucket>:count/total/max` -- so the
    two files can be read side by side without translating between them.
    """
    grouped: dict[str, list[int]] = defaultdict(list)
    for bucket, value in values.values():
        grouped[bucket].append(value)
    result: dict[str, int] = {}
    for bucket, bucket_values in sorted(grouped.items()):
        result[f"{bucket}:count"] = len(bucket_values)
        result[f"{bucket}:total"] = sum(bucket_values)
        result[f"{bucket}:max"] = max(bucket_values)
    return result


def _growth(
    current: dict[str, int], baseline: dict[str, int]
) -> dict[str, tuple[int, int]]:
    return {
        key: (baseline.get(key, 0), value)
        for key, value in current.items()
        if value > baseline.get(key, 0)
    }


def check(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for label, key in (
        ("oversized file", "oversized_files"),
        ("complex function", "complex_functions"),
        ("untyped escape count", "untyped_escapes"),
    ):
        for name, (before, after) in _growth(
            current[key], baseline.get(key, {})
        ).items():
            failures.append(f"{label} grew: {name} ({before} -> {after})")
    return failures


def _summary(current: dict[str, Any]) -> str:
    oversized = current["oversized_files"]
    escapes = current["untyped_escapes"]
    complexity = current["complex_functions"]
    return (
        f"{len(oversized)} files over {MAX_FILE_LINES} lines "
        f"({sum(oversized.values())} lines), "
        f"{sum(v for k, v in complexity.items() if k.endswith(':count'))} functions "
        f"over complexity {MAX_COMPLEXITY} "
        f"(worst {max([v for k, v in complexity.items() if k.endswith(':max')] or [0])}), "
        f"{sum(v for k, v in escapes.items() if k.endswith(':total'))} untyped escapes"
    )


def _require_a_modern_interpreter() -> None:
    """Fail with the command to use, rather than a traceback from `ast.parse`.

    This walks source written for 3.14, so an older interpreter cannot read its
    input whatever the script itself is written in -- and the failure it
    produces otherwise is a `SyntaxError` pointing into somebody else's file,
    which reads as that file being broken. The repository has been bitten by
    exactly that misreading from the other direction; see CLAUDE.md.
    """
    if sys.version_info < (3, 10):
        raise SystemExit(
            "check_client_structure.py reads Python 3.14 sources and cannot run "
            f"on {sys.version_info.major}.{sys.version_info.minor}. Run it as "
            "`cd lemma-cli && uv run python ../scripts/check_client_structure.py`, "
            "or via `make measure-clients`."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Report growth and exit 0. How `make quality` runs this today.",
    )
    args = parser.parse_args()

    _require_a_modern_interpreter()
    current = snapshot()

    if args.snapshot:
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0

    if args.update_baseline:
        payload = dict(current)
        payload["_comment"] = (
            "Size, complexity and untyped escapes in lemma_cli and lemma_sdk, at "
            "the thresholds lemma-backend/architecture-baseline.json uses. This "
            "file may shrink freely. Growing it means the client packages got "
            "further from the backend's standard, not closer. See "
            "scripts/check_client_structure.py."
        )
        args.baseline.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"✓ baseline written: {_summary(current)}")
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    failures = check(current, baseline)

    if not failures:
        print(f"✓ client structure: no growth ({_summary(current)})")
        return 0

    marker = "!" if args.advisory else "✗"
    print(f"{marker} client structure: {len(failures)} metric(s) grew")
    for failure in failures:
        print(f"  - {failure}")
    if args.advisory:
        print(
            "  advisory: not failing the build. Re-record with "
            "`--update-baseline` if the growth is deliberate."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
