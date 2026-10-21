from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import text

from app.core.concurrency.offload import run_blocking
from app.core.config import settings
from app.core.infrastructure.db.sql_text import escape_like as _escape_like
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    SEARCH_FILES_VERSION,
)


def parent_path_of(path: str) -> str:
    """The folder a path sits in, ``/`` for the root."""
    trimmed = path.rstrip("/")
    if "/" not in trimmed[1:]:
        return "/"
    return trimmed.rsplit("/", 1)[0]


# Query-time HNSW recall/latency knob (pgvector default is 40). Raising it
# improves recall, especially when the WHERE clause filters the ANN result
# (folder subtree, and the readable-id array when the scope is enumerated).
_HNSW_EF_SEARCH = 100


class DatastoreFileChunkRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        schema_name: str,
    ):
        self._session_factory = session_factory
        self.schema_name = schema_name

    @staticmethod
    def _build_rows(
        file_id: UUID,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        metadata: dict | None,
    ) -> list[dict[str, Any]]:
        rows = []
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            final_meta = {**(metadata or {}), **chunk.get("metadata", {})}
            rows.append(
                {
                    "chunk_index": idx,
                    "file_id": file_id,
                    "content": chunk.get("text", ""),
                    "embedding": "[" + ",".join(str(x) for x in embedding) + "]",
                    "metadata": json.dumps(final_meta),
                }
            )
        return rows

    async def add_chunks(
        self,
        file_id: UUID,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        metadata: dict | None = None,
        *,
        version: int = SEARCH_FILES_VERSION,
        sha256: str | None = None,
    ) -> int:
        """Replace a file's chunks in one transaction.

        The old chunks stay searchable until the new ones commit, and a failure
        anywhere before that -- embedding, a constraint, a dropped connection --
        leaves them exactly as they were. At version 2 the file's
        ``search_files`` row is written in the same transaction, carrying the
        sha the chunks were made from.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Cannot index {len(chunks)} chunks with {len(embeddings)} embeddings"
            )
        # Off the loop: this formats every dimension of every vector into text
        # (768 floats per chunk, hundreds of chunks for one document) and
        # json-dumps each chunk's metadata beside it. Pure CPU, proportional to
        # the document, on the indexing hot path.
        rows = await run_blocking(
            self._build_rows,
            file_id,
            chunks,
            embeddings,
            metadata,
            limiter="cpu_bound",
        )
        schema = self.schema_name
        async with self._session_factory() as session:
            # SET LOCAL, never bare SET: the connection goes back to the pool
            # when this session closes, and a bare SET would leave this pod's
            # schema on the search_path for whoever borrows it next.
            await session.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            if version >= SEARCH_FILES_VERSION:
                path = str((metadata or {}).get("path") or "")
                await session.execute(
                    text(
                        f'''INSERT INTO "{schema}".search_files
                        (file_id, path, parent_path, sha256, chunk_count, indexed_at)
                        VALUES (:file_id, :path, :parent_path, :sha256, :count, now())
                        ON CONFLICT (file_id) DO UPDATE SET
                            path = EXCLUDED.path, parent_path = EXCLUDED.parent_path,
                            sha256 = EXCLUDED.sha256, chunk_count = EXCLUDED.chunk_count,
                            indexed_at = EXCLUDED.indexed_at'''
                    ),
                    {
                        "file_id": file_id,
                        "path": path,
                        "parent_path": parent_path_of(path),
                        "sha256": sha256,
                        "count": len(rows),
                    },
                )
            await session.execute(
                text(
                    f'DELETE FROM "{schema}".reserved_chunks WHERE file_id = :file_id'
                ),
                {"file_id": file_id},
            )
            if rows:
                await session.execute(
                    text(
                        f'''INSERT INTO "{schema}".reserved_chunks (chunk_index, file_id, content, embedding, chunk_metadata) VALUES (:chunk_index, :file_id, :content, CAST(:embedding AS vector), CAST(:metadata AS jsonb))'''
                    ),
                    rows,
                )
            await session.commit()
            return len(chunks)

    async def remove_chunks_by_file(
        self, file_id: UUID, *, version: int = SEARCH_FILES_VERSION
    ) -> bool:
        return bool(await self.remove_chunks_by_files([file_id], version=version) >= 0)

    async def remove_chunks_by_files(
        self, file_ids: Sequence[UUID], *, version: int = SEARCH_FILES_VERSION
    ) -> int:
        """Drop the chunks of many files in one statement, and one transaction.

        One transaction means a folder's chunks go together or not at all, and
        deleting five hundred files is one round trip rather than five hundred
        sessions each committing on its own.
        """
        ids = list(file_ids)
        if not ids:
            return 0
        schema = self.schema_name
        async with self._session_factory() as session:
            await session.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            result = await session.execute(
                text(
                    f'DELETE FROM "{schema}".reserved_chunks '
                    "WHERE file_id = ANY(:file_ids)"
                ),
                {"file_ids": ids},
            )
            if version >= SEARCH_FILES_VERSION:
                await session.execute(
                    text(
                        f'DELETE FROM "{schema}".search_files '
                        "WHERE file_id = ANY(:file_ids)"
                    ),
                    {"file_ids": ids},
                )
            await session.commit()
            return int(result.rowcount or 0)

    async def update_file_path(
        self,
        file_id: UUID,
        path: str,
        parent_path: str | None = None,
        *,
        version: int = SEARCH_FILES_VERSION,
    ) -> bool:
        """Point a file's index entry at its new path.

        One row at version 2; at version 1 the path is in every chunk's JSON,
        so every chunk is rewritten.
        """
        schema = self.schema_name
        async with self._session_factory() as session:
            await session.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            if version >= SEARCH_FILES_VERSION:
                await session.execute(
                    text(
                        f'UPDATE "{schema}".search_files '
                        "SET path = :path, parent_path = :parent_path "
                        "WHERE file_id = :file_id"
                    ),
                    {
                        "file_id": file_id,
                        "path": path,
                        "parent_path": parent_path or parent_path_of(path),
                    },
                )
            else:
                await session.execute(
                    text(
                        f'''
                        UPDATE "{schema}".reserved_chunks
                        SET chunk_metadata =
                            jsonb_set(
                                jsonb_set(
                                    COALESCE(chunk_metadata, '{{}}'::jsonb),
                                    '{{path}}',
                                    to_jsonb(CAST(:path AS TEXT)),
                                    true
                                ),
                                '{{parent_path}}',
                                to_jsonb(COALESCE(CAST(:parent_path AS TEXT), '/')),
                                true
                            )
                        WHERE file_id = :file_id
                        '''
                    ),
                    {"file_id": file_id, "path": path, "parent_path": parent_path},
                )
            await session.commit()
            return True

    async def rename_prefix(self, previous_prefix: str, new_prefix: str) -> int:
        """Repoint every indexed file under a renamed folder, in one statement.

        Version 2 only: it is what makes a folder rename one update to the
        index rather than one per file. ``previous_prefix`` is the folder's
        old path; the folder itself has no index entry.
        """
        schema = self.schema_name
        old = previous_prefix.rstrip("/")
        new = new_prefix.rstrip("/")
        async with self._session_factory() as session:
            await session.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            result = await session.execute(
                text(
                    f'''
                    UPDATE "{schema}".search_files
                    SET path = :new || substr(path, :cut),
                        parent_path = CASE
                            WHEN parent_path = :old THEN :new
                            ELSE :new || substr(parent_path, :cut)
                        END
                    WHERE path LIKE :pattern ESCAPE '!'
                    '''
                ),
                {
                    "old": old,
                    "new": new,
                    "cut": len(old) + 1,
                    "pattern": f"{_escape_like(old)}/%",
                },
            )
            await session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _scope_params(
        scope_path: str | None, include_descendants: bool
    ) -> dict[str, Any]:
        return {
            "scope_path": scope_path,
            "scope_prefix": (
                f"{_escape_like(scope_path)}/%"
                if scope_path and scope_path != "/"
                else "/%"
            ),
            "direct_parent_path": scope_path or "/",
            "include_descendants": include_descendants,
        }

    def _scope_clause(self, version: int) -> str:
        """The folder filter, on ``search_files`` at version 2, chunk JSON before.

        At version 2 it becomes an id array computed once (an InitPlan), so the
        chunk scan filters on its own column and the vector index's iterative
        scan keeps pulling candidates until enough pass.
        """
        if version >= SEARCH_FILES_VERSION:
            return f"""(
                CAST(:scope_path AS TEXT) IS NULL
                OR rc.file_id = ANY(ARRAY(
                    SELECT sf.file_id FROM "{self.schema_name}".search_files AS sf
                    WHERE (
                        CAST(:include_descendants AS BOOLEAN) = TRUE
                        AND sf.path LIKE CAST(:scope_prefix AS TEXT) ESCAPE '!'
                    ) OR (
                        CAST(:include_descendants AS BOOLEAN) = FALSE
                        AND sf.parent_path = CAST(:direct_parent_path AS TEXT)
                    )
                ))
            )"""
        return """(
            CAST(:scope_path AS TEXT) IS NULL
            OR (
                CAST(:include_descendants AS BOOLEAN) = TRUE
                AND COALESCE(rc.chunk_metadata ->> 'path', '') LIKE CAST(:scope_prefix AS TEXT) ESCAPE '!'
            )
            OR (
                CAST(:include_descendants AS BOOLEAN) = FALSE
                AND COALESCE(rc.chunk_metadata ->> 'parent_path', '/') = CAST(:direct_parent_path AS TEXT)
            )
        )"""

    def _hits_select(self, version: int, ranked_cte: str) -> str:
        """Join ranked chunk hits to the path they live at now.

        At version 2 the path comes from ``search_files``, never from the
        chunk's JSON, which still holds the path the chunk was indexed at.
        """
        if version >= SEARCH_FILES_VERSION:
            return f"""
                SELECT h.chunk_index, h.file_id, sf.path, h.content,
                    COALESCE(h.chunk_metadata, '{{}}'::jsonb)
                        || jsonb_build_object('path', sf.path, 'parent_path', sf.parent_path)
                        AS chunk_metadata,
                    h.page_number, h.page_end, h.score
                FROM ({ranked_cte}) AS h
                JOIN "{self.schema_name}".search_files AS sf ON sf.file_id = h.file_id
                ORDER BY h.score DESC, h.chunk_index ASC
            """
        return f"""
            SELECT h.chunk_index, h.file_id, h.chunk_metadata ->> 'path' AS path,
                h.content, h.chunk_metadata, h.page_number, h.page_end, h.score
            FROM ({ranked_cte}) AS h
            ORDER BY h.score DESC, h.chunk_index ASC
        """

    @staticmethod
    def _rows(result) -> list[dict[str, Any]]:
        return [
            {
                "file_id": r.file_id,
                "path": r.path,
                "chunk_index": r.chunk_index,
                "content": r.content,
                "metadata": r.chunk_metadata,
                "page_number": r.page_number,
                "page_end": r.page_end,
                "score": float(r.score),
            }
            for r in result
        ]

    async def vector_search(
        self,
        query_embedding: list[float],
        pod_id: UUID,
        limit: int = 10,
        scope_path: str | None = None,
        include_descendants: bool = True,
        *,
        file_scope: SearchFileScope,
        version: int = SEARCH_FILES_VERSION,
    ) -> list[dict[str, Any]]:
        del pod_id
        if file_scope.matches_nothing:
            return []
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        dim = settings.embedding_dimension
        async with self._session_factory() as session:
            await session.execute(
                text(f'SET LOCAL search_path TO "{self.schema_name}", public')
            )
            # Tune the HNSW scan for this query. iterative_scan keeps pulling
            # candidates from the index until enough survive the WHERE filter
            # (folder subtree + readable ids), avoiding ANN over-filtering. These
            # are pgvector 0.8+ GUCs; harmless placeholders if the extension
            # isn't yet loaded in the session.
            await session.execute(text(f"SET LOCAL hnsw.ef_search = {_HNSW_EF_SEARCH}"))
            await session.execute(
                text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            )
            params: dict[str, Any] = {
                "vec": embedding_str,
                "limit": limit,
                **self._scope_params(scope_path, include_descendants),
            }
            file_filter = file_scope.sql_clause("rc.file_id", "readable_file_ids")
            if file_scope.binds:
                params["readable_file_ids"] = file_scope.parameter_value()
            ranked = f"""
                SELECT rc.chunk_index, rc.file_id, rc.content, rc.chunk_metadata,
                    (rc.chunk_metadata ->> 'page_number')::int AS page_number,
                    (rc.chunk_metadata ->> 'page_end')::int AS page_end,
                    1 - (rc.embedding::halfvec({dim}) <=> CAST(:vec AS halfvec({dim}))) AS score
                FROM "{self.schema_name}".reserved_chunks AS rc
                WHERE {self._scope_clause(version)}
                {file_filter}
                ORDER BY rc.embedding::halfvec({dim}) <=> CAST(:vec AS halfvec({dim}))
                LIMIT :limit
            """
            stmt = text(self._hits_select(version, ranked))
            if file_scope.binds:
                stmt = stmt.bindparams(
                    bindparam(
                        "readable_file_ids",
                        type_=ARRAY(PG_UUID(as_uuid=True)),
                    )
                )
            return self._rows(await session.execute(stmt, params))

    def text_search_statement(
        self,
        query: str,
        limit: int = 10,
        scope_path: str | None = None,
        include_descendants: bool = True,
        *,
        file_scope: SearchFileScope,
        version: int = SEARCH_FILES_VERSION,
    ):
        """The text-search statement and its parameters, unexecuted.

        Split from :meth:`text_search` so a plan test can ``EXPLAIN`` exactly
        what a search runs.

        Matching and ranking are on one vector per chunk: its file's path
        (weight B) followed by its content (weight A). A query whose terms are
        split between the two -- ``budget forecast`` for a chunk about the
        forecast in ``/Q3 Budget/notes.pdf`` -- matches the pair and neither
        half, and a negated term in the path excludes the chunk; testing the
        halves separately gets both wrong. Version 1 computes that vector per
        row from an expression; version 2 stores both halves and joins them at
        query time, so its result is the same and only the plan differs.

        No index can hold a vector spread over two tables, so version 2 finds
        candidates through the two it has. ``querytree`` is the part of the
        query an index can answer -- everything but negations -- and a chunk
        can only match if its content or its path holds at least one of that
        part's lexemes; the disjunction of them probes ``tsv`` and
        ``path_tsv``, and the joined vector is then tested exactly. A query
        with no such part (only negations: ``-draft``) has no probe and is
        tested against every chunk in scope, as the version-1 index did too.
        Each branch is gated by a one-time filter, so only one ever runs.
        """
        params: dict[str, Any] = {
            "query": query,
            "limit": limit,
            **self._scope_params(scope_path, include_descendants),
        }
        file_filter = file_scope.sql_clause("rc.file_id", "readable_file_ids")
        if file_scope.binds:
            params["readable_file_ids"] = file_scope.parameter_value()
        schema = self.schema_name
        common = """rc.chunk_index, rc.file_id, rc.content, rc.chunk_metadata,
                    (rc.chunk_metadata ->> 'page_number')::int AS page_number,
                    (rc.chunk_metadata ->> 'page_end')::int AS page_end"""
        if version >= SEARCH_FILES_VERSION:
            parsed = "websearch_to_tsquery('english', CAST(:query AS TEXT))"
            full = f"(SELECT {parsed})"
            # Each lexeme of the indexable part, as tsquery text prints it
            # (quoted, quotes doubled), OR-ed: NULL when there is none.
            probe = (
                "(SELECT string_agg(m[1], ' | ')::tsquery "
                f"FROM regexp_matches(querytree({parsed})::text, "
                "$re$('(?:[^']|'')*')$re$, 'g') AS m)"
            )
            joined = "(sf.path_tsv || rc.tsv)"
            branch = f"""
                    SELECT {common},
                        ts_rank_cd({joined}, {full}) AS score
                    FROM "{schema}".reserved_chunks AS rc
                    JOIN "{schema}".search_files AS sf ON sf.file_id = rc.file_id
                    WHERE {{gate}}
                      AND {self._scope_clause(version)}
                      {file_filter}
                      {{candidates}}
                      AND {joined} @@ {full}
            """
            probed = branch.format(
                gate=f"{probe} IS NOT NULL",
                candidates=f"""AND (
                        rc.tsv @@ {probe}
                        OR rc.file_id = ANY(ARRAY(
                            SELECT pf.file_id FROM "{schema}".search_files AS pf
                            WHERE pf.path_tsv @@ {probe}
                        ))
                      )""",
            )
            unprobed = branch.format(
                gate=f"{probe} IS NULL AND numnode({full}) > 0",
                candidates="",
            )
            ranked = f"""
                SELECT * FROM ({probed} UNION ALL {unprobed}) AS hits
                ORDER BY score DESC, chunk_index ASC
                LIMIT :limit
            """
        else:
            ranked = f"""
                SELECT {common},
                    ts_rank_cd(
                        setweight(to_tsvector('english', COALESCE(rc.chunk_metadata ->> 'path', '')), 'B') ||
                        setweight(to_tsvector('english', rc.content), 'A'),
                        q.query
                    ) AS score
                FROM "{schema}".reserved_chunks AS rc
                CROSS JOIN (SELECT websearch_to_tsquery('english', :query) AS query) AS q
                WHERE {self._scope_clause(version)}
                  {file_filter}
                  AND (
                    setweight(to_tsvector('english', COALESCE(rc.chunk_metadata ->> 'path', '')), 'B') ||
                    setweight(to_tsvector('english', rc.content), 'A')
                  ) @@ q.query
                ORDER BY score DESC, rc.chunk_index ASC
                LIMIT :limit
            """
        stmt = text(self._hits_select(version, ranked))
        if file_scope.binds:
            stmt = stmt.bindparams(
                bindparam(
                    "readable_file_ids",
                    type_=ARRAY(PG_UUID(as_uuid=True)),
                )
            )
        return stmt, params

    async def text_search(
        self,
        query: str,
        pod_id: UUID,
        limit: int = 10,
        scope_path: str | None = None,
        include_descendants: bool = True,
        *,
        file_scope: SearchFileScope,
        version: int = SEARCH_FILES_VERSION,
    ) -> list[dict[str, Any]]:
        """Full-text search over chunk content and its file's path, ranked
        with the path weighted below the content; see
        :meth:`text_search_statement`."""
        del pod_id
        if file_scope.matches_nothing:
            return []
        stmt, params = self.text_search_statement(
            query,
            limit,
            scope_path,
            include_descendants,
            file_scope=file_scope,
            version=version,
        )
        async with self._session_factory() as session:
            await session.execute(
                text(f'SET LOCAL search_path TO "{self.schema_name}", public')
            )
            return self._rows(await session.execute(stmt, params))
