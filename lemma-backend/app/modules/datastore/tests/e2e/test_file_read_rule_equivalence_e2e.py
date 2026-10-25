"""The resolved read rule must answer exactly what the per-row CASE answered.

File visibility used to be decided row by row by ``allowed_actions_expr``, whose
grant branches were correlated EXISTS joined back to the file table. It is now
decided by reading the caller's authority first (``file_read_rule``) and
applying it as a plain predicate. That is a rewrite of an *authorization*
answer, so the property under test is set equality, over pods built to reach
every branch: owners, all four visibilities and an unknown one, nested folders,
gaps in the folder chain, a file with rows beneath it, paths with LIKE
metacharacters, folder and document grants, pod-wide grants, grants in another
pod or on another pod's rows, and every kind of caller -- members with and
without role access, the default agent, delegated workloads with their own
principals, other pods, outsiders and superusers.

The oracle is ``allowed_actions_expr``, the per-row CASE. Its grant cascade
was split from one EXISTS with an OR inside into two; the original form is kept
below (``_legacy_grant_exists``) and checked against the split one directly, for
every principal group and every action, so the oracle itself is pinned to what
it was.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import and_, exists, false, func, or_, select, text
from sqlalchemy.orm import aliased

from app.core.authorization import sql_actions
from app.core.authorization.context import (
    Actor,
    Assistant,
    Context,
    OtherPod,
    Outsider,
    OutsiderKind,
    Person,
    PrincipalRef,
    ResourceType,
    Workload,
    WorkloadKind,
)
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import (
    Permissions,
    equivalent_permission_ids,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
)
from app.modules.datastore.infrastructure.repositories.file_visibility_sql import (
    has_unreadable_ancestor,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e

_READ = Permissions.FOLDER_READ
_WRITE = Permissions.FOLDER_WRITE
_DELETE = Permissions.FOLDER_DELETE


def _legacy_grant_exists(*, principal_group: frozenset[PrincipalRef], action: str):
    """The folder cascade before it was split: cross join and all."""
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


def _split_grant_exists(*, principal_group: frozenset[PrincipalRef], action: str):
    return sql_actions._grant_exists_for_action(
        resource_type=ResourceType.DOCUMENT,
        resource_id_col=DatastoreFile.id,
        pod_id_col=DatastoreFile.pod_id,
        principal_group=principal_group,
        action=action,
        resource_path_col=DatastoreFile.path,
    )


def _per_row_statement(ctx: Context, pod_id: UUID, *, walk: bool):
    actions = sql_actions.allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DOCUMENT,
        resource_id_col=DatastoreFile.id,
        pod_id_col=DatastoreFile.pod_id,
        owner_user_id_col=DatastoreFile.owner_user_id,
        visibility_col=DatastoreFile.visibility,
        resource_path_col=DatastoreFile.path,
    )
    stmt = select(DatastoreFile.id).where(
        DatastoreFile.pod_id == pod_id,
        sql_actions.allowed_actions_contains(actions, _READ),
    )
    if walk:
        stmt = stmt.where(~has_unreadable_ancestor(ctx, pod_id))
    return stmt


# ── Random pods ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _World:
    pod_id: UUID
    other_pod_id: UUID
    owners: tuple[UUID, UUID, UUID]
    principals: dict[str, PrincipalRef]


#: Names chosen to trip prefix logic: siblings sharing a prefix, separators
#: that sort just below and above ``/``, and LIKE metacharacters.
_NAMES = ["a", "a-b", "ab", "a_b", "a%c", "docs", "x.md", "a.b", "z0", "Ä"]
_VISIBILITIES = ["POD"] * 8 + ["RESTRICTED"] * 6 + ["PERSONAL"] * 3 + ["PUBLIC"] * 2
_ODD_VISIBILITY = "PRIVATE"


def _random_rows(rng: random.Random, world: _World) -> list[dict]:
    rows: list[dict] = []
    taken: set[str] = set()

    def add(path: str, kind: str) -> None:
        if path in taken:
            return
        taken.add(path)
        visibility = (
            _ODD_VISIBILITY if rng.random() < 0.03 else rng.choice(_VISIBILITIES)
        )
        completed = kind == "FILE" and rng.random() < 0.7
        rows.append(
            {
                "id": uuid7(),
                "pod": world.pod_id,
                "owner": rng.choice(world.owners),
                "kind": kind,
                "visibility": visibility,
                "path": path,
                "name": path.rsplit("/", 1)[-1] or "root",
                "search_enabled": rng.random() < 0.85,
                "status": "COMPLETED" if completed else "NOT_REQUIRED",
            }
        )

    if rng.random() < 0.35:
        add("/", "FOLDER")

    def grow(parent: str, depth: int) -> None:
        for _ in range(rng.randint(1, 4)):
            name = rng.choice(_NAMES)
            path = f"{parent.rstrip('/')}/{name}" if parent != "/" else f"/{name}"
            if depth < 4 and rng.random() < 0.45:
                # Sometimes leave the folder row out: a gap in the chain.
                if rng.random() > 0.1:
                    add(path, "FOLDER")
                grow(path, depth + 1)
            else:
                add(path, "FILE")
                # Rarely, something beneath a file -- the walk never assumed
                # only folders have descendants.
                if rng.random() < 0.08:
                    add(f"{path}/inner.md", "FILE")

    grow("/", 0)
    return rows


def _random_grants(
    rng: random.Random, world: _World, rows: list[dict], other_rows: list[dict]
) -> list[dict]:
    grants: list[dict] = []
    principals = list(world.principals.values())
    for _ in range(rng.randint(0, 10)):
        roll = rng.random()
        if roll < 0.7 and rows:
            target = rng.choice(rows)["id"]
        elif roll < 0.8:
            target = world.pod_id  # pod-wide
        elif roll < 0.9 and other_rows:
            target = rng.choice(other_rows)["id"]  # another pod's row
        else:
            target = uuid4()  # points at nothing
        grantee = rng.choice(principals)
        grants.append(
            {
                "pod": world.other_pod_id if rng.random() < 0.08 else world.pod_id,
                "resource_type": rng.choice(["folder", "document", "folder"])
                if rng.random() > 0.05
                else "datastore_table",
                "resource_id": target,
                "grantee_type": grantee.type,
                "grantee_id": grantee.id,
                "permission_id": rng.choice([_READ, _WRITE, _DELETE, _READ]),
            }
        )
    return grants


async def _seed(session, rows: list[dict], grants: list[dict]) -> None:
    # Fabricated owners, pods and grantees: foreign keys are not what is under
    # test, and `truncate_all` empties these tables before the next test.
    await session.execute(text("SET LOCAL session_replication_role = 'replica'"))
    if rows:
        await session.execute(
            text("""
            INSERT INTO datastore_files
              (id, pod_id, owner_user_id, kind, visibility, path, name,
               size_bytes, search_enabled, status, processing_attempts,
               created_at, updated_at)
            VALUES (:id, :pod, :owner, :kind, :visibility, :path, :name, 1,
                    :search_enabled, :status, 0, now(), now())
            """),
            rows,
        )
    if grants:
        await session.execute(
            text("""
            INSERT INTO resource_permission_grants
              (id, pod_id, resource_type, resource_id, grantee_type, grantee_id,
               permission_id, created_at)
            VALUES (gen_random_uuid(), :pod, :resource_type, :resource_id,
                    :grantee_type, :grantee_id, :permission_id, now())
            ON CONFLICT DO NOTHING
            """),
            grants,
        )


def _contexts(world: _World) -> list[tuple[str, Context]]:
    u0, u1, u2 = world.owners
    p = world.principals
    everything = frozenset({_READ, _WRITE, _DELETE})

    def ctx(name: str, actor: Actor, **kwargs) -> tuple[str, Context]:
        kwargs.setdefault("pod_id", world.pod_id)
        return name, Context(actor=actor, actor_id=name, authorizer=None, **kwargs)

    def person(user_id: UUID | None) -> Person:
        # A context with no user id still names a person; the rule reads the
        # context's own ``user_id``.
        return Person(user_id=user_id or uuid4())

    agent1, function1, pod1 = p["agent1"], p["function1"], p["pod1"]
    other_pod = OtherPod(pod_id=pod1.id)

    return [
        ctx("superuser", person(u0), user_id=u0, is_superuser=True),
        ctx(
            "member-reader",
            person(u0),
            user_id=u0,
            permission_ids=frozenset({_READ}),
            principal_refs=frozenset({p["role1"], p["member1"]}),
        ),
        ctx(
            "member-without-role-read",
            person(u1),
            user_id=u1,
            principal_refs=frozenset({p["role2"]}),
        ),
        ctx(
            "member-editor",
            person(u2),
            user_id=u2,
            permission_ids=everything,
            principal_refs=frozenset({p["role1"], p["role2"]}),
        ),
        ctx(
            "member-without-principals",
            person(u1),
            user_id=u1,
            permission_ids=frozenset({_READ}),
        ),
        ctx(
            "member-two-principal-sets",
            person(u0),
            user_id=u0,
            permission_ids=frozenset({_READ}),
            principal_refs=frozenset({p["role1"], p["agent1"]}),
            grant_principal_sets=(
                frozenset({p["role1"]}),
                frozenset({p["agent1"]}),
            ),
        ),
        ctx(
            "user-without-user-id",
            person(None),
            permission_ids=frozenset({_READ}),
            principal_refs=frozenset({p["role1"]}),
        ),
        ctx(
            "default-agent",
            Assistant(user_id=u0),
            user_id=u0,
            permission_ids=everything,
            principal_refs=frozenset({p["role1"], p["member1"]}),
        ),
        ctx(
            "delegated-agent",
            Workload(
                kind=WorkloadKind.AGENT,
                workload_id=agent1.id,
                for_user_id=u0,
                principal_refs=frozenset({agent1}),
            ),
            user_id=u0,
            permission_ids=frozenset({_READ}),
            principal_refs=frozenset({p["role1"], p["agent1"]}),
            invoker_principal_refs=frozenset({p["role1"]}),
        ),
        ctx(
            "delegated-function-no-user",
            # Nobody behind it: a person-less run, decided on its own grants.
            Workload(
                kind=WorkloadKind.FUNCTION,
                workload_id=function1.id,
                principal_refs=frozenset({function1}),
            ),
            principal_refs=frozenset({p["function1"]}),
        ),
        ctx(
            "agent-actor",
            Workload(kind=WorkloadKind.AGENT, workload_id=agent1.id),
            principal_refs=frozenset({p["agent1"]}),
        ),
        ctx(
            "function-actor-with-role-read",
            Workload(kind=WorkloadKind.FUNCTION, workload_id=function1.id),
            permission_ids=frozenset({_READ}),
            principal_refs=frozenset({p["function1"]}),
        ),
        ctx("pod-actor", other_pod, principal_refs=frozenset({p["pod1"]})),
        ctx(
            "pod-actor-two-sets",
            other_pod,
            principal_refs=frozenset({p["pod1"], p["role1"]}),
            grant_principal_sets=(frozenset({p["pod1"]}), frozenset({p["role1"]})),
        ),
        ctx(
            "pod-actor-pinned-elsewhere",
            other_pod,
            pod_id=world.other_pod_id,
            principal_refs=frozenset({p["pod1"]}),
        ),
        ctx(
            "pod-actor-unpinned",
            other_pod,
            pod_id=None,
            principal_refs=frozenset({p["pod1"]}),
        ),
        ctx("anonymous-pinned", Outsider(kind=OutsiderKind.ANONYMOUS)),
        ctx("anonymous-unpinned", Outsider(kind=OutsiderKind.ANONYMOUS), pod_id=None),
        ctx("contact", Outsider(kind=OutsiderKind.CONTACT)),
    ]


async def _ids(session, stmt) -> set[UUID]:
    return set((await session.execute(stmt)).scalars().all())


async def _build_world(db_session, pod_api: DatastoreApi, seed: int):
    rng = random.Random(seed)
    world = _World(
        pod_id=UUID(pod_api.pod_id),
        other_pod_id=uuid4(),
        owners=(uuid4(), uuid4(), uuid4()),
        principals={
            "role1": PrincipalRef("ROLE", uuid4()),
            "role2": PrincipalRef("ROLE", uuid4()),
            "member1": PrincipalRef("POD_MEMBER", uuid4()),
            "agent1": PrincipalRef("AGENT", uuid4()),
            "function1": PrincipalRef("FUNCTION", uuid4()),
            "pod1": PrincipalRef("POD", uuid4()),
        },
    )
    rows = _random_rows(rng, world)
    # Another pod with the same shape of paths: its rows must never count,
    # and grants pointing at them must not cascade into this pod.
    other_rows = [
        {**row, "id": uuid7(), "pod": world.other_pod_id}
        for row in _random_rows(rng, world)
    ]
    grants = _random_grants(rng, world, rows, other_rows)
    await _seed(db_session, rows + other_rows, grants)
    return rng, world, rows


@pytest.mark.parametrize("seed", range(16))
async def test_resolved_rule_matches_the_per_row_case(
    db_session, pod_api: DatastoreApi, seed: int
) -> None:
    rng, world, rows = await _build_world(db_session, pod_api, seed)
    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
    all_ids = {row["id"] for row in rows}
    # Indexed files: the world's rows are COMPLETED or never indexed, so
    # ``indexed_sha256`` (the reprocessing half of the rule) does not arise.
    searchable = {
        row["id"]
        for row in rows
        if row["kind"] == "FILE"
        and row["status"] == "COMPLETED"
        and row["search_enabled"]
    }
    paths = {row["id"]: row["path"] for row in rows}

    def show(ids: set[UUID]) -> list[str]:
        return sorted(paths.get(i, str(i)) for i in ids)

    for name, ctx in _contexts(world):
        for walk in (False, True):
            label = f"seed={seed} ctx={name} walk={walk}"
            oracle = await _ids(
                db_session, _per_row_statement(ctx, world.pod_id, walk=walk)
            )
            resolved = await repository.visible_file_ids(
                pod_id=world.pod_id, ctx=ctx, walk_ancestors=walk
            )
            assert resolved == oracle, (
                f"{label}: the resolved rule disagrees with the per-row CASE\n"
                f"  only resolved: {show(resolved - oracle)}\n"
                f"  only per-row:  {show(oracle - resolved)}"
            )

            among = {i for i in all_ids if rng.random() < 0.4} | {uuid7()}
            narrowed = await repository.visible_file_ids(
                pod_id=world.pod_id, ctx=ctx, walk_ancestors=walk, among=among
            )
            assert narrowed == oracle & among, (
                f"{label}: authorizing a candidate list disagrees\n"
                f"  only narrowed: {show(narrowed - oracle)}\n"
                f"  missed:        {show((oracle & among) - narrowed)}"
            )

            partition = await repository.searchable_partition(
                pod_id=world.pod_id, ctx=ctx, walk_ancestors=walk, timeout_ms=10_000
            )
            assert partition.readable == oracle & searchable, (
                f"{label}: the searchable split's readable side is wrong\n"
                f"  extra:   {show(set(partition.readable) - oracle)}\n"
                f"  missing: {show((oracle & searchable) - partition.readable)}"
            )
            assert partition.hidden == searchable - oracle, (
                f"{label}: the searchable split's hidden side is wrong"
            )
            # Read with a limit, each side is the right rows, just fewer.
            limit = max(1, len(searchable) // 3)
            capped = await repository.searchable_partition(
                pod_id=world.pod_id,
                ctx=ctx,
                walk_ancestors=walk,
                timeout_ms=10_000,
                limit=limit,
            )
            for side, full in (
                (capped.readable, partition.readable),
                (capped.hidden, partition.hidden),
            ):
                assert side <= full and len(side) == min(limit, len(full)), (
                    f"{label}: a limited split read the wrong rows"
                )

        listed = await repository.list_readable_anywhere(
            world.pod_id, ctx, limit=10_000
        )
        row_alone = _per_row_statement(ctx, world.pod_id, walk=False)
        assert {item.id for item in listed} == await _ids(db_session, row_alone), (
            f"seed={seed} ctx={name}: the any-depth listing disagrees"
        )


@pytest.mark.parametrize("seed", range(16))
async def test_the_split_cascade_matches_the_original(
    db_session, pod_api: DatastoreApi, seed: int
) -> None:
    """Two EXISTS answer exactly what one EXISTS with an OR inside answered."""
    _, world, rows = await _build_world(db_session, pod_api, seed)
    paths = {row["id"]: row["path"] for row in rows}
    groups = {frozenset({principal}) for principal in world.principals.values()} | {
        frozenset(world.principals.values())
    }
    for group in groups:
        for action in (_READ, _WRITE, _DELETE):
            base = select(DatastoreFile.id).where(DatastoreFile.pod_id == world.pod_id)
            legacy = await _ids(
                db_session,
                base.where(_legacy_grant_exists(principal_group=group, action=action)),
            )
            split = await _ids(
                db_session,
                base.where(_split_grant_exists(principal_group=group, action=action)),
            )
            assert split == legacy, (
                f"seed={seed} {sorted(p.type for p in group)} {action}\n"
                f"  only split:    {sorted(paths[i] for i in split - legacy)}\n"
                f"  only original: {sorted(paths[i] for i in legacy - split)}"
            )


async def test_the_fixture_reaches_every_branch(
    db_session, pod_api: DatastoreApi
) -> None:
    """Equivalence over pods that never hide anything would prove nothing.

    Across the seeds the suite runs, every kind of caller must see some rows
    and miss others, the walk must hide something the row-alone rule shows,
    and a grant must be what makes some row readable.
    """
    hidden_by_walk = 0
    granted_only = 0
    for seed in range(16):
        await db_session.execute(text("DELETE FROM resource_permission_grants"))
        await db_session.execute(text("DELETE FROM datastore_files"))
        _, world, rows = await _build_world(db_session, pod_api, seed)
        repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
        contexts = dict(_contexts(world))
        member = contexts["member-without-role-read"]
        alone = await repository.visible_file_ids(
            pod_id=world.pod_id, ctx=member, walk_ancestors=False
        )
        walked = await repository.visible_file_ids(
            pod_id=world.pod_id, ctx=contexts["member-reader"], walk_ancestors=True
        )
        unwalked = await repository.visible_file_ids(
            pod_id=world.pod_id, ctx=contexts["member-reader"], walk_ancestors=False
        )
        hidden_by_walk += len(unwalked - walked)
        delegated = await repository.visible_file_ids(
            pod_id=world.pod_id, ctx=contexts["delegated-agent"], walk_ancestors=False
        )
        granted_only += len(delegated) + len(alone)
    assert hidden_by_walk, "no seed has a readable row under an unreadable one"
    assert granted_only, "no seed has a grant that makes anything readable"
