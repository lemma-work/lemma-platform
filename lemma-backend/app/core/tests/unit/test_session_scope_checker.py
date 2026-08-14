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
    index.ingest(tree)
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

async def handler(uow_factory, repo):
    async with uow_factory() as uow:
        await repo.execute("select 1")
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
    import json

    checker = _load_checker()
    violations = checker.collect(checker.source_files())
    baseline = set(
        json.loads(checker.DEFAULT_BASELINE.read_text(encoding="utf-8"))["violations"]
    )
    current = {violation.key() for violation in violations}
    assert current - baseline == set(), "new session-scope violations; see the gate"
    assert baseline - current == set(), (
        "baseline lists violations that no longer exist; run --update-baseline"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
