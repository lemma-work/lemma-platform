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
from sqlalchemy import select

pytestmark = pytest.mark.e2e

#: Enough rows that an accidental O(N^2) plan is unmistakable, few enough that
#: seeding stays under a couple of seconds. The real pod that motivated this work
#: holds several times as many.
_FILE_COUNT = 3_000
_MEMBERS = 25


async def _seed_large_tree(
    session, pod_id: UUID, owner_user_id: UUID, stranger_user_id: UUID
) -> None:
    """A wide, realistic tree written in bulk.

    Shaped like the pods that hurt: many members with personal roots (the main
    source of rows a given caller may not read), a shared subtree, and a couple
    of RESTRICTED folders whose descendants only the ancestor walk can hide.

    `status` is written as a value `FileStatus` actually has. It used to be
    'READY', which is not one — harmless while every test here read ids only,
    and an immediate `ValueError` for the first one that hydrated a row.
    """
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, :owner, 'FOLDER', 'POD', '/', 'root', 0,
               false, 'COMPLETED', 0, now(), now()
        """),
        {"pod": pod_id, "owner": owner_user_id},
    )
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), :pod, :stranger, 'FOLDER', 'PERSONAL',
               '/u' || g, 'u' || g, 0, false, 'COMPLETED', 0, now(), now()
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
               true, 'COMPLETED', 0, now(), now()
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
               '/shared' || g, 'shared' || g, 0, false, 'COMPLETED', 0, now(), now()
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
               '/shared' || g, 'shared' || g, 0, false, 'COMPLETED', 0, now(), now()
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
               true, 'COMPLETED', 0, now(), now()
        FROM datastore_files f, generate_series(1, :per_folder) g
        WHERE f.pod_id = :pod AND f.kind = 'FOLDER' AND f.path LIKE '/shared%'
        """),
        {"pod": pod_id, "owner": owner_user_id, "per_folder": 125},
    )
    await session.execute(text("ANALYZE datastore_files"))


def _visibility_statement(
    ctx,
    pod_id: UUID,
    *,
    walk_ancestors: bool,
    among: list[UUID] | None = None,
    limit: int | None = None,
):
    """The exact statement ``visible_file_ids`` issues, every branch."""
    actions = _file_actions_expr(ctx)
    stmt = select(DatastoreFile.id).where(
        DatastoreFile.pod_id == pod_id,
        allowed_actions_contains(actions, Permissions.FOLDER_READ),
    )
    if among is not None:
        stmt = stmt.where(DatastoreFile.id.in_(among))
    if walk_ancestors:
        stmt = stmt.where(~has_unreadable_ancestor(ctx, pod_id))
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt


def _scan_nodes(plan: dict, out: list[str] | None = None) -> list[str]:
    out = [] if out is None else out
    node = plan.get("Node Type", "")
    if "Scan" in node:
        out.append(f"{node} on {plan.get('Relation Name', '?')}")
    for child in plan.get("Plans", []):
        _scan_nodes(child, out)
    return out


async def _explain(db_session, ctx, pod_id: UUID, **kwargs) -> dict:
    """Run the real statement under EXPLAIN ANALYZE and return its plan."""
    compiled = _visibility_statement(ctx, pod_id, **kwargs).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
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


def _rows_read(plan: dict) -> int:
    """Rows the executor actually pulled out of the file table.

    ``Actual Rows`` on a scan node is per loop, so it is multiplied by
    ``Actual Loops`` before being counted. This is the number the two bounded
    forms exist to hold down, and the only one that separates "stopped early"
    from "walked the pod and then discarded most of it".
    """
    total = 0
    if plan.get("Relation Name") == "datastore_files" and "Scan" in plan.get(
        "Node Type", ""
    ):
        total += int(plan.get("Actual Rows", 0)) * int(plan.get("Actual Loops", 1))
    for child in plan.get("Plans", []):
        total += _rows_read(child)
    return total


async def test_the_readable_set_probe_stops_at_its_ceiling(
    db_session, async_client, pod_api: DatastoreApi, fixed_test_user
) -> None:
    """Search asks "is the readable set small enough to send?", not "what is it?".

    This is the whole of the fix. Search used to read every file row in the pod
    on every query, to build an id array it then sent to the other database. It
    now asks for one id more than it is willing to send: a short answer is the
    complete readable set, a full one only means "more than the ceiling", and
    either way the statement stops there.

    The assertion is on rows pulled from the table, because that is what a
    ``LIMIT`` in the wrong place still gets wrong -- a plan that materialises
    the pod and then truncates returns the same ids and costs the same as
    before.
    """
    pod_id = UUID(pod_api.pod_id)
    user_id = UUID(fixed_test_user["id"])
    stranger = await signup_user(async_client, "visibility-scale-probe")
    await _seed_large_tree(db_session, pod_id, user_id, UUID(stranger["id"]))

    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)

    ceiling = 50
    plan = await _explain(
        db_session, ctx, pod_id, walk_ancestors=True, limit=ceiling + 1
    )
    rows = _rows_read(plan["Plan"])
    print(f"\n  probe with ceiling {ceiling} read {rows} rows")

    unbounded = await _explain(db_session, ctx, pod_id, walk_ancestors=True)
    unbounded_rows = _rows_read(unbounded["Plan"])
    assert unbounded_rows > 10 * rows, (
        f"the bounded probe read {rows} rows and the unbounded form read "
        f"{unbounded_rows}; they are close enough that the LIMIT is not "
        "stopping the scan, which is the only thing it is there for"
    )

    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
    readable = await repository.visible_file_ids(
        pod_id=pod_id, ctx=ctx, walk_ancestors=True, limit=ceiling + 1
    )
    assert len(readable) == ceiling + 1, (
        "this caller can read fewer files than the ceiling, so the fixture "
        "never exercises the branch that stops early"
    )


async def test_authorizing_a_candidate_pool_does_not_touch_the_rest_of_the_pod(
    db_session, async_client, pod_api: DatastoreApi, fixed_test_user
) -> None:
    """The other branch: authorize the rows that came back, and only those.

    When the readable set is too large to send, the chunk query runs unnarrowed
    and its results are authorized afterwards. That read is only bounded if the
    ``id IN (...)`` reaches the primary key -- an ``= ANY`` that the planner
    resolves by scanning the pod and filtering would leave the search costing
    exactly what it cost before, while looking fixed.
    """
    pod_id = UUID(pod_api.pod_id)
    user_id = UUID(fixed_test_user["id"])
    stranger = await signup_user(async_client, "visibility-scale-among")
    await _seed_large_tree(db_session, pod_id, user_id, UUID(stranger["id"]))

    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)

    candidates = list(
        (
            await db_session.execute(
                text("SELECT id FROM datastore_files WHERE pod_id = :pod LIMIT 40"),
                {"pod": pod_id},
            )
        ).scalars()
    )
    plan = await _explain(
        db_session, ctx, pod_id, walk_ancestors=True, among=candidates
    )
    outer = plan["Plan"]
    rows = _rows_read(outer)
    print(f"\n  authorizing {len(candidates)} candidates read {rows} rows")

    # Generous on purpose: the ancestor check probes the file table once per
    # surviving row, so the count is a small multiple of the pool and not the
    # pool itself. What it must not be is a number that grows with the pod.
    assert rows < 20 * len(candidates), (
        f"authorizing {len(candidates)} candidate rows read {rows} rows from "
        "the file table; the pool is supposed to be the bound"
    )
    unbounded = _rows_read(
        (await _explain(db_session, ctx, pod_id, walk_ancestors=True))["Plan"]
    )
    assert unbounded > 10 * max(rows, 1), (
        f"the narrowed form read {rows} rows and the same statement without "
        f"the id list read {unbounded}; they are close enough that the "
        "`id IN (...)` is not reaching the index"
    )

    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
    readable = await repository.visible_file_ids(
        pod_id=pod_id, ctx=ctx, walk_ancestors=True, among=candidates
    )
    assert readable and readable <= set(candidates), (
        "the narrowed form answered about files nobody asked about"
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


async def test_the_tree_reads_what_it_displays_not_the_whole_pod(
    db_session, async_client, pod_api: DatastoreApi, fixed_test_user
) -> None:
    """The directory tree must not scale with the number of files in the pod.

    It shows every folder but caps files at `files_per_directory` in each one,
    and it used to reach that shape by loading the pod twice over: every row
    hydrated into an entity, then every visible id fetched again, to render a
    few files per folder. Correct, and O(files) for an answer that is
    O(folders x files_per_directory).

    Asserted on what comes back rather than on the clock, for the same reason
    as the plan assertions above: row counts are a property of the query, and
    wall time is a property of whatever hardware CI provides.
    """
    pod_id = UUID(pod_api.pod_id)
    user_id = UUID(fixed_test_user["id"])
    stranger = await signup_user(async_client, "tree-scale")
    await _seed_large_tree(db_session, pod_id, user_id, UUID(stranger["id"]))

    counts = await db_session.execute(
        text("""
        SELECT kind, count(*) FROM datastore_files
        WHERE pod_id = :pod GROUP BY kind
        """),
        {"pod": pod_id},
    )
    by_kind = dict(counts.all())
    files, folders = by_kind.get("FILE", 0), by_kind.get("FOLDER", 0)
    assert files >= _FILE_COUNT, (
        f"the fixture only seeded {files} files; below ~{_FILE_COUNT} reading "
        "the whole pod is cheap enough to pass unnoticed"
    )

    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))

    per_directory = 3
    items = await repository.get_tree_items(
        pod_id,
        ctx=ctx,
        subtree_root="/",
        files_per_directory=per_directory,
        walk_ancestors=True,
    )

    returned_files = [item for item in items if item.is_file]
    print(
        f"\n  pod has {files} files in {folders} folders; "
        f"the tree read {len(returned_files)}"
    )

    # The bound that matters: one extra row per directory beyond what is shown,
    # which is how `has_more_files` is still answerable.
    ceiling = folders * (per_directory + 1)
    assert len(returned_files) <= ceiling, (
        f"the tree read {len(returned_files)} files for a view that can show at "
        f"most {folders} x {per_directory}; it is still scaling with the pod "
        f"({files} files) rather than with what it displays"
    )
    assert len(returned_files) < files, (
        "the tree read every file in the pod — the per-directory cap is not "
        "reaching the database at all"
    )

    # And no directory came back short: the cap has to be applied after
    # visibility, or a folder whose first entries the caller cannot read would
    # quietly show fewer files than it has.
    by_parent: dict[str, int] = {}
    for item in returned_files:
        by_parent[item.path.rsplit("/", 1)[0]] = (
            by_parent.get(item.path.rsplit("/", 1)[0], 0) + 1
        )
    assert by_parent, "the tree returned no files at all"
    assert max(by_parent.values()) <= per_directory + 1, (
        f"a directory came back with {max(by_parent.values())} files, more than "
        f"the {per_directory} shown plus the one that signals there are more"
    )
