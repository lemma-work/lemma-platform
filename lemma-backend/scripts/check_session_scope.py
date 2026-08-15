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
from collections import Counter
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
# The inverse of SESSION_OPENERS: a block that hands the pooled connection back
# for its duration. Recognised structurally so "I released first" stops being a
# claim in a comment and becomes something the gate can check -- and so the ten
# baselined sites that were already correct stop being reported as wrong. The
# runtime half is `app/core/infrastructure/db/transaction_locks.connection_released`,
# which commits when `safe_to_release` allows.
SESSION_RELEASERS = re.compile(r"(^|\.)connection_released$")


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
        # `async with SessionUnitOfWorkFactory(async_session_maker)() as uow:`
        # constructs the factory inline and calls it. Without this the whole
        # block was invisible to every rule -- 20+ production sites, including
        # ones that hold an open write transaction across an HTTP download.
        | SessionUnitOfWorkFactory
        # `async with ctx.uow() as uow:` is the standard form in streaq tasks,
        # so omitting it left the entire worker surface unchecked. Deliberately
        # NOT bare `uow`: `async with uow:` re-enters a unit of work that is
        # already open, and `SqlAlchemyUnitOfWork.__aenter__` returns `self`, so
        # it marks a transaction boundary on one session rather than opening a
        # second one. Counting it as a nested session reported three handlers as
        # holding two connections when they only ever hold one.
        | uow(?=\s*\()
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
    # The surface egress family all bottoms out in `target.adapter.<send>()`.
    # Anchored so `account_adapter.get` (a SQLAlchemy port) does not match.
    (r"(^|\.)adapter\.", "platform adapter"),
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

# --- Synchronous work that blocks the whole event loop ------------------------
#
# Everything above is about an `await`. But the checker only ever visited
# `ast.Await`, so a *synchronous* blocking call inside a session was invisible
# to it -- and that is strictly worse than the async case: an await at least
# lets other tasks run while the connection is pinned, whereas a sync call
# pins the connection *and* stops the loop.
#
# This is not hypothetical. `ComposioWebhookVerifier.verify` ran the synchronous
# Composio SDK on the event loop from an unauthenticated route, and no gate in
# the repo could see it.
#
# Kept to calls that are unambiguously blocking. A general "un-awaited call to
# a remote name" rule would fire on every `httpx.AsyncClient(...)` construction,
# and a gate with false positives gets baselined into irrelevance.
SYNC_BLOCKING_CALLS: tuple[tuple[str, str], ...] = (
    (r"^time\.sleep$", "blocking sleep"),
    (r"^(requests|urllib\.request)\.", "blocking HTTP"),
    (r"^requests\.sessions\.Session\.", "blocking HTTP"),
    (r"^subprocess\.(run|call|check_call|check_output|Popen)$", "subprocess"),
    (r"^os\.system$", "subprocess"),
    (r"^socket\.(create_connection|socket)$", "blocking socket"),
    # Reading a whole file synchronously on the loop, holding a connection.
    (r"^(pathlib\.)?Path\.(read_bytes|read_text|write_bytes|write_text)$", "blocking file I/O"),
)

SYNC_BLOCKING_PATTERNS = tuple(
    (re.compile(pattern), label) for pattern, label in SYNC_BLOCKING_CALLS
)


def _sync_blocking_label(callee: str) -> str | None:
    for pattern, label in SYNC_BLOCKING_PATTERNS:
        if pattern.search(callee):
            return label
    return None

# Receivers that make a call a query no matter what it is named. A repository
# method called `enqueue_run` writes an admission row; `store.save_import`
# writes a row. Without this the deny-list's verb matching would fire on the
# data-access layer, which is the one place it must not.
#
# Anchored to whole dotted segments, and that is the whole point of the pattern
# being this fiddly. It used to be a bare substring match, so *any* receiver
# containing one of these words was unconditionally "a database call" --
# `session_client.post`, `uow_llm.complete`, `outbox_webhook.send` all silenced
# the gate completely, and nothing would ever have reported it. A segment now
# has to *end* with one of these words (with an optional snake_case prefix, so
# `schedule_repository`, `self.__uow` and `AgentHostDispatchRepository(uow)` all
# still match), which is the thing that was actually meant. Case-insensitive
# because the receiver is often a class rather than an attribute -- the four
# `AgentHostDispatchRepository(...).enqueue_*` calls are precisely the
# "repository method named enqueue_run writes an admission row" case this
# exemption was written for.
DB_RECEIVERS = re.compile(
    r"(^|\.)[A-Za-z0-9_]*(repository|repositories|uow|session|dao|outbox|admission)(\.|$)",
    re.IGNORECASE,
)

# Third-party modules that talk to something over a network. A call to a symbol
# imported from one of these is non-database work by construction, and the
# call-graph propagation below can never learn that on its own: these functions
# have no definition inside `app/`, so a name lookup finds nothing and the call
# looks local and cheap.
REMOTE_MODULES = re.compile(
    r"""(?x)^(
          supertokens_python | aiohttp | httpx | requests | composio
        | slack_sdk | telegram | msal | resend | e2b | docker
        | redis | boto3 | botocore | azure
        | google\.(cloud|auth|oauth2) | googleapiclient
        | openai | anthropic | pydantic_ai | cohere | litellm
    )(\.|$)"""
)

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
        # provider/handler name -> one entry per definition sharing that name.
        # A list, not a set: `get_current_user` is both a plain dependency in
        # core/api/dependencies.py AND a route handler in user_controller.py
        # that takes `uow: UoWDep`. Keyed by name with overwrite, the handler
        # won, `CurrentUser` looked session-scoped, and every authenticated
        # route in the codebase was miscounted as holding a connection.
        self.params: dict[str, list[set[str]]] = {}
        # providers that yield while holding a session, e.g. `get_uow`
        self.seeds: set[str] = set()
        self.session_scoped: set[str] = set()
        # Interprocedural "this leaves the process" propagation. A controller
        # rarely calls httpx itself; it calls a service method that calls an
        # adapter that calls httpx. Matching only the leaf would miss every
        # real finding, so slowness is propagated up the call graph by name.
        # One entry per *definition*, not per name: `execute` has 21 of them and
        # they do not agree with each other.
        self.definitions: dict[str, list[dict]] = {}
        # Names imported from a networked third-party module. These have no
        # definition inside app/, so the call graph cannot discover them.
        self.remote_names: set[str] = set()
        self.remote_roots: set[str] = set()
        # Dependency FACTORIES: `PodViewerDep = require_pod_role(PodRole.VIEWER)`
        # returns `require_action(...)` which returns `Depends(_dependency)`.
        # Two hops, no `Annotated` anywhere, and `_dependency` takes a context
        # that holds a session -- so these routes hold a connection for the
        # whole response while carrying a comment saying they do not.
        # Project context managers that yield a live session to their caller.
        self.session_cms: set[str] = set()
        self._cm_bodies: list[tuple[str, ast.AST]] = []
        self.returns_depends: dict[str, set[str]] = {}
        self.returns_calls: dict[str, set[str]] = {}
        self.alias_factories: dict[str, set[str]] = {}
        # Dependencies that commit before returning -- see
        # `_hands_the_connection_back`. Excluded from propagation only: their
        # own bodies are still checked.
        self.releasing: set[str] = set()

    def ingest(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                self._ingest_alias(node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self._ingest_import(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.params.setdefault(node.name, []).append(_dependency_names(node))
                if _hands_the_connection_back(node):
                    self.releasing.add(node.name)
                if _yields_inside_session(node):
                    self.seeds.add(node.name)
                if _is_context_manager(node):
                    self._cm_bodies.append((node.name, node))
                    if _yields_inside_session(node, allow_context_manager=True):
                        self.session_cms.add(node.name)
                awaited, direct = _awaited_calls(node)
                self.definitions.setdefault(node.name, []).append(
                    {"awaits": awaited, "reason": direct}
                )
                self._ingest_returns(node)

    def _ingest_returns(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Record what a dependency factory hands back, so aliases can follow it."""
        for child in ast.walk(node):
            if not isinstance(child, ast.Return):
                continue
            if not isinstance(child.value, ast.Call):
                continue
            returned = child.value
            if _dotted(returned.func).split(".")[-1] == "Depends" and returned.args:
                self.returns_depends.setdefault(node.name, set()).add(
                    _dotted(returned.args[0]).split(".")[-1]
                )
            else:
                self.returns_calls.setdefault(node.name, set()).add(
                    _dotted(returned.func).split(".")[-1]
                )

    def _ingest_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.ImportFrom):
            if node.module and REMOTE_MODULES.search(node.module):
                for alias in node.names:
                    self.remote_names.add(alias.asname or alias.name)
            return
        for alias in node.names:
            if REMOTE_MODULES.search(alias.name):
                self.remote_roots.add(alias.asname or alias.name.split(".")[0])

    def resolve_slow(self) -> None:
        """Fixed point over definitions, resolved by name.

        A name is usable for propagation when EVERY definition sharing it is
        slow. Ambiguity only matters when the alternatives disagree: `execute`
        is 21 definitions of which most are queries, so it stays unusable, while
        `refresh_credentials` is 4 definitions that are all thread offloads and
        is perfectly safe to follow. The earlier revision required exactly one
        definition, which threw away every finding reached through a service
        interface with more than one implementation -- which is most of them.
        """
        changed = True
        while changed:
            changed = False
            # Sorted so the recorded reason is identical on every run: the
            # baseline keys include it, and a reason flipping between two
            # equally-true callees would churn the file for nothing.
            for name, definitions in sorted(self.definitions.items()):
                for definition in definitions:
                    if definition["reason"] is not None:
                        continue
                    for callee in sorted(definition["awaits"]):
                        if callee != name and self._name_is_slow(callee):
                            definition["reason"] = f"via {callee}"
                            changed = True
                            break

    #: How much of a name's definitions must be slow before a call to that name
    #: is treated as slow. Names are matched without a receiver type, so this is
    #: the precision/recall dial for the whole propagation pass.
    #:
    #: It used to be `all`, which is brittle in one direction and useless in the
    #: other. `get` has 56 definitions in this tree and 4 are slow; `create` has
    #: 58 and 4. Treating either as slow (i.e. `any`) would flood the gate and
    #: get it switched off. But requiring *every* definition meant one fast
    #: method sharing a name with seven slow ones silenced all seven -- a future
    #: edit anywhere in the tree could de-fang propagation for a name nobody was
    #: thinking about.
    #:
    #: Measured across the tree: 1.0, 0.9 and 0.75 all report exactly the same
    #: 33 violations today, so this is a robustness change rather than a
    #: behaviour change -- adding one fast `download_attachment_bytes` to the
    #: six slow ones no longer turns the rule off. 0.6 adds four more, which is
    #: a judgement call for its own change with its own evidence.
    SLOW_DEFINITION_RATIO = 0.75

    def _name_is_slow(self, name: str) -> bool:
        if name in self.remote_names:
            return True
        definitions = self.definitions.get(name)
        if not definitions:
            return False
        slow = sum(1 for d in definitions if d["reason"] is not None)
        return slow / len(definitions) >= self.SLOW_DEFINITION_RATIO

    def why_slow(self, callee: str) -> str | None:
        """Reason `callee` is non-database work, if it is."""
        direct = _classify_non_db(callee)
        if direct is not None:
            return direct
        parts = callee.split(".")
        if parts[0] in self.remote_roots:
            return "remote SDK"
        receiver = ".".join(parts[:-1])
        if receiver and DB_RECEIVERS.search(receiver.lower()):
            return None
        name = parts[-1]
        if name in self.remote_names:
            return "remote SDK"
        if not self._name_is_slow(name):
            return None
        # `None` is filtered before sorting. With the old all-or-nothing rule a
        # mixed name could never reach here, so mixing `str` and `None` in this
        # set was a latent `TypeError` waiting for the first loosening of
        # `_name_is_slow` -- which is exactly the change above.
        reasons = {
            d["reason"] for d in self.definitions.get(name, []) if d["reason"]
        }
        return sorted(reasons)[0] if reasons else None

    def _ingest_alias(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not targets:
            return
        providers = _depends_callees(node.value)
        for target in targets:
            if providers:
                self.alias_to_providers.setdefault(target, set()).update(providers)
            if isinstance(node.value, ast.Call):
                self.alias_factories.setdefault(target, set()).add(
                    _dotted(node.value.func).split(".")[-1]
                )

    def expand_session_context_managers(self) -> None:
        """Grow the derived opener set: a CM yielding inside another CM's session."""
        changed = True
        while changed:
            changed = False
            for name, node in self._cm_bodies:
                if name in self.session_cms:
                    continue
                if _yields_inside_session(
                    node, frozenset(self.session_cms), allow_context_manager=True
                ):
                    self.session_cms.add(name)
                    changed = True

    def _expand_factories(self) -> None:
        """Resolve `X = factory(...)` to whatever `Depends` the factory returns."""
        yields: dict[str, set[str]] = {
            name: set(targets) for name, targets in self.returns_depends.items()
        }
        changed = True
        while changed:
            changed = False
            for name, callees in sorted(self.returns_calls.items()):
                current = yields.setdefault(name, set())
                for callee in sorted(callees):
                    addition = yields.get(callee, set()) - current
                    if addition:
                        current |= addition
                        changed = True
        for alias, factories in self.alias_factories.items():
            for factory in factories:
                if yields.get(factory):
                    self.alias_to_providers.setdefault(alias, set()).update(
                        yields[factory]
                    )

    def resolve(self) -> None:
        """Fixed point over dependency providers.

        A name counts as session-scoped only when EVERY definition sharing it
        is -- the same rule the slowness propagation uses, and for the same
        reason. One definition holding a session says nothing about a different
        function that happens to share its name.
        """
        self._expand_factories()
        self.session_scoped = set(self.seeds)
        changed = True
        while changed:
            changed = False
            for name, definitions in sorted(self.params.items()):
                if name in self.session_scoped or name in self.releasing:
                    continue
                if all(
                    any(self._reaches_session(dep) for dep in dependencies)
                    for dependencies in definitions
                ):
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
    """Dependency names a route depends on, from its signature AND its decorator.

    `@router.get(..., dependencies=[PodViewerDep])` binds a dependency that
    never appears in the signature. It still runs, and if it yields a session it
    still holds a connection for the whole response -- which is exactly how two
    pod_bundle SSE routes pin a connection for the length of the stream while
    carrying a comment saying they hold none.
    """
    names: set[str] = set()
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        for keyword in decorator.keywords:
            if keyword.arg != "dependencies":
                continue
            names.update(_depends_callees(keyword.value))
            for child in ast.walk(keyword.value):
                if isinstance(child, ast.Name):
                    names.add(child.id)
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


def _is_zero_sleep(call: ast.Call) -> bool:
    """``await asyncio.sleep(0)`` is a yield to the loop, not a wait.

    It is the idiom for "let other tasks run" -- batch loops use it so a large
    backlog is spread out instead of dispatched as one burst. The connection is
    held for one loop tick, which is not the thing this gate exists to catch,
    and reporting it trains people to read the gate as noise.

    Only a literal zero counts. `sleep(delay)` with a variable stays a
    violation, because the checker cannot know what it holds.
    """
    if not SLEEP_CALLS.search(_dotted(call.func)):
        return False
    if len(call.args) != 1 or call.keywords:
        return False
    arg = call.args[0]
    return isinstance(arg, ast.Constant) and arg.value == 0


SLEEP_CALLS = re.compile(r"(^|\.)sleep$")


def _awaited_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], str | None]:
    """Bare names this function awaits, and why it is directly non-DB (if it is).

    Nested function definitions are skipped: their awaits belong to them.

    Awaits inside a ``connection_released`` block are skipped too, and for the
    same reason propagation exists at all: this index answers "does calling this
    hold a connection across slow work", not "is this slow". A function that
    hands the connection back before its slow call does not, and neither does
    anything that calls it -- so it must not poison the name for every caller.
    """
    awaited: set[str] = set()
    direct: str | None = None
    stack: list[ast.AST] = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(current, ast.AsyncWith) and any(
            SESSION_RELEASERS.search(_dotted(item.context_expr))
            for item in current.items
        ):
            continue
        if isinstance(current, ast.Await) and isinstance(current.value, ast.Call):
            if _is_zero_sleep(current.value):
                # A yield to the loop, not a wait -- and the index has to agree
                # with the visitor about that, or the name stays slow for every
                # caller while the site itself reports clean.
                stack.extend(ast.iter_child_nodes(current))
                continue
            callee = _dotted(current.value.func)
            awaited.add(callee.rsplit(".", 1)[-1])
            if direct is None:
                direct = _classify_non_db(callee)
        stack.extend(ast.iter_child_nodes(current))
    return awaited, direct


def _yields_inside_session(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    derived: frozenset[str] = frozenset(),
    *,
    allow_context_manager: bool = False,
) -> bool:
    """True for a provider that opens a session and yields while holding it.

    Used two ways: to seed `get_uow`-shaped FastAPI dependencies (which are not
    context managers), and to discover the project's own session-yielding
    context managers, where the `@asynccontextmanager` exemption must not apply.
    """
    if _is_context_manager(node) and not allow_context_manager:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.AsyncWith) and _opens_session(child, derived):
            if any(
                isinstance(inner, (ast.Yield, ast.YieldFrom))
                for stmt in child.body
                for inner in ast.walk(stmt)
            ):
                return True
    return False


def _hands_the_connection_back(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """True for a dependency that commits before handing its value to the route.

    A FastAPI dependency that reads the database and then commits has given the
    pooled connection back. Its dependents run with nothing checked out, so it
    must not mark them request-scoped -- otherwise the two pod_bundle SSE routes
    are reported for a hold that `_release_after_authorization` already ended,
    and the only way to silence it is a baseline entry that reads as a real bug.

    Deliberately narrow, because the failure mode is a blind gate:

    * The release must be a **top-level statement** of the function body. A
      release inside `if`/`try` may not run, and a dependency that sometimes
      keeps its connection keeps it as far as this checker is concerned.
    * A `yield` anywhere disqualifies the function. Yield-dependencies resume
      after the response, so FastAPI keeps them (and their session) alive for
      the whole request no matter what they committed on the way in.

    The route's own body is unaffected: `holds_session` reads the route's
    dependency names, so a handler that takes `uow: UoWDep` itself is still
    request-scoped.
    """
    for child in ast.walk(node):
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            return False
    for statement in node.body:
        call = None
        if isinstance(statement, ast.Expr):
            call = statement.value
        if isinstance(call, ast.Await):
            call = call.value
        if isinstance(call, ast.Call) and _dotted(call.func).split(".")[-1] in (
            "_release_after_authorization",
            "release_after_authorization",
        ):
            return True
        if isinstance(statement, ast.AsyncWith) and any(
            isinstance(item.context_expr, ast.Call)
            and _dotted(item.context_expr.func).split(".")[-1] == "connection_released"
            for item in statement.items
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


def _opens_session(node: ast.AsyncWith, derived: frozenset[str] = frozenset()) -> bool:
    """True if this `async with` checks out a pooled connection.

    `derived` carries the project's own session-yielding context managers,
    discovered rather than listed: `uow_scope`, `pod_context_scope`,
    `pod_services`, `connector_services` and friends all wrap a session and hand
    it to their caller, so an `async with` on one holds a connection exactly as
    an `async with session_maker()` does. Hardcoding the names meant every new
    helper started life invisible to the gate.
    """
    for item in node.items:
        callee = _dotted(item.context_expr)
        if SESSION_OPENERS.search(callee):
            return True
        if callee.rsplit(".", 1)[-1] in derived:
            return True
    return False


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
        self._openers = frozenset(index.session_cms)
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
        if any(
            SESSION_RELEASERS.search(_dotted(item.context_expr))
            for item in node.items
        ):
            # The connection is given back for the body of this block, so work
            # inside it holds nothing. Restored afterwards: the caller may go on
            # to query again, and the next await is judged normally.
            outer = self._session_depth
            self._session_depth = 0
            for child in node.body:
                self.visit(child)
            self._session_depth = outer
            return
        if not _opens_session(node, self._openers):
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
        if (
            self._session_depth
            and isinstance(node.value, ast.Call)
            and not _is_zero_sleep(node.value)
        ):
            callee = _dotted(node.value.func)
            label = self.index.why_slow(callee)
            if label is not None:
                self._record(node.lineno, "non-db-await", f"{label}: {callee}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Synchronous blocking work inside a session. Worse than the awaited
        # kind: the connection is pinned *and* the loop cannot make progress
        # for anyone else while it happens.
        if self._session_depth:
            label = _sync_blocking_label(_dotted(node.func))
            if label is not None:
                self._record(
                    node.lineno, "sync-blocking-call", f"{label}: {_dotted(node.func)}"
                )
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
    index.expand_session_context_managers()
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



def _load_baseline(path: Path) -> dict[str, int]:
    """Read the baseline, accepting the older list form.

    It used to be a list of keys, which could only express "this key is
    allowed" -- not how many times. Reading a list as one-each keeps an
    un-migrated checkout working and makes the first `--update-baseline`
    write the counted form.
    """
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
        help="Rewrite the baseline from the current tree. Shrinking is always fine; "
        "growing it needs a reason in the PR description.",
    )
    args = parser.parse_args()

    violations = collect(source_files())

    counts = Counter(v.key() for v in violations)

    if args.update_baseline:
        payload = {
            "_comment": (
                "Pre-existing places where a DB connection is held across non-database "
                "work. This file may shrink freely; growing it means a new connection "
                "is being held across an LLM call, an HTTP request or a thread offload. "
                "Entries are {key: count}: the key has no line number so that edits "
                "above a violation do not churn the file, and the count is what stops "
                "a baselined function having a free slot for a second one. "
                "See scripts/check_session_scope.py."
            ),
            "violations": dict(sorted(counts.items())),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {sum(counts.values())} entries")
        return 0

    baseline = _load_baseline(args.baseline)

    # Report every violation whose key now occurs more often than the baseline
    # allows. Keys carry no line number -- deliberately, so that unrelated edits
    # above a violation do not churn the file -- which used to mean a second
    # identical await in an already-baselined function was accepted in silence.
    # Counting closes that without reintroducing the churn.
    new: list[Violation] = []
    seen: Counter[str] = Counter()
    for violation in violations:
        key = violation.key()
        seen[key] += 1
        if seen[key] > baseline.get(key, 0):
            new.append(violation)

    fixed = sum(
        max(0, allowed - counts.get(key, 0)) for key, allowed in baseline.items()
    )

    if fixed:
        print(f"✓ {fixed} baselined violation(s) gone — run --update-baseline")
    if not new:
        print(
            "✓ session scope: no new violations "
            f"({sum(baseline.values())} baselined)"
        )
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
