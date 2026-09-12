"""Resolving a revision ref: same answers as the Python it replaced, far fewer rows.

The twin of ``apps/tests/e2e/test_app_release_ref_lookup_e2e.py``, and kept
separate for the same reason the two resolvers are: the stored hash carries a
``sha256:`` prefix the typed ref does not, so the two statements differ in the
one place where getting it wrong returns an empty result rather than an error.

Revisions are minted through the repository rather than by saving code -- the
hash of a real build is not something a test can choose, and every case that
matters here is a statement about which hashes exist. That is also what keeps
this module out of the sandbox lane.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import text, update
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.e2e


def _hash(prefix: str) -> str:
    """A stored revision hash whose digest starts with *prefix*."""
    return "sha256:" + (prefix + "0" * 64)[:64]


async def _create_function(client, pod_id: str) -> str:
    name = f"fn_refs_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "description": "ref lookup e2e"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _mint(db_session, function_id, hashes):
    from app.modules.function.domain.entities import FunctionRevisionEntity
    from app.modules.function.infrastructure.models import FunctionRevisionModel
    from app.modules.function.infrastructure.repositories import FunctionRepository

    repository = FunctionRepository.__new__(FunctionRepository)
    repository.session = db_session
    for revision_hash, pruned in hashes:
        entity = await repository.record_revision(
            FunctionRevisionEntity(
                function_id=UUID(function_id),
                revision_number=0,
                revision_hash=revision_hash,
                code_path=f"revisions/{uuid4()}/function.py",
            )
        )
        if pruned:
            await db_session.execute(
                update(FunctionRevisionModel)
                .where(FunctionRevisionModel.id == entity.id)
                .values(pruned_at=datetime.now(timezone.utc))
            )
    await db_session.commit()
    return repository


async def _bulk_seed(db_session, function_id, count):
    """Fill the table so the planner has a real choice, and tell it the shape.

    Raw SQL because `record_revision` locks the parent function and allocates
    its number one row at a time, which is the right thing for a build and the
    wrong thing for 500 of them. ANALYZE afterwards is what the plan assertion
    needs: on a table with no statistics every index costs about the same and
    the planner picks arbitrarily, which is not a fact about the index.
    """
    await db_session.execute(
        text(
            "INSERT INTO function_revisions (id, created_at, function_id, "
            "revision_number, revision_hash, code_path, input_schema, "
            "output_schema) "
            "SELECT gen_random_uuid(), now(), :function_id, g, "
            "'sha256:' || md5(g::text) || md5((g + 1)::text), "
            "'revisions/' || g || '/function.py', "
            '\'{"type": "object"}\'::jsonb, \'{"type": "object"}\'::jsonb '
            "FROM generate_series(1, :count) g"
        ),
        {"function_id": function_id, "count": count},
    )
    await db_session.execute(text("ANALYZE function_revisions"))
    await db_session.commit()


def _oracle(revisions, prefix):
    """The resolver's own Python, before it became a statement.

    Note the ``removeprefix``: the ref is bare hex and the column is not. This
    is the asymmetry the statement has to reproduce, and the reason an
    unanchored guess here would fail silently rather than loudly.
    """
    if not prefix:
        return []
    matches = sorted(
        (
            item
            for item in revisions
            if item.revision_hash.removeprefix("sha256:").startswith(prefix)
        ),
        key=lambda item: (item.is_pruned, -(item.revision_number or 0)),
    )
    best: dict[str, object] = {}
    for item in matches:
        best.setdefault(item.revision_hash, item)
    return [best[digest] for digest in sorted(best)][:2]


# Two hashes share `abc`, and `abcd` carries three rows: republishing code whose
# hash is still live reuses its row -- `uq_function_revision_active_hash` sees to
# that -- so a hash only repeats once retention has pruned the earlier row, which
# is exactly the history below. `ab` reaches both hashes and is ambiguous; `abcd`
# reaches one and is not, and must resolve to the live row rather than the newest
# pruned one. `b2` has nothing but a pruned row, which still has to resolve so the
# caller can say "removed by retention" instead of 404.
_HASHES = [
    (_hash("abcd"), True),
    (_hash("abcd"), True),
    (_hash("abcd"), False),
    (_hash("abce"), False),
    (_hash("b1"), False),
    (_hash("b2"), True),
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
    _hash("abcd").removeprefix("sha256:"),
    _hash("abcd").removeprefix("sha256:") + "extra",
    # The algorithm label is stripped from a ref before it gets here, so a
    # prefix that still carries one names nothing -- the same answer the Python
    # gave, and not the one a naive `LIKE prefix%` on the raw column would.
    "sha256:abcd",
    # LIKE metacharacters, read literally by `startswith`.
    "ab%",
    "a_cd",
    "%",
    "_",
    "ab!",
    "!",
]


async def test_a_hash_prefix_resolves_to_what_the_python_resolved_to(
    authenticated_client, test_pod, db_session
):
    function_id = await _create_function(authenticated_client, test_pod["id"])
    repository = await _mint(db_session, function_id, _HASHES)

    everything = await repository.list_revisions(UUID(function_id))
    assert len(everything) == len(_HASHES)

    for prefix in _PREFIXES:
        found = await repository.find_revisions_by_hash_prefix(
            UUID(function_id), prefix
        )
        expected = _oracle(everything, prefix)
        assert [item.id for item in found] == [item.id for item in expected], (
            f"prefix {prefix!r}: statement and Python disagree"
        )


async def test_the_read_stays_at_two_rows_as_the_history_grows(
    authenticated_client, test_pod, db_session
):
    """The point of the change: resolving a ref must not cost the build history.

    Asserted as rows *hydrated*, which is what actually grew. The statement
    count was always one -- ``list_revisions`` is a single query -- so counting
    statements would have called the old version efficient.
    """
    function_id = await _create_function(authenticated_client, test_pod["id"])
    await _bulk_seed(db_session, function_id, 500)
    repository = await _mint(db_session, function_id, [(_hash("abcd"), False)])

    assert len(await repository.list_revisions(UUID(function_id))) == 501
    resolved = await repository.find_revisions_by_hash_prefix(UUID(function_id), "abcd")
    assert len(resolved) == 1
    assert resolved[0].revision_hash == _hash("abcd")


async def test_the_prefix_comparison_is_answered_by_the_index(
    authenticated_client, test_pod, db_session
):
    """`text_pattern_ops` is load-bearing, and this is what says so.

    A plain btree on `(function_id, revision_hash)` also produces an index plan
    here -- on the `function_id` equality alone, with the `LIKE` demoted to a
    heap ``Filter``, which is every revision the function has. What
    distinguishes a prefix the index actually answers is the pattern-range
    operator ``~>=~`` in the Index Cond, which only the `text_pattern_ops`
    opclass puts there under a collation that is not C.

    The table is seeded first because an empty one makes every index look alike
    to the planner, and sequential scans are then disabled because on a table
    this size it would rightly prefer one. The question is what the index *can*
    serve, not what wins at test scale.
    """
    from app.modules.function.infrastructure.revision_repository import (
        revision_prefix_statement,
    )

    function_id = await _create_function(authenticated_client, test_pod["id"])
    await _bulk_seed(db_session, function_id, 500)

    compiled = revision_prefix_statement(UUID(function_id), "abcd").compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )

    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    explained = await db_session.execute(text(f"EXPLAIN (FORMAT JSON) {compiled}"))
    plan = explained.scalar_one()
    if isinstance(plan, str):
        plan = json.loads(plan)

    conditions: list[str] = []

    def walk(node: dict) -> None:
        if node.get("Index Name") == "ix_function_revision_function_hash_prefix":
            conditions.append(node.get("Index Cond", ""))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan[0]["Plan"])
    assert conditions, (
        "the prefix index was not used at all -- the planner reached for "
        f"something that cannot answer the prefix: {plan}"
    )
    assert any("~>=~" in condition for condition in conditions), (
        "the index was used for `function_id` only -- the prefix fell through "
        f"to a heap filter, which reads every revision: {conditions}"
    )
