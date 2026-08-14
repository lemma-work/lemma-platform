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
