"""Resolving a release ref: same answers as the Python it replaced, far fewer rows.

The resolver used to load an app's entire release history and run ``startswith``
over it. Moving that into SQL is only safe if the statement agrees with the
Python on every case the Python handled -- so the old algorithm is kept here, in
full, and the test asserts the two never disagree. A narrowed query that quietly
returns nothing is fast and wrong, and an equivalence oracle is what tells them
apart.

Releases are minted through the repository rather than by uploading bundles,
because the digest of a real upload is not something a test can choose, and
every case that matters here -- a shared prefix, a redeploy of identical bytes,
a pruned row -- is a statement about which digests exist.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import text, update
from sqlalchemy.dialects import postgresql

from app.modules.apps.domain.entities import AppReleaseEntity

pytestmark = pytest.mark.e2e


def _digest(prefix: str) -> str:
    """A 64-character hex digest starting with *prefix*."""
    return (prefix + "0" * 64)[:64]


async def _repository(db_session):
    from app.modules.apps.infrastructure.repositories import AppRepository

    repository = AppRepository.__new__(AppRepository)
    repository.session = db_session
    return repository


async def _create_app(client, pod_id: str) -> str:
    name = f"app_refs_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/apps",
        json={"name": name, "public_slug": f"refs-{uuid4().hex[:8]}"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _mint(repository, db_session, app_id, versions):
    """Record one release per entry, pruning the ones flagged for it."""
    from app.modules.apps.infrastructure.models import AppReleaseModel

    minted = []
    for version, pruned in versions:
        entity = await repository.record_release(
            AppReleaseEntity(
                app_id=UUID(app_id),
                version=version,
                dist_root_path=f"releases/{version}/{uuid4()}/dist/",
            )
        )
        if pruned:
            await db_session.execute(
                update(AppReleaseModel)
                .where(AppReleaseModel.id == entity.id)
                .values(pruned_at=datetime.now(timezone.utc))
            )
        minted.append(entity)
    await db_session.commit()
    return minted


async def _bulk_seed(db_session, app_id, count):
    """Fill the table so the planner has a real choice, and tell it the shape.

    Raw SQL because `record_release` locks the parent app and allocates its
    number one row at a time, which is the right thing for a deployment and the
    wrong thing for 500 of them. ANALYZE afterwards is what the plan assertion
    needs: on a table with no statistics every index costs about the same and
    the planner picks arbitrarily, which is not a fact about the index.
    """
    await db_session.execute(
        text(
            "INSERT INTO app_releases "
            "(id, created_at, app_id, version, release_number, dist_root_path) "
            "SELECT gen_random_uuid(), now(), :app_id, "
            "md5(g::text) || md5((g + 1)::text), g, "
            "'releases/' || g || '/dist/' "
            "FROM generate_series(1, :count) g"
        ),
        {"app_id": app_id, "count": count},
    )
    await db_session.execute(text("ANALYZE app_releases"))
    await db_session.commit()


def _oracle(releases, prefix):
    """The resolver's own Python, before it became a statement.

    ``list_releases`` then ``startswith`` then sort, collapsed to the best row
    per distinct version and cut at two -- which is the contract the repository
    method now publishes, expressed in the terms the old code used.
    """
    if not prefix:
        return []
    matches = sorted(
        (item for item in releases if item.version.startswith(prefix)),
        key=lambda item: (item.is_pruned, -(item.release_number or 0)),
    )
    best: dict[str, AppReleaseEntity] = {}
    for item in matches:
        best.setdefault(item.version, item)
    return [best[version] for version in sorted(best)][:2]


# Two versions share `abc`, one of them twice over (a redeploy of identical
# bytes), and one of those redeploys is pruned. `ab` reaches both versions and
# is therefore ambiguous; `abcd` reaches only the redeployed one and is not.
_VERSIONS = [
    (_digest("abcd"), False),
    (_digest("abcd"), True),
    (_digest("abcd"), False),
    (_digest("abce"), False),
    (_digest("b1"), False),
    (_digest("b2"), True),
]

_PREFIXES = [
    "a",
    "ab",
    "abc",
    "abcd",
    "abce",
    "b",
    "b1",
    "b2",
    "z",
    _digest("abcd"),
    _digest("abcd") + "extra",
    # LIKE metacharacters. `startswith` reads these literally and matches
    # nothing; an unescaped LIKE would read `ab%` as "anything starting with
    # ab" and report an ambiguity that does not exist.
    "ab%",
    "a_cd",
    "%",
    "_",
    "ab!",
    "!",
]


async def test_a_digest_prefix_resolves_to_what_the_python_resolved_to(
    authenticated_client, test_pod, db_session
):
    app_id = await _create_app(authenticated_client, test_pod["id"])
    repository = await _repository(db_session)
    await _mint(repository, db_session, app_id, _VERSIONS)

    everything = await repository.list_releases(UUID(app_id))
    assert len(everything) == len(_VERSIONS)

    for prefix in _PREFIXES:
        found = await repository.find_releases_by_digest_prefix(UUID(app_id), prefix)
        expected = _oracle(everything, prefix)
        assert [item.id for item in found] == [item.id for item in expected], (
            f"prefix {prefix!r}: statement and Python disagree"
        )


async def test_the_read_stays_at_two_rows_as_the_history_grows(
    authenticated_client, test_pod, db_session
):
    """The point of the change: resolving a ref must not cost the deploy history.

    Asserted as rows *hydrated*, which is what actually grew. The statement
    count was always one -- ``list_releases`` is a single query -- so counting
    statements would have called the old version efficient.
    """
    app_id = await _create_app(authenticated_client, test_pod["id"])
    repository = await _repository(db_session)
    await _bulk_seed(db_session, app_id, 500)
    await _mint(repository, db_session, app_id, [(_digest("abcd"), False)])

    assert len(await repository.list_releases(UUID(app_id))) == 501
    resolved = await repository.find_releases_by_digest_prefix(UUID(app_id), "abcd")
    assert len(resolved) == 1
    assert resolved[0].version == _digest("abcd")


async def test_the_prefix_comparison_is_answered_by_the_index(
    authenticated_client, test_pod, db_session
):
    """`text_pattern_ops` is load-bearing, and this is what says so.

    A plain btree on `(app_id, version)` also produces an index plan here -- on
    the `app_id` equality alone, with the `LIKE` demoted to a heap ``Filter``,
    which is every release the app has. What distinguishes a prefix the index
    actually answers is the pattern-range operator ``~>=~`` in the Index Cond,
    which only the `text_pattern_ops` opclass puts there under a collation that
    is not C.

    The table is seeded first because an empty one makes every index look alike
    to the planner, and sequential scans are then disabled because on a table
    this size it would rightly prefer one. The question is what the index *can*
    serve, not what wins at test scale.
    """
    from app.modules.apps.infrastructure.repositories import release_prefix_statement

    app_id = await _create_app(authenticated_client, test_pod["id"])
    await _bulk_seed(db_session, app_id, 500)

    compiled = release_prefix_statement(UUID(app_id), "abcd").compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )

    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    explained = await db_session.execute(text(f"EXPLAIN (FORMAT JSON) {compiled}"))
    plan = explained.scalar_one()
    if isinstance(plan, str):
        plan = json.loads(plan)

    conditions: list[str] = []

    def walk(node: dict) -> None:
        if node.get("Index Name") == "ix_app_release_app_version_prefix":
            conditions.append(node.get("Index Cond", ""))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan[0]["Plan"])
    assert conditions, (
        "the prefix index was not used at all -- the planner reached for "
        f"something that cannot answer the prefix: {plan}"
    )
    assert any("~>=~" in condition for condition in conditions), (
        "the index was used for `app_id` only -- the prefix fell through to a "
        f"heap filter, which reads every release the app has: {conditions}"
    )
