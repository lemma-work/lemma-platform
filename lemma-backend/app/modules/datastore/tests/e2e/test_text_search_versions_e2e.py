"""Text search answers the same at pod-schema version 2 as it did at version 1.

Version 1 matched and ranked each chunk on one vector, its file's path (weight
B) followed by its content (weight A), computed per row. Version 2 stores the
two halves in two tables. Testing them separately -- the content matches, or
the path does -- loses every query whose terms are split between the two, and
lets a chunk through whose path holds a negated term. So the halves are joined
at query time, and this proves the answer did not move: a randomized corpus at
version 1, a few hundred randomized queries in websearch syntax (plain terms,
split terms, negations, quoted phrases, ``or``), the same results with the
same scores after the migration -- and a plan that reaches chunks through the
two text indexes rather than by reading every chunk.
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings
from app.core.test_utils import get_postgres_url
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.infrastructure.file_chunk_repository import (
    DatastoreFileChunkRepository,
    parent_path_of,
)
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHUNKS_VERSION,
    SEARCH_FILES_VERSION,
    forget_all,
    migrate_pod_schema,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import (
    close_datastore_engine,
    get_datastore_session_maker,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = pytest.mark.e2e

postgres_only_container = e2e_fixtures.postgres_only_container
postgres_only_database_url = e2e_fixtures.postgres_only_database_url

# Words the English stemmer leaves recognisable and the parser splits on the
# spaces around them, so they become lexemes in a path as well as in content.
_WORDS = (
    "budget forecast revenue quarterly invoice contract summary meeting "
    "roadmap hiring launch vendor audit payroll travel marketing pricing "
    "research customer support"
).split()
_FILES = 60
_QUERIES = 300
_SEEDS = (7, 1234, 98765)


@pytest.fixture
async def engine(
    postgres_only_container, postgres_only_database_url
) -> AsyncIterator[AsyncEngine]:
    name = f"lemma_tsv_{uuid4().hex[:12]}"
    admin = create_async_engine(
        postgres_only_database_url, isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = get_postgres_url(postgres_only_container, name)
    previous = datastore_settings.datastore_database_url
    datastore_settings.datastore_database_url = url
    await close_datastore_engine()
    forget_all()
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()
        forget_all()
        await close_datastore_engine()
        datastore_settings.datastore_database_url = previous
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


def _phrase(rng: random.Random, low: int, high: int) -> str:
    return " ".join(rng.sample(_WORDS, rng.randint(low, high)))


def _path(rng: random.Random) -> str:
    folders = [_phrase(rng, 1, 2).title() for _ in range(rng.randint(0, 2))]
    name = _phrase(rng, 1, 2).replace(" ", rng.choice((" ", "-", "_")))
    return "/" + "/".join([*folders, f"{name}.pdf"])


def _query(rng: random.Random) -> str:
    terms: list[str] = []
    for _ in range(rng.randint(1, 3)):
        roll = rng.random()
        if roll < 0.15:
            terms.append(f'"{_phrase(rng, 2, 2)}"')
        elif roll < 0.3:
            terms.append(f"-{rng.choice(_WORDS)}")
        else:
            terms.append(rng.choice(_WORDS))
        if len(terms) > 1 and rng.random() < 0.15:
            terms.insert(-1, "or")
    return " ".join(terms)


async def _corpus(engine: AsyncEngine, schema: str, rng: random.Random) -> None:
    dim = settings.embedding_dimension
    vector = "[" + ",".join(["0.1"] * dim) + "]"
    async with engine.begin() as conn:
        for _ in range(_FILES):
            file_id = uuid4()
            path = _path(rng)
            meta = json.dumps({"path": path, "parent_path": parent_path_of(path)})
            for index in range(rng.randint(1, 3)):
                await conn.execute(
                    text(
                        f'INSERT INTO "{schema}".reserved_chunks '
                        "(chunk_index, file_id, content, embedding, chunk_metadata) "
                        "VALUES (:i, :f, :c, CAST(:v AS vector), CAST(:m AS jsonb))"
                    ),
                    {
                        "i": index,
                        "f": file_id,
                        "c": _phrase(rng, 2, 8),
                        "v": vector,
                        "m": meta,
                    },
                )


type _Hit = tuple[UUID, int, float]


async def _answers(
    repo: DatastoreFileChunkRepository, queries: list[str], version: int
) -> dict[str, set[_Hit]]:
    answers: dict[str, set[_Hit]] = {}
    for query in queries:
        rows = await repo.text_search(
            query,
            uuid4(),
            limit=10_000,
            file_scope=SearchFileScope.post_filtered(),
            version=version,
        )
        scores = [row["score"] for row in rows]
        assert scores == sorted(scores, reverse=True), query
        answers[query] = {
            (row["file_id"], row["chunk_index"], round(row["score"], 6)) for row in rows
        }
    return answers


# Each seed builds and compares a whole randomized corpus, most of a minute
# between them; the protected lane runs all three.
# `test_version_2_reaches_chunks_through_the_text_indexes` stays in the
# pull-request lane.
@pytest.mark.slow
@pytest.mark.parametrize("seed", _SEEDS)
async def test_version_2_matches_and_ranks_exactly_as_version_1(
    engine: AsyncEngine, seed: int
) -> None:
    rng = random.Random(seed)
    schema = schema_name_for_pod(uuid4())
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await migrate_pod_schema(engine, schema, target=CHUNKS_VERSION)
    await _corpus(engine, schema, rng)
    repo = DatastoreFileChunkRepository(get_datastore_session_maker(), schema)
    queries = sorted({_query(rng) for _ in range(_QUERIES)})
    # The case the split broke, spelled out: a term only in the path and a
    # term only in the content of the same chunk.
    split_file = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f'INSERT INTO "{schema}".reserved_chunks '
                "(chunk_index, file_id, content, embedding, chunk_metadata) "
                "SELECT 0, :f, 'forecast for next year', embedding, "
                "CAST(:m AS jsonb) "
                f'FROM "{schema}".reserved_chunks LIMIT 1'
            ),
            {
                "f": split_file,
                "m": json.dumps(
                    {
                        "path": "/Q3 Budget Review/notes.pdf",
                        "parent_path": "/Q3 Budget Review",
                    }
                ),
            },
        )
    split = "budget forecast"
    queries = sorted({*queries, split, "budget -notes", "-budget forecast"})

    before = await _answers(repo, queries, CHUNKS_VERSION)
    await migrate_pod_schema(engine, schema)
    after = await _answers(repo, queries, SEARCH_FILES_VERSION)

    differ = {q: (before[q] ^ after[q]) for q in queries if before[q] != after[q]}
    assert not differ, f"{len(differ)} of {len(queries)} queries differ: {differ}"
    # The corpus is not degenerate: most queries find something, and the
    # split-term query finds the chunk it was built from.
    assert sum(1 for q in queries if before[q]) > len(queries) // 3
    assert split_file in {file_id for file_id, _, _ in after[split]}
    assert split_file not in {file_id for file_id, _, _ in after["-budget forecast"]}


async def test_version_2_reaches_chunks_through_the_text_indexes(
    engine: AsyncEngine,
) -> None:
    rng = random.Random(42)
    schema = schema_name_for_pod(uuid4())
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await migrate_pod_schema(engine, schema, target=CHUNKS_VERSION)
    await _corpus(engine, schema, rng)
    await migrate_pod_schema(engine, schema)
    repo = DatastoreFileChunkRepository(get_datastore_session_maker(), schema)

    async def plan(query: str) -> str:
        stmt, params = repo.text_search_statement(
            query, 10, file_scope=SearchFileScope.post_filtered()
        )
        async with engine.begin() as conn:
            # Tiny tables are cheaper to scan; the question is whether the
            # statement *can* use the indexes, which a scan would hide.
            await conn.execute(text("SET LOCAL enable_seqscan = off"))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            explain = text(f"EXPLAIN (ANALYZE, COSTS OFF) {stmt.text}")
            explain = explain.bindparams(*stmt._bindparams.values())
            rows = await conn.execute(explain, params)
            return "\n".join(row[0] for row in rows)

    probed = await plan("budget -forecast")
    assert "ix_reserved_chunks_tsv" in probed, probed
    assert "ix_search_files_path_tsv" in probed, probed
    assert "Seq Scan on reserved_chunks" not in probed, probed
    # Only one branch runs: the unprobed one is gated off.
    assert "One-Time Filter" in probed, probed

    # Nothing but negations has nothing to probe with, and still answers.
    assert await repo.text_search(
        "-budget", uuid4(), limit=5, file_scope=SearchFileScope.post_filtered()
    )
    # A query of stop words matches nothing and reads nothing.
    assert (
        await repo.text_search(
            "the and", uuid4(), limit=5, file_scope=SearchFileScope.post_filtered()
        )
        == []
    )
