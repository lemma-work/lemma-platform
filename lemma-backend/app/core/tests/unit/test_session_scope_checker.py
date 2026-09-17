"""Tests for the session-scope gate (scripts/check_session_scope.py).

The gate is what lets `worker_concurrency` be sized from RAM instead of from
`db_pool_size`, so it has to be right about both directions: it must catch a
connection held across non-database work, and it must stay quiet on the
patterns the codebase uses correctly. A checker that cries wolf gets baselined
into irrelevance, which is worse than no checker at all.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_checker():
    script = Path(__file__).resolve().parents[4] / "scripts" / "check_session_scope.py"
    spec = importlib.util.spec_from_file_location("check_session_scope", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the script uses `from __future__ import
    # annotations`, so @dataclass resolves its field types through
    # sys.modules at class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(source: str) -> list:
    checker = _load_checker()
    tree = ast.parse(source)
    index = checker.DependencyIndex()
    # The same path the visitor is given: the index keys definitions by
    # `path:lineno` so a call can be judged with the definition it sits inside
    # taken out of the running, and the two halves have to agree on the name.
    index.ingest(tree, "sample.py")
    index.resolve()
    index.resolve_slow()
    visitor = checker.SessionScopeChecker("sample.py", index)
    visitor.visit(tree)
    return visitor.violations


def _rules(source: str) -> set[str]:
    return {violation.rule for violation in _run(source)}


def test_flags_thread_offload_inside_a_session():
    source = """
async def ingest(uow_factory):
    async with uow_factory() as uow:
        await uow.session.execute("select 1")
        await run_blocking(extract, document)
"""
    assert "non-db-await" in _rules(source)


def test_flags_outbound_http_inside_a_session():
    source = """
async def sync(uow_factory, client):
    async with uow_factory() as uow:
        await client.post("https://example.test/hook")
"""
    assert "non-db-await" in _rules(source)


def test_flags_sleep_inside_a_session():
    source = """
import asyncio

async def poll(uow_factory):
    async with uow_factory() as uow:
        await asyncio.sleep(5)
"""
    assert "non-db-await" in _rules(source)


def test_allows_a_session_that_only_queries():
    source = """
async def load(uow_factory):
    async with uow_factory() as uow:
        row = await uow.session.execute("select 1")
        await uow.commit()
    return row
"""
    assert _run(source) == []


def test_allows_slow_work_after_the_session_closes():
    """The prescribed fix must not itself trip the gate."""
    source = """
async def publish(uow_factory, client):
    async with uow_factory() as uow:
        row = await uow.session.execute("select 1")
    await client.post("https://example.test/hook")
    async with uow_factory() as uow:
        await uow.session.execute("update ...")
"""
    assert _run(source) == []


def test_flags_a_session_held_across_a_yield():
    source = """
async def stream(uow_factory):
    async with uow_factory() as uow:
        async for row in uow.session.stream("select 1"):
            yield row
"""
    assert "session-across-yield" in _rules(source)


def test_exempts_asynccontextmanager_plumbing():
    """`@asynccontextmanager` yields the session by construction."""
    source = """
from contextlib import asynccontextmanager

@asynccontextmanager
async def uow_scope(session_maker):
    async with session_maker() as session:
        yield session
"""
    assert _run(source) == []


def test_discovers_the_projects_own_session_yielding_context_managers():
    """`async with pod_services(...)` holds a connection just as much.

    These are found, not listed: hardcoding names meant every new helper
    started life invisible to the gate.
    """
    source = """
from contextlib import asynccontextmanager

@asynccontextmanager
async def pod_services(uow_factory):
    async with uow_factory() as uow:
        yield Services(uow)

async def tool(uow_factory, client):
    async with pod_services(uow_factory) as services:
        await client.post("https://example.test/hook")
"""
    violations = _run(source)
    assert [v.scope for v in violations] == ["tool"]
    assert violations[0].rule == "non-db-await"


def test_flags_a_nested_session():
    source = """
async def outer(uow_factory):
    async with uow_factory() as uow:
        async with uow_factory() as inner:
            await inner.session.execute("select 1")
"""
    assert "nested-session" in _rules(source)


def test_resets_scope_across_a_nested_function_definition():
    """A closure defined inside a session block does not run inside it."""
    source = """
async def outer(uow_factory, client):
    async with uow_factory() as uow:
        async def later():
            await client.post("https://example.test/hook")
        register(later)
"""
    assert _run(source) == []


def test_flags_request_scoped_dependency_holding_a_connection():
    """The `Depends(get_uow)` path, which has no `async with` to see."""
    source = """
from typing import Annotated
from fastapi import Depends
from contextlib import asynccontextmanager

async def get_uow():
    async with create_uow_from_session_maker(async_session_maker) as uow:
        yield uow

UoWDep = Annotated[object, Depends(get_uow)]

async def handler(uow: UoWDep, client):
    await client.post("https://example.test/hook")
"""
    assert "non-db-await/request-scoped" in _rules(source)


def test_request_scope_propagates_through_a_service_dependency():
    """`get_service(uow: UoWDep)` makes every handler depending on it session-held."""
    source = """
from typing import Annotated
from fastapi import Depends

async def get_uow():
    async with create_uow_from_session_maker(async_session_maker) as uow:
        yield uow

UoWDep = Annotated[object, Depends(get_uow)]

def get_service(uow: UoWDep):
    return Service(uow)

ServiceDep = Annotated[object, Depends(get_service)]

async def handler(service: ServiceDep, client):
    await client.post("https://example.test/hook")
"""
    assert "non-db-await/request-scoped" in _rules(source)


def test_decorator_dependencies_hold_a_connection_too():
    """`dependencies=[...]` never appears in the signature but still runs.

    Two pod_bundle SSE routes pin a connection for the length of the stream
    this way, while carrying a comment saying they hold none.
    """
    source = """
from fastapi import Depends

async def get_uow():
    async with create_uow_from_session_maker(async_session_maker) as uow:
        yield uow

def require_pod_role(role):
    return require_action(role)

def require_action(permission):
    return Depends(_dependency)

async def _dependency(uow: UoWDep):
    return uow

UoWDep = Annotated[object, Depends(get_uow)]
PodViewerDep = require_pod_role("viewer")

@router.get("/x", dependencies=[PodViewerDep])
async def stream_events(client):
    await client.post("https://example.test/hook")
"""
    assert "non-db-await/request-scoped" in _rules(source)


def test_a_name_collision_does_not_make_every_route_request_scoped():
    """`get_current_user` is both a plain dependency and a route handler.

    Keyed by name with overwrite, the handler's `uow: UoWDep` won and every
    authenticated route in the codebase counted as holding a connection.
    """
    source = """
from fastapi import Depends

async def get_uow():
    async with create_uow_from_session_maker(async_session_maker) as uow:
        yield uow

UoWDep = Annotated[object, Depends(get_uow)]

def get_current_user(request):
    return request.state.user

CurrentUser = Annotated[object, Depends(get_current_user)]

async def get_current_user(uow: UoWDep):
    return await uow.session.execute("select 1")

async def some_route(user: CurrentUser, client):
    await client.post("https://example.test/hook")
"""
    # `get_uow` itself is reported, as always; the point is that `some_route`
    # is not dragged in with it.
    assert [v.scope for v in _run(source)] == ["get_uow"]


def test_slowness_propagates_through_an_unambiguous_callee():
    """A controller calls a service that calls httpx; the controller is flagged."""
    source = """
async def _deliver_to_slack(client, payload):
    await client.post("https://slack.test/api", json=payload)

async def handler(uow_factory, client, payload):
    async with uow_factory() as uow:
        await _deliver_to_slack(client, payload)
"""
    violations = _run(source)
    assert [v.rule for v in violations] == ["non-db-await"]
    assert violations[0].detail == "outbound HTTP: _deliver_to_slack"


def test_ambiguous_names_propagate_when_every_definition_is_slow():
    """Ambiguity only matters when the alternatives disagree.

    `refresh_credentials` has four implementations and all of them are thread
    offloads; refusing to follow it threw away real findings for no safety gain.
    """
    source = """
class SlackAdapter:
    async def deliver(self, payload):
        await self.client.post("https://slack.test", json=payload)

class TeamsAdapter:
    async def deliver(self, payload):
        await self.client.post("https://teams.test", json=payload)

async def handler(uow_factory, adapter, payload):
    async with uow_factory() as uow:
        await adapter.deliver(payload)
"""
    assert "non-db-await" in _rules(source)


def test_symbols_imported_from_a_networked_sdk_are_slow():
    """These have no definition in app/, so the call graph cannot find them."""
    source = """
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user

async def deactivate(uow_factory, user_id):
    async with uow_factory() as uow:
        await uow.session.execute("update users ...")
        await revoke_all_sessions_for_user(user_id)
"""
    violations = _run(source)
    assert [v.rule for v in violations] == ["non-db-await"]
    assert violations[0].detail.startswith("remote SDK")


def test_a_local_module_is_not_mistaken_for_a_remote_one():
    source = """
from app.modules.pod.services import load_pod

async def handler(uow_factory, pod_id):
    async with uow_factory() as uow:
        await load_pod(uow, pod_id)
"""
    assert _run(source) == []


def test_ambiguous_names_do_not_propagate():
    """Two definitions of one name means resolution is a guess -- stay quiet.

    Without this, `execute` (defined on every repository) resolves to whichever
    definition happened to be slow and the gate flags all database access.
    """
    source = """
async def execute(client):
    await client.post("https://example.test/hook")

class Repo:
    async def execute(self, sql):
        return await self.session.execute(sql)

class Engine:
    async def execute(self, sql):
        return await self.session.execute(sql)

async def handler(uow_factory, thing):
    async with uow_factory() as uow:
        await thing.execute("select 1")
"""
    assert _run(source) == []


def test_repository_receiver_is_never_non_db():
    """`enqueue_run` on a repository writes a row; it is not a job dispatch."""
    source = """
async def dispatch(uow_factory, dispatch_repository):
    async with uow_factory() as uow:
        await dispatch_repository.enqueue_run(host_id=1)
"""
    assert _run(source) == []


def test_baseline_matches_the_tree():
    """The committed baseline must describe the code as it actually is.

    A stale baseline either hides a regression or fails the build for something
    already fixed, and both teach people to ignore it.
    """
    from collections import Counter

    checker = _load_checker()
    violations = checker.collect(checker.source_files())
    # Counted, matching the gate. Reading this as a `set` compared keys only,
    # so a function that grew a second identical violation -- the hole the
    # counted baseline was introduced to close -- would have slipped past the
    # test that exists to keep the baseline honest.
    baseline = checker._load_baseline(checker.DEFAULT_BASELINE)
    current = Counter(violation.key() for violation in violations)

    grew = {
        k: (current[k], baseline.get(k, 0))
        for k in current
        if current[k] > baseline.get(k, 0)
    }
    shrank = {
        k: (current.get(k, 0), v) for k, v in baseline.items() if current.get(k, 0) < v
    }

    assert not grew, f"new session-scope violations; see the gate: {grew}"
    assert not shrank, (
        f"baseline lists violations that no longer exist, run --update-baseline: {shrank}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))


# --- synchronous blocking work ------------------------------------------------
#
# The checker only ever visited `ast.Await`, so a *synchronous* blocking call
# inside a session was invisible -- and that case is strictly worse than the
# awaited one. An await at least lets other tasks run while the connection is
# pinned; a sync call pins the connection *and* stops the loop for everyone.
#
# Composio webhook verification ran the synchronous Composio SDK on the event
# loop from an unauthenticated route, and no gate in this repo could see it.


@pytest.mark.parametrize(
    ("call", "label"),
    [
        ("time.sleep(2)", "blocking sleep"),
        ("requests.post(url, json=body)", "blocking HTTP"),
        ("subprocess.run(['ls'])", "subprocess"),
        ("os.system('ls')", "subprocess"),
    ],
)
def test_synchronous_blocking_work_inside_a_session_is_reported(
    call: str, label: str
) -> None:
    violations = _run(
        "async def handler(uow_factory):\n"
        "    async with uow_factory() as uow:\n"
        "        await uow.session.execute(query)\n"
        f"        {call}\n"
    )
    assert [v.rule for v in violations] == ["sync-blocking-call"]
    assert violations[0].detail.startswith(label)


def test_the_same_call_outside_the_session_is_not_reported() -> None:
    """The rule is about the hold, not about blocking in general.

    Blocking the loop outside a session is a different problem with a different
    gate (`check_io_hygiene`). Reporting it here would make this gate noisy
    about something it is not measuring, and a noisy gate gets baselined.
    """
    violations = _run(
        "async def handler(uow_factory):\n"
        "    async with uow_factory() as uow:\n"
        "        await uow.session.execute(query)\n"
        "    time.sleep(2)\n"
    )
    assert violations == []


def test_a_constructor_from_a_remote_module_is_not_a_blocking_call() -> None:
    """Deliberately narrow: building a client is not doing I/O.

    A general "un-awaited call to a remote name" rule would fire on every
    `httpx.AsyncClient(...)`, and false positives are how a gate ends up
    switched off. Only calls that unambiguously block are listed.
    """
    violations = _run(
        "async def handler(uow_factory):\n"
        "    async with uow_factory() as uow:\n"
        "        client = httpx.AsyncClient(timeout=30)\n"
        "        await uow.session.execute(query)\n"
    )
    assert [v.rule for v in violations] == []


# --- how propagation decides a name is slow ------------------------------------


def test_one_fast_definition_no_longer_silences_the_slow_ones() -> None:
    """The rule used to be all-or-nothing, which was brittle in a bad direction.

    Names are matched without a receiver type, so `_name_is_slow` is the
    precision/recall dial for the whole propagation pass. Requiring *every*
    definition of a name to be slow meant that adding one fast method sharing a
    name with several slow ones turned the rule off for all of them -- silently,
    from anywhere in the tree, for a name nobody was thinking about.

    Here `fetch_remote` is slow in three definitions and fast in a fourth. It
    must stay slow.
    """
    checker = _load_checker()
    index = checker.DependencyIndex()
    index.definitions["fetch_remote"] = [
        {"reason": "outbound HTTP", "awaits": set()},
        {"reason": "outbound HTTP", "awaits": set()},
        {"reason": "outbound HTTP", "awaits": set()},
        {"reason": None, "awaits": set()},
    ]

    assert index._name_is_slow("fetch_remote") is True
    assert index.why_slow("helper.fetch_remote") == "outbound HTTP"


def test_a_facade_is_not_judged_against_its_own_definition() -> None:
    """Delegating to the same method name must not report the delegator.

    `FunctionRevisionUseCases.get_revision` opens a short scope, resolves the
    revision, closes it, and only then reads the code from storage -- the shape
    this gate exists to encourage. Because it reads storage it is itself a slow
    definition of `get_revision`, and with only two definitions of that name in
    the tree it pushed its own call to `FunctionRevisionService.get_revision`
    over the ratio. The method reported itself.

    `resolve_slow` already refuses self-reference by name; this is the reporting
    half of the same rule, and the second half below is what keeps it from
    becoming a blanket exemption for the name.
    """
    source = """
class UseCases:
    async def get_revision(self, pod_id):
        async with self._uow_factory() as uow:
            revision = await service.get_revision(pod_id)
        revision.code = await service.read_revision_code(revision)
        return revision


class Service:
    async def get_revision(self, pod_id):
        return await self.repository.get_revision_by_hash(pod_id)

    async def read_revision_code(self, revision):
        return await self.storage.read_file(revision.code_path)
"""
    assert _run(source) == []

    # The teeth. Same shape, but the delegate really does read storage: the
    # exemption covers the enclosing definition, never the name.
    slow = source.replace(
        "        return await self.repository.get_revision_by_hash(pod_id)",
        "        return await self.storage.read_file(pod_id)",
    )
    assert [v.rule for v in _run(slow)] == ["non-db-await"]


def test_a_name_that_is_usually_fast_is_still_not_slow() -> None:
    """The other direction, which is why `any` is not the answer.

    `get` has 56 definitions in this tree and four are slow; `create` has 58 and
    four. Marking either slow would report most of the codebase and the gate
    would be switched off within a week.
    """
    checker = _load_checker()
    index = checker.DependencyIndex()
    index.definitions["get"] = [
        {"reason": None, "awaits": set()} for _ in range(52)
    ] + [{"reason": "outbound HTTP", "awaits": set()} for _ in range(4)]

    assert index._name_is_slow("get") is False
    assert index.why_slow("thing.get") is None


def test_a_mixed_name_reports_a_reason_rather_than_crashing() -> None:
    """`why_slow` sorted a set that could contain `None`.

    Unreachable under the old all-or-nothing rule -- a mixed name never got
    this far -- so it was a `TypeError` waiting for the first loosening, which
    is precisely the change above. Found by measuring thresholds, not by
    reading.
    """
    checker = _load_checker()
    index = checker.DependencyIndex()
    index.definitions["mixed"] = [
        {"reason": "redis", "awaits": set()},
        {"reason": "outbound HTTP", "awaits": set()},
        {"reason": "outbound HTTP", "awaits": set()},
        {"reason": None, "awaits": set()},
    ]

    assert index.why_slow("thing.mixed") == "outbound HTTP"


_RELEASING_DEPENDENCY = """
from fastapi import Depends

async def get_uow():
    async with create_uow_from_session_maker(async_session_maker) as uow:
        yield uow

UoWDep = Annotated[object, Depends(get_uow)]

async def get_pod_context(uow: UoWDep) -> Context:
    ctx = await resolve_pod_context(session=uow.session)
    %(release)s
    return ctx

PodContextDep = Annotated[object, Depends(get_pod_context)]

@router.get("/x")
async def stream_events(ctx: PodContextDep, client):
    await client.post("https://example.test/hook")
"""


def test_a_dependency_that_commits_first_does_not_make_a_route_request_scoped():
    """`_release_after_authorization` gives the connection back before the route runs.

    Without this the two pod_bundle SSE routes are reported for a hold that
    ended in the dependency, and the only way to quiet them is a baseline entry
    that reads like an unfixed bug.
    """
    source = _RELEASING_DEPENDENCY % {
        "release": "await _release_after_authorization(uow)"
    }

    # `get_uow` itself still yields inside its session -- that is the provider
    # this whole exemption is about, and it is reported either way.
    assert _rules(source) == {"session-across-yield"}


def test_the_same_dependency_without_the_release_still_counts():
    """The exemption is the commit, not the shape of the dependency."""
    source = _RELEASING_DEPENDENCY % {"release": "pass"}

    assert "non-db-await/request-scoped" in _rules(source)


def test_a_release_that_might_not_run_is_not_a_release():
    """A commit inside `if` leaves the caller holding a connection on the other branch."""
    source = _RELEASING_DEPENDENCY % {
        "release": "if fresh:\n        await _release_after_authorization(uow)"
    }

    assert "non-db-await/request-scoped" in _rules(source)


def test_a_yield_dependency_gets_no_exemption_for_committing():
    """FastAPI resumes it after the response, so its session outlives the release."""
    source = """
from fastapi import Depends

async def get_uow():
    async with create_uow_from_session_maker(async_session_maker) as uow:
        yield uow

UoWDep = Annotated[object, Depends(get_uow)]

async def get_pod_context(uow: UoWDep):
    await _release_after_authorization(uow)
    yield ctx

PodContextDep = Annotated[object, Depends(get_pod_context)]

@router.get("/x")
async def stream_events(ctx: PodContextDep, client):
    await client.post("https://example.test/hook")
"""

    assert "non-db-await/request-scoped" in _rules(source)


# --- work deferred to after the commit ----------------------------------------


def test_work_registered_with_after_commit_is_not_a_hold():
    """The shape `_invalidate_snapshots_after_commit` uses, and 29 callers inherit.

    Read literally the helper awaits a Redis round trip, so every role mutation
    in the tree inherited it. Neither branch can hold a connection: the deferred
    one runs after the commit, and the inline one runs only when there is no
    unit of work -- which is exactly when there is no pooled connection to keep.
    """
    source = """
async def invalidate(session, uow):
    async def _run():
        await run_blocking(purge_snapshots)

    if uow is None:
        await _run()
        return
    uow.after_commit(_run)


async def mutate(uow_factory):
    async with uow_factory() as uow:
        await uow.session.execute("update roles set x = 1")
        await invalidate(uow.session, uow)
"""
    assert _rules(source) == set()


def test_a_nested_function_not_registered_is_still_a_hold():
    """The narrowness is the point: only a name handed to `after_commit` is safe."""
    source = """
async def invalidate(session, uow):
    async def _run():
        await run_blocking(purge_snapshots)

    await _run()


async def mutate(uow_factory):
    async with uow_factory() as uow:
        await uow.session.execute("update roles set x = 1")
        await invalidate(uow.session, uow)
"""
    assert "non-db-await" in _rules(source)


def test_a_session_opened_from_a_private_factory_attribute_is_seen():
    """`self._uow_factory()` was invisible: 39 sites across 12 files unchecked."""
    source = """
class Service:
    async def run(self):
        async with self._uow_factory() as uow:
            await uow.session.execute("select 1")
            await run_blocking(extract, document)
"""
    assert "non-db-await" in _rules(source)


# --- committing on purpose before a slow call ---------------------------------


def test_a_commit_before_the_slow_call_is_not_a_hold():
    """`create_auth_config` and `update_install` do exactly this, deliberately.

    Both commit with a comment saying the network work must not be waited on
    holding a pooled connection. Read without statement order they look like
    holds, and so does every controller that calls them.
    """
    source = """
async def install(uow_factory):
    async with uow_factory() as uow:
        await uow.session.execute("select 1")
        await uow.commit()
        await run_blocking(negotiate_with_server)
"""
    assert _rules(source) == set()


def test_a_query_after_the_commit_re_acquires():
    """The span closes when something queries again, or the gate goes blind.

    Commit, insert, then call out is a real hold: the insert took a connection
    back out and the call is waiting on it.
    """
    source = """
async def install(uow_factory):
    async with uow_factory() as uow:
        await uow.commit()
        await uow.session.execute("insert into installs values (1)")
        await run_blocking(negotiate_with_server)
"""
    assert "non-db-await" in _rules(source)


def test_slow_work_between_a_commit_and_a_write_is_still_released():
    """The shape one boundary could not describe: commit, call out, then write.

    `create_auth_config` negotiates with a tenant-named MCP server after its
    commit and inserts afterwards. The negotiation is genuinely released; the
    insert genuinely re-acquires.
    """
    source = """
async def install(uow_factory):
    async with uow_factory() as uow:
        await uow.commit()
        await run_blocking(negotiate_with_server)
        await uow.auth_config_repository.create(row)
"""
    assert _rules(source) == set()


def test_a_call_to_a_closure_defined_here_is_not_voted_on() -> None:
    """A nested `def` is not an ambiguous name.

    `_retry_failed_conversation` awaits a closure it defines three lines above.
    The bare name `run` has 21 definitions in this tree and 11 are slow, so the
    ratio answered "job enqueue" for a call that can only mean the local one.
    """
    source = """
async def handler(uow_factory, context):
    async def run(scoped_uow):
        return await agent_conversations.retry_failed_run(scoped_uow, context)

    async with uow_factory() as scoped_uow:
        return await run(scoped_uow)


class Harness:
    async def run(self, ctx):
        return await self.queue.enqueue_run(ctx)
"""
    assert _run(source) == []

    # The teeth: the closure itself doing slow work is still a hold. The rule
    # changes which definition is consulted, not whether one is.
    slow = source.replace(
        "        return await agent_conversations.retry_failed_run(scoped_uow, context)",
        "        return await self.storage.download_file(context)",
    )
    assert [v.rule for v in _run(slow)] == ["non-db-await"]


def test_a_commit_at_the_top_of_a_loop_body_releases_for_that_iteration() -> None:
    """Per-iteration release, which is the only kind that works in a loop.

    `DatastoreEventHandler` fires one schedule at a time and each one may run an
    LLM inference. A single commit before the loop is undone by the first fire
    row written inside it, so the release has to happen per iteration -- and a
    commit at the top of a loop body runs on every one of them. If the loop does
    not run, neither does the slow call it was protecting.
    """
    source = """
async def fire_all(uow_factory, schedules, repo):
    async with uow_factory() as uow:
        for schedule in schedules:
            await uow.commit()
            await processor.run_stream(schedule)
            await repo.record_fire(schedule.id)
"""
    assert _run(source) == []

    # The teeth: `if`/`try` are still not descended into, so a commit that may
    # not run still does not release.
    branched = source.replace(
        "            await uow.commit()",
        "            if schedule.wants_release:\n                await uow.commit()",
    )
    assert [v.rule for v in _run(branched)] == ["non-db-await"]


def test_commit_now_counts_as_the_commit() -> None:
    """A service reaching its unit of work through the session still releases.

    `commit_now` exists because a service is built from a session, so the commit
    is unavoidably written as "if there is one" -- and the only case it skips is
    the one where there is no pooled connection to hand back. Naming the helper
    asserts that once instead of at every call site.
    """
    source = """
async def promote(uow_factory, repository):
    async with uow_factory() as uow:
        app = await repository.create(entity)
        await commit_now(repository)
        archive = await run_blocking(zip_it, app)
        return archive
"""
    assert _run(source) == []

    without = source.replace("        await commit_now(repository)\n", "")
    assert [v.rule for v in _run(without)] == ["non-db-await"]


def test_a_commit_inside_a_branch_does_not_release():
    """It may not run, and a block that sometimes keeps its connection keeps it."""
    source = """
async def install(uow_factory, should_commit):
    async with uow_factory() as uow:
        if should_commit:
            await uow.commit()
        await run_blocking(negotiate_with_server)
"""
    assert "non-db-await" in _rules(source)
