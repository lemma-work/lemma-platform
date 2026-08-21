#!/usr/bin/env python3
"""Fail the build on I/O that escapes the bounds this process relies on.

Three rules, all from findings the existing gates were green on. Each is the
kind of thing that is invisible at the call site and only shows up as a stalled
worker or a pinned connection hours later.

``unlimited-offload``
    Blocking work handed to a thread without going through
    :func:`app.core.concurrency.offload.run_blocking`.

    ``offload.py`` exists to partition thread capacity by workload class --
    ``cpu_bound``, ``external_http``, ``crypto`` -- so a burst of one kind
    cannot starve another, and its docstring says ``asyncio.to_thread`` "is
    replaced by this so there is a single, coherent, bounded system". It was
    not: 37 of 70 offloads bypassed it, so the ``OFFLOAD_*_LIMIT`` settings
    governed under half the traffic they name. Worse, ``asyncio.to_thread``
    uses asyncio's *default executor* -- a different pool from anyio's, shared
    with every ``getaddrinfo`` the process does, and untouched by the headroom
    ``configure_thread_pool()`` raises at startup.

``untimed-aiohttp-session``
    ``aiohttp.ClientSession()`` built without an explicit ``timeout=``.

    aiohttp's default total timeout is **five minutes** (httpx's is five
    seconds, which is why this rule does not need to cover httpx). One
    unresponsive upstream parks the caller for that long, and where the caller
    holds a DB session it parks a pooled connection with it -- which is exactly
    how an unauthenticated OAuth callback could pin a connection for minutes.

``process-lifetime-construction``
    A client whose construction is expensive and whose lifetime is the process
    -- an object store, an HTTP client, a DB engine, a Redis pool -- built
    inside a plain ``def`` that does not memoize the result.

    This is the rule that would have caught the one that got away. A DI builder
    called ``GCSStore(...)`` in its constructor; the constructor resolves
    credentials against the GKE metadata server before it returns, so every
    request and every agent tool call paid 350-500ms of blocking network I/O on
    the event loop. The loop-stall sampler named that frame in more traces than
    any other.

    The other two rules could not see it, and neither could ``make lint-async``,
    because all of them inspect ``async def`` bodies and this call was in a
    *synchronous* one. Sync is what made it invisible: a blocking call in a sync
    function is unremarkable until you notice every caller is a coroutine.

    Cached factories are fine -- that is the fix, not the offence -- so a
    function decorated with ``lru_cache``/``cache`` is exempt, as are the few
    modules whose whole job is to own one of these singletons.

All three are ratcheted against a baseline: it may shrink freely, and anything
new fails the build. See ``scripts/check_session_scope.py`` for the sibling gate
on connection scope, and ``make lint-async`` for the ruff rules that cover
blocking calls made directly on the loop.

Usage::

    uv run python scripts/check_io_hygiene.py
    uv run python scripts/check_io_hygiene.py --update-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "io-hygiene-baseline.json"

SCAN_ROOT = ROOT / "app"
EXCLUDED_PARTS = ("tests", "test_support")

# `run_blocking` is implemented in terms of the primitives this gate bans, so
# the module that owns the bound is the one place allowed to call them.
OFFLOAD_OWNER = "app/core/concurrency"

# Fully-qualified constructors whose cost is a network round trip and whose
# natural lifetime is the process. Qualified deliberately: `Client` and
# `AsyncClient` are among the most reused names in the dependency tree (composio,
# openai, kubernetes all export one), so matching the bare name would flag a
# dozen things this rule has no opinion about. Bare names are resolved through
# the importing module's own aliases instead -- see `_ImportAliases`.
# Each of these was measured in a production pod rather than assumed. The
# object stores resolve GKE credentials over the network (350-500ms); the
# vendor SDKs each build an httpx client and import their lazy namespaces
# (Composio 42-262ms, AsyncOpenAI 43-46ms); a bare httpx client is ~45ms.
PROCESS_LIFETIME_CLIENTS = {
    "obstore.store.GCSStore",
    "obstore.store.S3Store",
    "obstore.store.AzureStore",
    "httpx.Client",
    "httpx.AsyncClient",
    "sqlalchemy.create_engine",
    "sqlalchemy.ext.asyncio.create_async_engine",
    "redis.asyncio.Redis.from_url",
    "redis.asyncio.BlockingConnectionPool.from_url",
    "composio.Composio",
    "openai.AsyncOpenAI",
    "openai.OpenAI",
}

# Modules whose entire purpose is to hold one of these for the process. Each
# already implements the memoization this rule exists to require, using a keyed
# dict rather than a decorator, so the decorator exemption cannot see it.
PROCESS_LIFETIME_OWNERS = (
    "app/core/object_storage.py",
    "app/core/net/http_client.py",
    "app/core/infrastructure/redis/client.py",
    "app/core/infrastructure/db/session.py",
    "app/core/infrastructure/db/manager.py",
    "app/modules/datastore/infrastructure/session.py",
    "app/modules/function/application/function_runtime_http_client.py",
    "app/modules/agent/services/runtime_model_factory.py",
)

MEMOIZING_DECORATORS = {"lru_cache", "cache", "cached", "cached_property"}

# Dotted callees that hand work to a thread pool this process does not bound.
UNLIMITED_OFFLOADS = {
    "asyncio.to_thread",
    "anyio.to_thread.run_sync",
    "to_thread.run_sync",
    # The session-scope gate has counted this as a thread offload since it was
    # written; this one did not, so the two gates disagreed about the same call
    # and a `loop.run_in_executor(None, ...)` -- which offloads onto an
    # *unbounded* default executor -- passed here without comment.
    "run_in_executor",
    "loop.run_in_executor",
    "get_event_loop.run_in_executor",
    "get_running_loop.run_in_executor",
}


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
        return (
            f"{self.path}:{self.line}  {self.rule}  in {self.scope}()  [{self.detail}]"
        )


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _is_none(node: ast.AST) -> bool:
    """Whether an argument is a literal ``None``."""
    return isinstance(node, ast.Constant) and node.value is None


class _ImportAliases(ast.NodeVisitor):
    """Map the names a module actually uses onto their fully-qualified originals.

    ``from httpx import AsyncClient`` and ``from openai import AsyncClient`` put
    the same identifier in two files meaning two different things. Reading the
    imports is what lets the rule below tell them apart, so it can be specific
    about which clients it cares about instead of matching on a popular word.
    """

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module:
            return  # relative import: not a third-party client
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def resolve(self, dotted: str) -> str:
        if not dotted:
            return dotted
        head, _, rest = dotted.partition(".")
        target = self.aliases.get(head)
        if target is None:
            return dotted
        return f"{target}.{rest}" if rest else target


def _is_memoized(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _dotted(decorator).split(".")[-1] in MEMOIZING_DECORATORS
        for decorator in node.decorator_list
    )


class IoHygieneChecker(ast.NodeVisitor):
    def __init__(self, path: str, aliases: _ImportAliases | None = None) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._scope: list[str] = []
        self._offload_owner = OFFLOAD_OWNER in path
        self._aliases = aliases or _ImportAliases()
        self._lifetime_owner = any(
            path.endswith(owner) for owner in PROCESS_LIFETIME_OWNERS
        )
        # (is_sync, is_memoized) for each enclosing function, innermost last.
        self._functions: list[tuple[bool, bool]] = []

    def _visit_scoped(self, node: ast.AST, name: str) -> None:
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_sync: bool
    ) -> None:
        self._functions.append((is_sync, _is_memoized(node)))
        self._visit_scoped(node, node.name)
        self._functions.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_sync=True)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_sync=False)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node, node.name)

    def _in_uncached_sync_function(self) -> bool:
        """Whether the innermost enclosing function is a plain, unmemoized ``def``.

        Innermost, not any: a closure returned by a cached factory is built once
        but *called* per request, so it is the closure's own caching that
        decides, not its parent's.
        """
        if not self._functions:
            return False  # module scope: constructed once, which is the point
        is_sync, is_memoized = self._functions[-1]
        return is_sync and not is_memoized

    def visit_Call(self, node: ast.Call) -> None:
        callee = _dotted(node.func)
        if callee in UNLIMITED_OFFLOADS and not self._offload_owner:
            self._record(node.lineno, "unlimited-offload", callee)
        elif callee.endswith("aiohttp.ClientSession") or callee == "ClientSession":
            timeout = next((kw for kw in node.keywords if kw.arg == "timeout"), None)
            if timeout is None:
                self._record(node.lineno, "untimed-aiohttp-session", callee)
            elif _is_none(timeout.value):
                # `timeout=None` disables aiohttp's timeout entirely, so the
                # keyword being *present* proved nothing. A request to a hung
                # upstream then hangs for the life of the process, which is the
                # exact failure this rule exists to prevent -- and it passed.
                self._record(node.lineno, "disabled-aiohttp-timeout", callee)

        if not self._lifetime_owner and self._in_uncached_sync_function():
            qualified = self._aliases.resolve(callee)
            if qualified in PROCESS_LIFETIME_CLIENTS:
                self._record(node.lineno, "process-lifetime-construction", qualified)

        self.generic_visit(node)

    def _record(self, line: int, rule: str, detail: str) -> None:
        scope = ".".join(self._scope) or "<module>"
        self.violations.append(Violation(self.path, line, scope, rule, detail))


def collect(paths: list[Path]) -> list[Violation]:
    found: list[Violation] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _ImportAliases()
        aliases.visit(tree)
        checker = IoHygieneChecker(str(path.relative_to(ROOT)), aliases)
        checker.visit(tree)
        found.extend(checker.violations)
    return sorted(found, key=lambda v: (v.path, v.line, v.rule))


def source_files() -> list[Path]:
    return sorted(
        path
        for path in SCAN_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def _load_baseline(path: Path) -> dict[str, int]:
    """Read the baseline, accepting the older list form (one occurrence each)."""
    entries = json.loads(path.read_text(encoding="utf-8"))["violations"]
    if isinstance(entries, dict):
        return {key: int(count) for key, count in entries.items()}
    return dict(Counter(entries))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine.",
    )
    args = parser.parse_args()

    violations = collect(source_files())

    if args.update_baseline:
        payload = {
            "_comment": (
                "Pre-existing thread offloads that bypass the named limiters, "
                "aiohttp sessions with no timeout, and process-lifetime clients "
                "built per call. This file may shrink freely; growing it means new "
                "unbounded I/O. See scripts/check_io_hygiene.py."
            ),
            "violations": dict(sorted(Counter(v.key() for v in violations).items())),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {sum(payload['violations'].values())} entries")
        return 0

    baseline = _load_baseline(args.baseline)

    # Counted, not a set. The key carries no line number so that edits above a
    # violation do not churn the file -- which used to mean a second identical
    # call in an already-baselined function slipped through unreported. The
    # sibling gate had the same hole and it was hiding two real ones.
    counts = Counter(v.key() for v in violations)
    new: list[Violation] = []
    seen: Counter[str] = Counter()
    for violation in violations:
        seen[violation.key()] += 1
        if seen[violation.key()] > baseline.get(violation.key(), 0):
            new.append(violation)
    fixed = sum(
        max(0, allowed - counts.get(key, 0)) for key, allowed in baseline.items()
    )

    if fixed:
        print(f"✓ {fixed} baselined violation(s) gone — run --update-baseline")
    if not new:
        print(f"✓ I/O hygiene: no new violations ({sum(baseline.values())} baselined)")
        return 0

    print(f"✗ I/O hygiene: {len(new)} new violation(s)\n")
    for violation in new:
        print(f"  {violation.render()}")
    print(
        "\nOffload through run_blocking(..., limiter=...) so the work is bounded, "
        "and give every aiohttp session an explicit ClientTimeout."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
