"""Visibility must stay index-driven as a pod grows.

The sibling equivalence suite proves the visibility statement returns the right
ids; it runs on a handful of files and cannot see how the work scales. That gap
hid a real regression: the first version of this statement expressed the
ancestor check as ``LEFT(descendant.path, LENGTH(ancestor.path) + 1) =
ancestor.path || '/'``, a theta-join on a function of both sides that no index
can serve. It was correct, it was one statement, and on a 16,000-file pod it
took 6.6 seconds instead of 0.14 — a full inner scan per outer row.

So the assertion here is on the *plan*, not the clock. Wall time depends on
whatever hardware CI provides; "does the planner have to scan the table once
per row" does not, and it is the exact property that broke.
"""

from __future__ import annotations

import json
import time
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.core.authorization.service import AuthorizationDataService
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
    _file_actions_expr,
)
from app.modules.datastore.infrastructure.repositories.file_visibility_sql import (
    has_unreadable_ancestor,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import allowed_actions_contains
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.tests.e2e.harness import DatastoreApi, signup_user
from sqlalchemy import and_, select

pytestmark = pytest.mark.e2e

#: Enough rows that an accidental O(N^2) plan is unmistakable, few enough that
#: seeding stays under a couple of seconds. The production pod that motivated
#: this work has 16,050.
_FILE_COUNT = 3_000
_MEMBERS = 25


async def _seed_large_tree(
    session, pod_id: UUID, owner_user_id: UUID, stranger_user_id: UUID
) -> None:
    """A wide, realistic tree written in bulk.

    Shaped like the pods that hurt: many members with personal roots (the main
    source of rows a given caller may not read), a shared subtree, and a couple
    of RESTRICTED folders whose descendants only the ancestor walk can hide.
    """
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, :owner, 'FOLDER', 'POD', '/', 'root', 0,
               false, 'READY', 0, now(), now()
        """),
        {"pod": pod_id, "owner": owner_user_id},
    )
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, :stranger, 'FOLDER', 'PERSONAL',
               '/u' || g, 'u' || g, 0, false, 'READY', 0, now(), now()
        FROM generate_series(1, :members) g
        """),
        {"pod": pod_id, "members": _MEMBERS, "stranger": stranger_user_id},
    )
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, f.owner_user_id, 'FILE', 'PERSONAL',
               f.path || '/n' || g || '.md', 'n' || g || '.md', 100,
               true, 'READY', 0, now(), now()
        FROM datastore_files f, generate_series(1, 20) g
        WHERE f.pod_id = :pod AND f.kind = 'FOLDER' AND f.path LIKE '/u%'
        """),
        {"pod": pod_id},
    )
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, :owner, 'FOLDER', 'POD',
               '/shared' || g, 'shared' || g, 0, false, 'READY', 0, now(), now()
        FROM generate_series(1, 20) g WHERE g % 10 <> 0
        """),
        {"pod": pod_id, "owner": owner_user_id},
    )
    # The RESTRICTED folders belong to someone else, so the caller cannot read
    # them by ownership and the ancestor walk is the only thing hiding what is
    # inside.
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, :stranger, 'FOLDER', 'RESTRICTED',
               '/shared' || g, 'shared' || g, 0, false, 'READY', 0, now(), now()
        FROM generate_series(1, 20) g WHERE g % 10 = 0
        """),
        {"pod": pod_id, "stranger": stranger_user_id},
    )
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, :owner, 'FILE', 'POD',
               f.path || '/d' || g || '.md', 'd' || g || '.md', 500,
               true, 'READY', 0, now(), now()
        FROM datastore_files f, generate_series(1, :per_folder) g
        WHERE f.pod_id = :pod AND f.kind = 'FOLDER' AND f.path LIKE '/shared%'
        """),
        {"pod": pod_id, "owner": owner_user_id, "per_folder": 125},
    )
    await session.execute(text("ANALYZE datastore_files"))


def _visibility_statement(ctx, pod_id: UUID, *, walk_ancestors: bool):
    """The exact statement ``visible_file_ids`` issues, both branches."""
    actions = _file_actions_expr(ctx)
    stmt = select(DatastoreFile.id).where(
        DatastoreFile.pod_id == pod_id,
        allowed_actions_contains(actions, Permissions.FOLDER_READ),
    )
    if walk_ancestors:
        stmt = stmt.where(~has_unreadable_ancestor(ctx, pod_id))
    return stmt


def _scan_nodes(plan: dict, out: list[str] | None = None) -> list[str]:
    out = [] if out is None else out
    node = plan.get("Node Type", "")
    if "Scan" in node:
        out.append(f"{node} on {plan.get('Relation Name', '?')}")
    for child in plan.get("Plans", []):
        _scan_nodes(child, out)
    return out


async def _explain(db_session, ctx, pod_id: UUID, *, walk_ancestors: bool) -> dict:
    """Run the real statement under EXPLAIN ANALYZE and return its plan."""
    compiled = _visibility_statement(
        ctx, pod_id, walk_ancestors=walk_ancestors
    ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    explained = await db_session.execute(
        text(f"EXPLAIN (FORMAT JSON, ANALYZE, BUFFERS) {compiled}")
    )
    plan_json = explained.scalar_one()
    if isinstance(plan_json, str):
        plan_json = json.loads(plan_json)
    return plan_json[0]


async def _repeated_inner_nodes(db_session, ctx, pod_id: UUID) -> list[str]:
    """Plan nodes the executor re-enters once per outer row.

    This is the discriminator, and it is not the obvious one. Buffer counts
    mislead badly here: the quadratic version reads *fewer* pages (662 against
    150,127 on a 16,000-file pod) because Postgres materializes the inner side
    once and then rescans it in memory — 208 million comparisons, 6.6 seconds,
    almost no I/O. The index-probing version touches far more buffers, all of
    them cached hits, and finishes in 0.14.

    What actually separates them is the *type* of node being re-entered. A
    per-row ``Materialize`` or ``Seq Scan`` of the file table means the whole
    table is being rescanned for every row; an ``Index Scan`` means each row
    probes for its own ancestors and nothing else.
    """
    result = await _explain(db_session, ctx, pod_id, walk_ancestors=True)

    repeated: list[str] = []

    def walk(node: dict) -> None:
        if node.get("Actual Loops", 1) > 1:
            relation = node.get("Relation Name", "")
            repeated.append(
                f"{node['Node Type']}{' on ' + relation if relation else ''}"
            )
        for child in node.get("Plans", []):
            walk(child)

    walk(result["Plan"])
    return repeated


async def test_visibility_stays_index_driven_on_a_large_pod(
    db_session, async_client, pod_api: DatastoreApi, fixed_test_user
) -> None:
    pod_id = UUID(pod_api.pod_id)
    user_id = UUID(fixed_test_user["id"])
    stranger = await signup_user(async_client, "visibility-scale")
    await _seed_large_tree(db_session, pod_id, user_id, UUID(stranger["id"]))

    total = await db_session.execute(
        text("SELECT count(*) FROM datastore_files WHERE pod_id = :pod"),
        {"pod": pod_id},
    )
    seeded = total.scalar_one()
    assert seeded >= _FILE_COUNT, (
        f"the fixture only seeded {seeded} files; below ~{_FILE_COUNT} an "
        "O(N^2) plan is fast enough to pass unnoticed"
    )

    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)

    repeated = await _repeated_inner_nodes(db_session, ctx, pod_id)
    print(f"\n  per-row plan nodes: {sorted(set(repeated))}")

    rescans = [
        node
        for node in repeated
        if node.startswith("Materialize") or node == "Seq Scan on datastore_files"
    ]
    assert not rescans, (
        "the ancestor check re-reads the whole file table once per row: "
        f"{sorted(set(rescans))}. That is the shape that took 6.6s on a "
        "16,000-file pod — ancestors must be probed through the (pod_id, path) "
        f"index instead.\n  all per-row nodes: {sorted(set(repeated))}"
    )
    assert any(node.startswith("Index Scan on datastore_files") for node in repeated), (
        "nothing in the plan probes the file table per row, so the ancestor "
        f"check is not resolving ancestors by path at all:\n  {sorted(set(repeated))}"
    )

    # A second, softer signal. Deliberately generous — this is a smoke bound
    # against a plan blowing up, not a latency budget.
    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
    started = time.perf_counter()
    visible = await repository.visible_file_ids(
        pod_id=pod_id, ctx=ctx, walk_ancestors=True
    )
    elapsed = time.perf_counter() - started
    print(f"\n  visibility over {seeded} files: {elapsed * 1000:.1f}ms")
    assert elapsed < 3.0, (
        f"visibility over {seeded} files took {elapsed:.2f}s; the shipped-then-"
        "reverted quadratic version took 6.6s over 16,000"
    )
    assert visible, "the caller sees nothing at all — the fixture proves nothing"


async def test_the_split_search_actually_uses_is_index_driven_too(
    db_session, async_client, pod_api: DatastoreApi, fixed_test_user
) -> None:
    """The projected form of the predicate needs its own proof.

    ``visible_file_ids`` applies the ancestor check as a filter;
    ``file_visibility_split`` — the one search calls — projects it as a boolean
    column instead, so the planner is free to choose a different shape for it.
    Asserting the filtered form is index-driven says nothing about the
    projected one, and the projected one is the hot path.
    """
    pod_id = UUID(pod_api.pod_id)
    user_id = UUID(fixed_test_user["id"])
    stranger = await signup_user(async_client, "visibility-scale-split")
    await _seed_large_tree(db_session, pod_id, user_id, UUID(stranger["id"]))

    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)

    actions = _file_actions_expr(ctx)
    visible_expr = and_(
        allowed_actions_contains(actions, Permissions.FOLDER_READ),
        ~has_unreadable_ancestor(ctx, pod_id),
    )
    compiled = (
        select(DatastoreFile.id, visible_expr.label("visible"))
        .where(DatastoreFile.pod_id == pod_id)
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    explained = await db_session.execute(
        text(f"EXPLAIN (FORMAT JSON, ANALYZE, BUFFERS) {compiled}")
    )
    plan_json = explained.scalar_one()
    if isinstance(plan_json, str):
        plan_json = json.loads(plan_json)

    repeated: list[str] = []

    def walk(node: dict) -> None:
        if node.get("Actual Loops", 1) > 1:
            relation = node.get("Relation Name", "")
            repeated.append(
                f"{node['Node Type']}{' on ' + relation if relation else ''}"
            )
        for child in node.get("Plans", []):
            walk(child)

    walk(plan_json[0]["Plan"])
    rescans = [
        node
        for node in repeated
        if node.startswith("Materialize") or node == "Seq Scan on datastore_files"
    ]
    assert not rescans, (
        "the projected form of the visibility predicate rescans the whole file "
        f"table per row: {sorted(set(rescans))}"
    )

    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
    started = time.perf_counter()
    visible, hidden = await repository.file_visibility_split(
        pod_id=pod_id, ctx=ctx, walk_ancestors=True
    )
    elapsed = time.perf_counter() - started
    print(f"\n  split over {len(visible) + len(hidden)} files: {elapsed * 1000:.1f}ms")
    assert elapsed < 3.0, f"the split took {elapsed:.2f}s"
    assert visible and hidden, (
        "the fixture produced no split at all, so this measures nothing"
    )


async def test_a_restricted_folder_still_hides_its_subtree_at_scale(
    db_session, async_client, pod_api: DatastoreApi, fixed_test_user
) -> None:
    """The fast plan must not have bought its speed by checking less.

    Counterweight to the plan assertion above: every rewrite in this area has
    been a trade between how much of the tree is consulted and how quickly, and
    the failure mode is always that fewer ancestors get looked at.
    """
    pod_id = UUID(pod_api.pod_id)
    user_id = UUID(fixed_test_user["id"])
    stranger = await signup_user(async_client, "visibility-scale-hide")
    await _seed_large_tree(db_session, pod_id, user_id, UUID(stranger["id"]))

    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
    visible = await repository.visible_file_ids(
        pod_id=pod_id, ctx=ctx, walk_ancestors=True
    )

    under_restricted = await db_session.execute(
        text("""
        SELECT id, path FROM datastore_files
        WHERE pod_id = :pod AND path LIKE '/shared10/%'
        """),
        {"pod": pod_id},
    )
    rows = under_restricted.all()
    assert rows, "the fixture built no descendants under the RESTRICTED folder"
    leaked = [path for file_id, path in rows if file_id in visible]
    assert not leaked, (
        f"{len(leaked)} POD files under a RESTRICTED folder the caller cannot "
        f"read were visible, e.g. {leaked[:3]}"
    )

    personal = await db_session.execute(
        text("""
        SELECT id FROM datastore_files
        WHERE pod_id = :pod AND path LIKE '/u1/%' LIMIT 5
        """),
        {"pod": pod_id},
    )
    others = [row[0] for row in personal.all()]
    assert others and not (set(others) & visible), (
        "another member's PERSONAL files were visible"
    )
