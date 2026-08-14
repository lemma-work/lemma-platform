#!/usr/bin/env python3
"""Fail the build when a pooled DB connection is held across non-database work.

An ``AsyncSession`` owns a Postgres connection for its whole lifetime. Every
`await` inside an open session that is *not* a query leaves that connection
checked out and idle: the pool shrinks for everyone else while nothing is being
asked of the database. At agent timescales this is not a rounding error -- one
LLM call inside a session block holds a connection for seconds, and a streaming
response can hold one for minutes.

Getting this right is what lets the pool be small and lets task concurrency be
sized from RAM instead of from ``db_pool_size``. That trade is only safe if it
stays true, which is what this script is for.

Four things are reported:

``non-db-await``
    An `await` on work that is definitionally not a query -- HTTP, object
    storage, Redis, an event publish, a job enqueue, a model call, a sandbox
    operation, a thread offload, a sleep -- lexically inside an open session.

``session-across-yield``
    A session opened in a generator that yields while holding it. FastAPI keeps
    a yield-dependency alive for the whole response body, so this is how a
    "streaming" endpoint quietly pins a connection for the length of the
    stream. This is the shape of the pod-import incident (see
    docs/design/pod-bundle-share-import.md).

``nested-session``
    A second session opened while one is already held on the same task. Costs
    two connections for one unit of work, and self-deadlocks a saturated pool:
    the outer session can't be returned until the inner one is granted.

``async-for-non-db``
    Iterating a non-database async source while holding a session -- the same
    bug as ``non-db-await``, spread over an iterator.

The checker is deliberately deny-list driven rather than allow-list driven. It
cannot resolve `self.foo.bar()` to a definition without type inference, so it
recognises the operations this codebase actually uses to leave the process. That
means it is not sound in the abstract -- but paired with the baseline ratchet it
is sound in the direction that matters: whatever it can see, it will not let get
worse.

Usage::

    uv run python scripts/check_session_scope.py
    uv run python scripts/check_session_scope.py --update-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "session-scope-baseline.json"

# Production code only. Test harnesses open sessions around blocking fixtures on
# purpose and never run on a serving event loop.
SCAN_ROOT = ROOT / "app"
EXCLUDED_PARTS = ("tests", "test_support")

# --- What counts as opening a session -----------------------------------------
#
# Matched against the dotted callee of an `async with` item. Covers the UoW
# factories, the raw session makers (primary and datastore), and direct engine
# checkouts.
SESSION_OPENERS = re.compile(
    r"""(?x)
    (^|\.)(
          async_session_maker
        | _?session_maker
        | get_session_maker
        | get_datastore_session_maker
        | _?session_factory
        | uow_factory
        | create_uow_from_session_maker
    )$
    |
    (^|\.)(engine|_engine)\.(begin|connect)$
    """
)

# --- What counts as non-database work -----------------------------------------
#
# Matched against the dotted callee of an `await`. Each entry is something that
# either leaves this process or leaves this event loop; none of them can be a
# query. Ordered roughly by how long they hold the connection hostage.
NON_DB_AWAITS: tuple[tuple[str, str], ...] = (
    # Thread offloads and sleeps: the connection is held while the loop is
    # explicitly not making progress on this task.
    (r"(^|\.)run_blocking$", "thread offload"),
    (r"^asyncio\.to_thread$", "thread offload"),
    (r"(^|\.)to_thread\.run_sync$", "thread offload"),
    (r"(^|\.)run_in_executor$", "thread offload"),
    (r"^(asyncio|anyio|trio)\.sleep$", "sleep"),
    # Model calls. Seconds to minutes.
    (r"(^|\.)(run_stream|stream_response|request_stream)$", "model call"),
    (r"(^|\.)(embed|embed_batch|embed_documents|embed_query|rerank)$", "model call"),
    (r"(^|\.)(complete|completion|chat_completion)$", "model call"),
    # Sandboxes and workspaces: container start, command exec, file transfer.
    (r"(^|\.)(exec_command|run_command|start_sandbox|ensure_sandbox)$", "sandbox"),
    (r"(^|\.)(write_file|read_file|upload_to_sandbox|download_from_sandbox)$", "sandbox"),
    # Outbound HTTP.
    (r"(^|\.)(client|_client|http_client|_http_client)\.(get|post|put|patch|delete|head|request|send|stream)$", "outbound HTTP"),
    (r"(^|\.)httpx\.", "outbound HTTP"),
    (r"(^|\.)_request$", "outbound HTTP"),
    (r"(^|\.)(fetch_spec|fetch_url|download_file|download)$", "outbound HTTP"),
    # Object storage.
    (r"(^|\.)storage\.", "object storage"),
    (r"(^|\.)(upload_file|delete_file|get_file_bytes|put_object|get_object)$", "object storage"),
    # Redis, event publishing, job dispatch. Individually fast, but they are the
    # ones that turn a tidy unit of work into a distributed one.
    (r"(^|\.)(redis|_redis)\.", "redis"),
    (r"(^|\.)_get_redis$", "redis"),
    (r"(^|\.)publish(_[a-z_]+)?$", "event publish"),
    (r"(^|\.)enqueue(_[a-z_]+)?$", "job enqueue"),
    # Crypto/KMS round trips.
    (r"(^|\.)(wrap_key|unwrap_key|encrypt_key|decrypt_key)$", "kms"),
)

NON_DB_PATTERNS = tuple((re.compile(pattern), label) for pattern, label in NON_DB_AWAITS)

# Receivers that make a call a query no matter what it is named. A repository
# method called `enqueue_run` writes an admission row; `store.save_import`
# writes a row. Without this the deny-list's verb matching would fire on the
# data-access layer, which is the one place it must not.
DB_RECEIVERS = re.compile(r"(repository|repositories|uow|session|dao|outbox|admission)")

# `@asynccontextmanager` helpers yield the session by construction -- that IS
# the mechanism, and the scope that matters is the caller's `async with`, which
# this checker analyses on its own. FastAPI `Depends` generators are NOT
# exempted: the framework holds those open for the whole response body.
CONTEXTMANAGER_DECORATORS = {"asynccontextmanager", "contextmanager"}


class DependencyIndex:
    """Works out which FastAPI dependencies hold a session for the whole request.

    The lexical rules above only see `async with ...session_maker()`. The much
    larger exposure is `Depends`: `get_uow` yields a session, FastAPI keeps a
    yield-dependency alive until the response body is finished, and every
    service dependency built from `uow: UoWDep` inherits that. A handler with
    such a parameter holds a pooled connection for its entire duration without
    a single `async with` in sight.

    So: seed with the providers that yield a session, follow `Depends` edges
    through `Annotated` aliases and provider signatures to a fixed point, and
    treat any handler that lands on one as holding a session throughout.
    """

    def __init__(self) -> None:
        # `UoWDep = Annotated[SqlAlchemyUnitOfWork, Depends(get_uow)]`
        self.alias_to_providers: dict[str, set[str]] = {}
        # provider/handler name -> the dependency names its parameters name
        self.params: dict[str, set[str]] = {}
        # providers that yield while holding a session, e.g. `get_uow`
        self.seeds: set[str] = set()
        self.session_scoped: set[str] = set()
        # Interprocedural "this leaves the process" propagation. A controller
        # rarely calls httpx itself; it calls a service method that calls an
        # adapter that calls httpx. Matching only the leaf would miss every
        # real finding, so slowness is propagated up the call graph by name.
        self.awaits: dict[str, set[str]] = {}
        self.slow_reason: dict[str, str] = {}
        # How many definitions share each bare name. Without type inference,
        # `await thing.execute(...)` can only be resolved by name -- and
        # `execute` is defined on dozens of classes, most of them repositories.
        # Propagating through an ambiguous name poisons the whole graph (it
        # marked `conn.execute` as an HTTP call). So propagation is restricted
        # to names with exactly one definition, where resolution is certain.
        # That trades recall for precision on purpose: a gate that cries wolf
        # gets baselined into irrelevance.
        self.definition_counts: dict[str, int] = {}

    def ingest(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                self._ingest_alias(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.params[node.name] = _dependency_names(node)
                if _yields_inside_session(node):
                    self.seeds.add(node.name)
                self.definition_counts[node.name] = (
                    self.definition_counts.get(node.name, 0) + 1
                )
                awaited, direct = _awaited_calls(node)
                self.awaits.setdefault(node.name, set()).update(awaited)
                if direct is not None and node.name not in self.slow_reason:
                    self.slow_reason[node.name] = direct

    def _unambiguous(self, name: str) -> bool:
        return self.definition_counts.get(name, 0) == 1

    def resolve_slow(self) -> None:
        """Fixed point: awaiting something unambiguously slow makes you slow."""
        changed = True
        while changed:
            changed = False
            # Sorted so the recorded reason is the same on every run: the
            # baseline keys include it, and a reason that flips between two
            # equally-true callees would churn the file for no reason.
            for name, callees in sorted(self.awaits.items()):
                if name in self.slow_reason or not self._unambiguous(name):
                    continue
                for callee in sorted(callees):
                    if (
                        callee != name
                        and callee in self.slow_reason
                        and self._unambiguous(callee)
                    ):
                        self.slow_reason[name] = f"via {callee}"
                        changed = True
                        break

    def why_slow(self, callee: str) -> str | None:
        """Reason `callee` is non-database work, if it is."""
        direct = _classify_non_db(callee)
        if direct is not None:
            return direct
        bare = callee.rsplit(".", 1)
        receiver = bare[0] if len(bare) > 1 else ""
        if receiver and DB_RECEIVERS.search(receiver.lower()):
            return None
        name = bare[-1]
        if not self._unambiguous(name):
            return None
        return self.slow_reason.get(name)

    def _ingest_alias(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        providers = _depends_callees(node.value)
        if targets and providers:
            for target in targets:
                self.alias_to_providers.setdefault(target, set()).update(providers)

    def resolve(self) -> None:
        self.session_scoped = set(self.seeds)
        changed = True
        while changed:
            changed = False
            for name, dependencies in self.params.items():
                if name in self.session_scoped:
                    continue
                if any(self._reaches_session(dep) for dep in dependencies):
                    self.session_scoped.add(name)
                    changed = True

    def _reaches_session(self, name: str) -> bool:
        if name in self.session_scoped:
            return True
        return any(
            provider in self.session_scoped
            for provider in self.alias_to_providers.get(name, ())
        )

    def holds_session(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return any(self._reaches_session(dep) for dep in _dependency_names(node))


def _depends_callees(node: ast.AST) -> set[str]:
    """Names passed to `Depends(...)` anywhere inside an expression."""
    found: set[str] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and _dotted(child.func).split(".")[-1] == "Depends"
            and child.args
        ):
            found.add(_dotted(child.args[0]))
    return found


def _dependency_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Dependency names a signature refers to, via annotation alias or Depends()."""
    names: set[str] = set()
    arguments = node.args
    for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        if arg.annotation is None:
            continue
        names.update(_depends_callees(arg.annotation))
        for child in ast.walk(arg.annotation):
            if isinstance(child, ast.Name):
                names.add(child.id)
    for default in (*arguments.defaults, *arguments.kw_defaults):
        if default is not None:
            names.update(_depends_callees(default))
    return names


def _awaited_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], str | None]:
    """Bare names this function awaits, and why it is directly non-DB (if it is).

    Nested function definitions are skipped: their awaits belong to them.
    """
    awaited: set[str] = set()
    direct: str | None = None
    stack: list[ast.AST] = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(current, ast.Await) and isinstance(current.value, ast.Call):
            callee = _dotted(current.value.func)
            awaited.add(callee.rsplit(".", 1)[-1])
            if direct is None:
                direct = _classify_non_db(callee)
        stack.extend(ast.iter_child_nodes(current))
    return awaited, direct


def _yields_inside_session(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for a `get_uow`-shaped provider: opens a session, yields inside it."""
    if _is_context_manager(node):
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.AsyncWith) and _opens_session(child):
            if any(
                isinstance(inner, (ast.Yield, ast.YieldFrom))
                for stmt in child.body
                for inner in ast.walk(stmt)
            ):
                return True
    return False


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    scope: str
    rule: str
    detail: str

    def key(self) -> str:
        """Identity for the baseline: no line number, so edits above don't churn."""
        return f"{self.path}::{self.scope}::{self.rule}::{self.detail}"

    def render(self) -> str:
        return f"{self.path}:{self.line}  {self.rule}  in {self.scope}()  [{self.detail}]"


def _dotted(node: ast.AST) -> str:
    """Best-effort dotted source name for a callee, e.g. `self.storage.upload_file`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        # `get_session_maker()(...)` -- the interesting name is the inner call.
        return _dotted(node.func)
    if isinstance(node, ast.Await):
        return _dotted(node.value)
    return ""


def _opens_session(node: ast.AsyncWith) -> bool:
    return any(
        SESSION_OPENERS.search(_dotted(item.context_expr)) for item in node.items
    )


def _classify_non_db(callee: str) -> str | None:
    receiver = callee.rsplit(".", 1)[0] if "." in callee else ""
    if receiver and DB_RECEIVERS.search(receiver.lower()):
        return None
    for pattern, label in NON_DB_PATTERNS:
        if pattern.search(callee):
            return label
    return None


def _is_context_manager(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _dotted(decorator).split(".")[-1] in CONTEXTMANAGER_DECORATORS
        for decorator in node.decorator_list
    )


class SessionScopeChecker(ast.NodeVisitor):
    def __init__(self, path: str, index: DependencyIndex) -> None:
        self.path = path
        self.index = index
        self.violations: list[Violation] = []
        self._scope: list[str] = []
        self._session_depth = 0
        self._in_context_manager = False
        self._request_scoped = False

    # A nested function body does not run inside the enclosing `async with`, so
    # depth resets across a def boundary -- unless the function itself takes a
    # request-scoped session dependency, in which case its whole body runs with
    # a connection checked out.
    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        request_scoped = self.index.holds_session(node)
        outer_depth = self._session_depth
        outer_request = self._request_scoped
        self._session_depth = 1 if request_scoped else 0
        self._request_scoped = request_scoped
        outer_cm = self._in_context_manager
        self._in_context_manager = _is_context_manager(node)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()
        self._in_context_manager = outer_cm
        self._request_scoped = outer_request
        self._session_depth = outer_depth

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        if not _opens_session(node):
            self.generic_visit(node)
            return
        if self._session_depth:
            self._record(node.lineno, "nested-session", _dotted(node.items[0].context_expr))
        self._session_depth += 1
        for child in node.body:
            self.visit(child)
        self._session_depth -= 1
        # The context managers themselves are evaluated outside the block.
        for item in node.items:
            if item.optional_vars is not None:
                self.visit(item.optional_vars)

    def visit_Await(self, node: ast.Await) -> None:
        if self._session_depth and isinstance(node.value, ast.Call):
            callee = _dotted(node.value.func)
            label = self.index.why_slow(callee)
            if label is not None:
                self._record(node.lineno, "non-db-await", f"{label}: {callee}")
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        if self._session_depth:
            callee = _dotted(
                node.iter.func if isinstance(node.iter, ast.Call) else node.iter
            )
            label = self.index.why_slow(callee)
            if label is not None:
                self._record(node.lineno, "async-for-non-db", f"{label}: {callee}")
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self._check_yield(node)
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self._check_yield(node)
        self.generic_visit(node)

    def _check_yield(self, node: ast.Yield | ast.YieldFrom) -> None:
        if self._session_depth and not self._in_context_manager:
            self._record(node.lineno, "session-across-yield", self._scope_name())

    def _scope_name(self) -> str:
        return ".".join(self._scope) or "<module>"

    def _record(self, line: int, rule: str, detail: str) -> None:
        # Distinguish "you opened a session and then did this" from "FastAPI
        # opened one for the whole request and then you did this" -- same cost,
        # very different fix.
        if self._request_scoped and self._session_depth == 1:
            rule = f"{rule}/request-scoped"
        self.violations.append(
            Violation(self.path, line, self._scope_name(), rule, detail)
        )


def collect(paths: list[Path]) -> list[Violation]:
    parsed = [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in paths
    ]
    # `Depends` graphs cross files, so the index is built over the whole tree
    # before anything is judged.
    index = DependencyIndex()
    for _, tree in parsed:
        index.ingest(tree)
    index.resolve()
    index.resolve_slow()

    found: list[Violation] = []
    for path, tree in parsed:
        checker = SessionScopeChecker(str(path.relative_to(ROOT)), index)
        checker.visit(tree)
        found.extend(checker.violations)
    return sorted(found, key=lambda v: (v.path, v.line, v.rule))


def source_files() -> list[Path]:
    return sorted(
        path
        for path in SCAN_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine; "
        "growing it needs a reason in the PR description.",
    )
    args = parser.parse_args()

    violations = collect(source_files())

    if args.update_baseline:
        payload = {
            "_comment": (
                "Pre-existing places where a DB connection is held across non-database "
                "work. This file may shrink freely; growing it means a new connection "
                "is being held across an LLM call, an HTTP request or a thread offload. "
                "See scripts/check_session_scope.py."
            ),
            "violations": sorted({v.key() for v in violations}),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {len(payload['violations'])} entries")
        return 0

    baseline = set(json.loads(args.baseline.read_text(encoding="utf-8"))["violations"])
    new = [v for v in violations if v.key() not in baseline]
    fixed = baseline - {v.key() for v in violations}

    if fixed:
        print(f"✓ {len(fixed)} baselined violation(s) gone — run --update-baseline")
    if not new:
        print(f"✓ session scope: no new violations ({len(baseline)} baselined)")
        return 0

    print(f"✗ session scope: {len(new)} new violation(s)\n")
    for violation in new:
        print(f"  {violation.render()}")
    print(
        "\nA session holds a pooled Postgres connection until it closes. Close it "
        "before the non-database work and open a new one to persist the result."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
