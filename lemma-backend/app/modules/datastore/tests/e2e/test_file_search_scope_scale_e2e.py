"""A delegated workload with folder grants, searching one pod of a large platform.

The shape that took file search down: a function holding a handful of folder
grants searched a pod of a few thousand files, and the visibility statement
joined every (row, grant, action) to a sequential scan of the *whole* file
table -- every pod's files -- inside an OR the planner could not index. Its cost
grew with the platform, not the pod, so nothing about the pod had to change for
it to get worse.

The fixture is that shape at platform scale: tens of thousands of files across
many pods, the target pod a few thousand, the workload holding five read
grants and one write grant on folders. The assertions are on the plan -- no
sequential scan of the file table, nothing re-entered per row, rows read
bounded by the pod -- with a deliberately generous clock bound beside them.
"""

from __future__ import annotations

import json
import time
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import and_, exists, false, func, or_, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import aliased

from app.core.authorization import sql_actions
from app.core.authorization.context import (
    ActorType,
    Context,
    PrincipalRef,
    ResourceType,
)
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import (
    Permissions,
    equivalent_permission_ids,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.composition import get_datastore_composition
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.search_scope import SearchScopeTimedOut
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_read_rule import (
    blocked_folders_statement,
    grant_groups,
    grant_reach_statement,
    resolve_blocked_folders,
    resolve_read_rule,
    searchable_files_statement,
)
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.searcher import FileSearcher
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e

_OTHER_PODS = 60
_ROWS_PER_OTHER_POD = 900
_PLATFORM_FLOOR = 50_000

#: How long the scope may take, generously: a smoke bound against a plan that
#: has blown up, not a latency budget. The plan assertions are the real check.
_SCOPE_BUDGET_S = 1.0
#: How long the original statement is given to prove it is still slow here.
_LEGACY_BUDGET_MS = 3_000

_READ_GRANTS = ["/t1", "/t2/s3", "/t4", "/t5/s1", "/t9"]
_WRITE_GRANTS = ["/t10"]


async def _seed_platform(session, pod_id: UUID, owner: UUID, stranger: UUID) -> None:
    # Fabricated pods, owners and grantees: foreign keys are not under test,
    # and `truncate_all` empties these tables before the next test.
    await session.execute(text("SET LOCAL session_replication_role = 'replica'"))
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), pods.pid, :owner,
               CASE WHEN g % 10 = 0 THEN 'FOLDER' ELSE 'FILE' END,
               (ARRAY['POD','POD','RESTRICTED','PERSONAL'])[1 + g % 4],
               '/f' || (g / 10) || CASE WHEN g % 10 = 0 THEN '' ELSE '/d' || g || '.md' END,
               'n' || g, 1, true,
               CASE WHEN g % 10 = 0 THEN 'NOT_REQUIRED' ELSE 'COMPLETED' END,
               0, now(), now()
        FROM (SELECT gen_random_uuid() AS pid FROM generate_series(1, :pods)) pods,
             generate_series(0, :per_pod - 1) g
        """),
        {"owner": owner, "pods": _OTHER_PODS, "per_pod": _ROWS_PER_OTHER_POD},
    )
    # The target pod: thirty top folders (every seventh RESTRICTED and someone
    # else's), ten subfolders each, nine documents per subfolder, and ten
    # members' personal roots.
    await session.execute(
        text("""
        INSERT INTO datastore_files
          (id, pod_id, owner_user_id, kind, visibility, path, name, size_bytes,
           search_enabled, status, processing_attempts, created_at, updated_at)
        SELECT gen_random_uuid(), CAST(:pod AS uuid),
               CASE WHEN i % 7 = 0 THEN CAST(:stranger AS uuid)
                    ELSE CAST(:owner AS uuid) END, 'FOLDER',
               CASE WHEN i % 7 = 0 THEN 'RESTRICTED' ELSE 'POD' END,
               '/t' || i, 't' || i, 0, false, 'NOT_REQUIRED', 0, now(), now()
        FROM generate_series(1, 30) i
        UNION ALL
        SELECT gen_random_uuid(), CAST(:pod AS uuid), CAST(:owner AS uuid), 'FOLDER', 'POD',
               '/t' || i || '/s' || j, 's' || j, 0, false, 'NOT_REQUIRED', 0,
               now(), now()
        FROM generate_series(1, 30) i, generate_series(1, 10) j
        UNION ALL
        SELECT gen_random_uuid(), CAST(:pod AS uuid), CAST(:owner AS uuid), 'FILE', 'POD',
               '/t' || i || '/s' || j || '/d' || k || '.md', 'd' || k || '.md',
               100, true, 'COMPLETED', 0, now(), now()
        FROM generate_series(1, 30) i, generate_series(1, 10) j,
             generate_series(1, 9) k
        UNION ALL
        SELECT gen_random_uuid(), CAST(:pod AS uuid), CAST(:stranger AS uuid), 'FOLDER', 'PERSONAL',
               '/u' || m, 'u' || m, 0, false, 'NOT_REQUIRED', 0, now(), now()
        FROM generate_series(1, 10) m
        UNION ALL
        SELECT gen_random_uuid(), CAST(:pod AS uuid), CAST(:stranger AS uuid), 'FILE', 'PERSONAL',
               '/u' || m || '/p' || k || '.md', 'p' || k || '.md', 100, true,
               'COMPLETED', 0, now(), now()
        FROM generate_series(1, 10) m, generate_series(1, 10) k
        """),
        {"pod": pod_id, "owner": owner, "stranger": stranger},
    )
    # Noise: grants elsewhere on the platform, and to others in this pod.
    await session.execute(
        text("""
        INSERT INTO resource_permission_grants
          (id, pod_id, resource_type, resource_id, grantee_type, grantee_id,
           permission_id, created_at)
        SELECT gen_random_uuid(), f.pod_id, 'folder', f.id, 'AGENT',
               gen_random_uuid(), 'folder.read', now()
        FROM datastore_files f
        WHERE f.kind = 'FOLDER' AND f.pod_id <> :pod
        """),
        {"pod": pod_id},
    )
    # From the model, as the migration creates it in production: the blocked
    # folders read below is about using it.
    present = await session.execute(
        text(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'datastore_files' "
            "AND indexname = 'ix_datastore_file_pod_folder_visibility'"
        )
    )
    assert present.scalar_one_or_none(), "the folder-visibility index is missing"


async def _grant(session, pod_id: UUID, grantee: PrincipalRef) -> None:
    for paths, permission in (
        (_READ_GRANTS, Permissions.FOLDER_READ),
        (_WRITE_GRANTS, Permissions.FOLDER_WRITE),
    ):
        await session.execute(
            text("""
            INSERT INTO resource_permission_grants
              (id, pod_id, resource_type, resource_id, grantee_type, grantee_id,
               permission_id, created_at)
            SELECT gen_random_uuid(), :pod, 'folder', f.id, :type, :id,
                   :permission, now()
            FROM datastore_files f WHERE f.pod_id = :pod AND f.path = ANY(:paths)
            """),
            {
                "pod": pod_id,
                "type": grantee.type,
                "id": grantee.id,
                "permission": permission,
                "paths": paths,
            },
        )
    await session.execute(text("ANALYZE datastore_files"))
    await session.execute(text("ANALYZE resource_permission_grants"))


@pytest.fixture
async def platform(db_session, pod_api: DatastoreApi) -> dict:
    pod_id = UUID(pod_api.pod_id)
    owner, stranger = uuid4(), uuid4()
    function = PrincipalRef("FUNCTION", uuid4())
    await _seed_platform(db_session, pod_id, owner, stranger)
    await _grant(db_session, pod_id, function)

    counts = await db_session.execute(
        text("""
        SELECT count(*), count(*) FILTER (WHERE pod_id = :pod)
        FROM datastore_files
        """),
        {"pod": pod_id},
    )
    platform_rows, pod_rows = counts.one()
    assert platform_rows >= _PLATFORM_FLOOR, (
        f"only {platform_rows} files on the platform; the defect scaled with "
        "the platform, so a small table hides it"
    )
    assert 2_000 <= pod_rows <= 5_000, pod_rows

    workload = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"function:{function.id}",
        authorizer=None,
        user_id=owner,
        pod_id=pod_id,
        permission_ids=frozenset({Permissions.FOLDER_READ}),
        principal_refs=frozenset({function}),
        workload_principal_refs=frozenset({function}),
    )
    member = Context(
        actor_type=ActorType.USER,
        actor_id="member",
        authorizer=None,
        user_id=uuid4(),
        pod_id=pod_id,
        permission_ids=frozenset({Permissions.FOLDER_READ}),
        principal_refs=frozenset({PrincipalRef("ROLE", uuid4())}),
    )
    return {
        "pod_id": pod_id,
        "owner": owner,
        "pod_rows": pod_rows,
        "platform_rows": platform_rows,
        "workload": workload,
        "member": member,
    }


def _legacy_grant_exists(*, principal_group: frozenset[PrincipalRef], action: str):
    """The cascade before it was split: one EXISTS, an OR, an unconstrained alias."""
    if not principal_group:
        return false()
    grant = ResourcePermissionGrantModel
    granted = aliased(DatastoreFile)
    return exists().where(
        grant.pod_id == DatastoreFile.pod_id,
        grant.resource_type.in_(grant_resource_type_values(ResourceType.DOCUMENT)),
        grant.permission_id.in_(equivalent_permission_ids(action)),
        or_(
            *(
                and_(grant.grantee_type == p.type, grant.grantee_id == p.id)
                for p in principal_group
            )
        ),
        or_(
            grant.resource_id == DatastoreFile.pod_id,
            and_(
                granted.id == grant.resource_id,
                granted.pod_id == DatastoreFile.pod_id,
                or_(
                    DatastoreFile.path == granted.path,
                    func.left(DatastoreFile.path, func.length(granted.path) + 1)
                    == granted.path.concat("/"),
                ),
            ),
        ),
    )


def _legacy_workload_statement(ctx: Context, pod_id: UUID):
    """The statement that timed out: a delegated workload's read filter as it was.

    Every action's grant EXISTS built into the array, then tested for read --
    the shape ``_delegated_allowed_actions_expr`` produced over the unsplit
    cascade, minus the PERSONAL guard, which cannot make it cheaper.
    """
    actions = sql_actions._conditional_actions_array_expr(
        [
            (
                action,
                _legacy_grant_exists(
                    principal_group=ctx.workload_principal_refs, action=action
                ),
            )
            for action in (
                Permissions.FOLDER_READ,
                Permissions.FOLDER_WRITE,
                Permissions.FOLDER_DELETE,
            )
        ]
    )
    return select(DatastoreFile.id).where(
        DatastoreFile.pod_id == pod_id,
        sql_actions.allowed_actions_contains(actions, Permissions.FOLDER_READ),
    )


def _per_row_statement(ctx: Context, pod_id: UUID):
    """What the search scope ran before: the per-row CASE, every action."""
    actions = sql_actions.allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DOCUMENT,
        resource_id_col=DatastoreFile.id,
        pod_id_col=DatastoreFile.pod_id,
        owner_user_id_col=DatastoreFile.owner_user_id,
        visibility_col=DatastoreFile.visibility,
        resource_path_col=DatastoreFile.path,
    )
    return select(DatastoreFile.id).where(
        DatastoreFile.pod_id == pod_id,
        sql_actions.allowed_actions_contains(actions, Permissions.FOLDER_READ),
    )


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


async def _explain(session, statement) -> dict:
    result = await session.execute(
        text(f"EXPLAIN (FORMAT JSON, ANALYZE, BUFFERS) {_sql(statement)}")
    )
    plan = result.scalar_one()
    return (json.loads(plan) if isinstance(plan, str) else plan)[0]


def _nodes(plan: dict) -> list[dict]:
    found = [plan]
    for child in plan.get("Plans", []):
        found.extend(_nodes(child))
    return found


def _file_table_scans(plan: dict) -> list[str]:
    """The plan's reads of the file table, by node, index and loop count."""
    return [
        f"{node['Node Type']} via {node.get('Index Name', '-')} "
        f"loops={node.get('Actual Loops')} rows={node.get('Actual Rows')}"
        for node in _nodes(plan["Plan"])
        if node.get("Relation Name") == "datastore_files"
        or str(node.get("Index Name", "")).startswith(
            ("ix_datastore_file", "datastore_files_")
        )
    ]


def _assert_index_driven(plan: dict, *, label: str, pod_rows: int) -> None:
    nodes = [
        n for n in _nodes(plan["Plan"]) if n.get("Relation Name") == "datastore_files"
    ]
    scans = _file_table_scans(plan)
    assert not [n for n in nodes if n["Node Type"] == "Seq Scan"], (
        f"{label}: a sequential scan of the file table -- every pod's files:\n  "
        + "\n  ".join(scans)
    )
    rows_read = sum(
        int(n.get("Actual Rows", 0)) * int(n.get("Actual Loops", 1)) for n in nodes
    )
    assert rows_read <= 2 * pod_rows, (
        f"{label}: read {rows_read} file rows for a pod of {pod_rows}:\n  "
        + "\n  ".join(scans)
    )


async def _timed(coro) -> tuple[object, float]:
    started = time.perf_counter()
    value = await coro
    return value, time.perf_counter() - started


async def _legacy_ms(session, ctx: Context, pod_id: UUID) -> str:
    """Run the original statement under a cap; report how it ended."""
    statement = _legacy_workload_statement(ctx, pod_id)
    savepoint = await session.begin_nested()
    started = time.perf_counter()
    try:
        await session.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": str(_LEGACY_BUDGET_MS)},
        )
        await session.execute(statement)
        return f"{(time.perf_counter() - started) * 1000:.0f}ms"
    except Exception as exc:  # noqa: BLE001 -- the cancel is the expected outcome
        if getattr(getattr(exc, "orig", None), "sqlstate", None) != "57014":
            raise
        return f"cancelled at {_LEGACY_BUDGET_MS}ms"
    finally:
        await savepoint.rollback()


def _authorizer(session) -> FileAuthorizer:
    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(session))
    return FileAuthorizer(DatastoreAuthorization(object()), repository, PathResolver())


async def test_a_delegated_workload_scope_is_index_driven_at_platform_scale(
    db_session, platform
) -> None:
    ctx, pod_id = platform["workload"], platform["pod_id"]

    legacy = await _legacy_ms(db_session, ctx, pod_id)

    authorizer = _authorizer(db_session)
    scope, elapsed = await _timed(authorizer.search_file_scope(pod_id=pod_id, ctx=ctx))
    print(
        f"\n  platform {platform['platform_rows']} files, pod {platform['pod_rows']}:"
        f"\n  before (per-row CASE, unsplit cascade): {legacy}"
        f"\n  after (resolved scope, grants read + split): {elapsed * 1000:.1f}ms"
        f" -> {'readable' if scope.enumerated else 'hidden' if scope.excluding else 'unnarrowed'}"
        f" side, {len(scope.file_ids)} ids"
    )
    assert elapsed < _SCOPE_BUDGET_S, f"the scope took {elapsed:.2f}s"
    assert scope.enumerated and scope.file_ids, (
        "five folder grants reach a few hundred of the pod's files; the readable "
        f"side is the shorter and should have been sent, got {scope}"
    )

    # The plan of every statement the scope issued.
    reaches_plan = await _explain(
        db_session, grant_reach_statement(pod_id, frozenset().union(*grant_groups(ctx)))
    )
    rule = await resolve_read_rule(db_session, pod_id=pod_id, ctx=ctx)
    scope_plan = await _explain(
        db_session, searchable_files_statement(pod_id, rule.readable())
    )
    print(f"  grants read: {_file_table_scans(reaches_plan)}")
    print(f"  scope statement: {_file_table_scans(scope_plan)}")
    _assert_index_driven(
        reaches_plan, label="grants read", pod_rows=platform["pod_rows"]
    )
    _assert_index_driven(scope_plan, label="scope", pod_rows=platform["pod_rows"])
    assert not [
        n
        for n in _nodes(scope_plan["Plan"])
        if n.get("Relation Name") and int(n.get("Actual Loops", 1)) > 1
    ], "the scope statement re-enters a table per row; nothing in it should"

    # Listings still project actions row by row through the split cascade;
    # it must not have the old shape either.
    current_plan = await _explain(db_session, _per_row_statement(ctx, pod_id))
    current_ms = current_plan["Execution Time"]
    print(f"  per-row CASE with the split cascade: {current_ms:.1f}ms")
    print(f"    {sorted(set(_file_table_scans(current_plan)))[:6]}")
    assert not [
        n
        for n in _nodes(current_plan["Plan"])
        if n.get("Relation Name") == "datastore_files" and n["Node Type"] == "Seq Scan"
    ], "the split cascade still scans the whole file table"


async def test_a_member_scope_resolves_blocked_folders_by_index(
    db_session, platform
) -> None:
    ctx, pod_id = platform["member"], platform["pod_id"]
    authorizer = _authorizer(db_session)
    scope, elapsed = await _timed(authorizer.search_file_scope(pod_id=pod_id, ctx=ctx))
    print(
        f"\n  member scope: {elapsed * 1000:.1f}ms -> "
        f"{'readable' if scope.enumerated else 'hidden' if scope.excluding else 'unnarrowed'}"
        f" side, {len(scope.file_ids)} ids"
    )
    assert elapsed < _SCOPE_BUDGET_S, f"the scope took {elapsed:.2f}s"
    assert scope.excluding and scope.file_ids, (
        "a member reads all but the restricted subtrees and other people's "
        f"personal files; the hidden side should have been sent, got {scope}"
    )

    rule = await resolve_read_rule(db_session, pod_id=pod_id, ctx=ctx)
    blocked_plan = await _explain(db_session, blocked_folders_statement(pod_id, rule))
    print(f"  blocked folders: {_file_table_scans(blocked_plan)}")
    _assert_index_driven(
        blocked_plan, label="blocked folders", pod_rows=platform["pod_rows"]
    )

    blocked = await resolve_blocked_folders(db_session, pod_id=pod_id, rule=rule)
    readable = rule.readable()
    if not blocked.empty:
        readable = and_(readable, ~blocked.hides(DatastoreFile.path))
    scope_plan = await _explain(
        db_session, searchable_files_statement(pod_id, readable)
    )
    print(f"  scope statement: {_file_table_scans(scope_plan)}")
    _assert_index_driven(scope_plan, label="scope", pod_rows=platform["pod_rows"])


_NEEDLE = "quillwort"


async def _index(pod_id: UUID, file_id: UUID, path: str) -> None:
    service = get_datastore_composition().build_search_service(pod_id)
    await service.index_file_chunks(
        file_id,
        [{"text": f"{_NEEDLE} {path}"}],
        {"path": path, "parent_path": path.rsplit("/", 1)[0] or "/"},
    )


async def test_a_scope_that_runs_out_of_time_post_filters_instead_of_failing(
    db_session, platform, monkeypatch
) -> None:
    """A permission query past its timeout degrades search; it never fails it.

    The workload here holds a grant on every document except those under
    ``/t3`` -- thousands of granted paths, which makes the scope slow to
    evaluate per row and so certain to overrun a one-millisecond timeout.
    """
    pod_id, owner = platform["pod_id"], platform["owner"]
    function = PrincipalRef("FUNCTION", uuid4())
    await db_session.execute(
        text("""
        INSERT INTO resource_permission_grants
          (id, pod_id, resource_type, resource_id, grantee_type, grantee_id,
           permission_id, created_at)
        SELECT gen_random_uuid(), :pod, 'document', f.id, :type, :id,
               'folder.read', now()
        FROM datastore_files f
        WHERE f.pod_id = :pod AND f.kind = 'FILE' AND f.path NOT LIKE '/t3/%'
        """),
        {"pod": pod_id, "type": function.type, "id": function.id},
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"function:{function.id}",
        authorizer=None,
        user_id=owner,
        pod_id=pod_id,
        principal_refs=frozenset({function}),
        workload_principal_refs=frozenset({function}),
    )
    paths = {"/t1/s1/d1.md", "/t3/s1/d1.md"}
    ids = dict(
        (
            await db_session.execute(
                text(
                    "SELECT path, id FROM datastore_files "
                    "WHERE pod_id = :pod AND path = ANY(:paths)"
                ),
                {"pod": pod_id, "paths": sorted(paths)},
            )
        ).all()
    )
    for path, file_id in ids.items():
        await _index(pod_id, file_id, path)
    await _index(pod_id, uuid7(), "/t1/s1/ghost.md")  # a chunk whose row is gone

    monkeypatch.setattr(datastore_settings, "datastore_search_scope_timeout_ms", 1)
    before = (await db_session.execute(text("SHOW statement_timeout"))).scalar_one()
    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
    with pytest.raises(SearchScopeTimedOut):
        await repository.searchable_partition(
            pod_id=pod_id, ctx=ctx, walk_ancestors=False, timeout_ms=1
        )
    after = (await db_session.execute(text("SHOW statement_timeout"))).scalar_one()
    assert after == before, (
        f"the scope's timeout leaked into the transaction: {before!r} -> {after!r}"
    )

    authorizer = FileAuthorizer(
        DatastoreAuthorization(object()), repository, PathResolver()
    )
    scope = await authorizer.search_file_scope(pod_id=pod_id, ctx=ctx)
    assert not scope.enumerated and not scope.narrowed, (
        f"the scope query was cancelled, so nothing could be narrowed: {scope}"
    )

    searcher = FileSearcher(
        lambda: get_datastore_composition().build_search_service,
        DatastoreAuthorization(object()),
        authorizer,
        PathResolver(),
        lookup=None,
    )
    results = await searcher.search_files(
        pod_id, owner, _NEEDLE, limit=10, search_method="TEXT", ctx=ctx
    )
    assert {result.file_id for result in results} == {ids["/t1/s1/d1.md"]}, (
        "after the fallback, search must return exactly the readable match: "
        f"got {sorted(result.path for result in results)}"
    )
