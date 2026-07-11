from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import text

from app.core.config import settings
from app.modules.datastore.infrastructure.sql_identifiers import (
    escape_like as _escape_like,
)

# Query-time HNSW recall/latency knob (pgvector default is 40). Raising it
# improves recall, especially when post-filtering by folder subtree / visibility.
_HNSW_EF_SEARCH = 100


class DatastoreFileChunkRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        schema_name: str,
    ):
        self._session_factory = session_factory
        self.schema_name = schema_name

    async def add_chunks(
        self,
        file_id: UUID,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        metadata: dict | None = None,
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Cannot index {len(chunks)} chunks with {len(embeddings)} embeddings"
            )
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

        async with self._session_factory() as session:
            await session.execute(
                text(f'SET search_path TO "{self.schema_name}", public')
            )
            await session.execute(
                text(
                    f'DELETE FROM "{self.schema_name}".reserved_chunks WHERE file_id = :file_id'
                ),
                {"file_id": file_id},
            )

            if rows:
                await session.execute(
                    text(
                        f'''INSERT INTO "{self.schema_name}".reserved_chunks (chunk_index, file_id, content, embedding, chunk_metadata) VALUES (:chunk_index, :file_id, :content, CAST(:embedding AS vector), CAST(:metadata AS jsonb))'''
                    ),
                    rows,
                )
            await session.commit()
            return len(chunks)

    async def remove_chunks_by_file(self, file_id: UUID) -> bool:
        async with self._session_factory() as session:
            await session.execute(
                text(f'SET search_path TO "{self.schema_name}", public')
            )
            await session.execute(
                text(
                    f'DELETE FROM "{self.schema_name}".reserved_chunks WHERE file_id = :file_id'
                ),
                {"file_id": file_id},
            )
            await session.commit()
            return True

    async def update_file_path(
        self,
        file_id: UUID,
        path: str,
        parent_path: str | None = None,
    ) -> bool:
        async with self._session_factory() as session:
            await session.execute(
                text(f'SET search_path TO "{self.schema_name}", public')
            )
            await session.execute(
                text(
                    f'''
                    UPDATE "{self.schema_name}".reserved_chunks
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
                {
                    "file_id": file_id,
                    "path": path,
                    "parent_path": parent_path,
                },
            )
            await session.commit()
            return True

    async def vector_search(
        self,
        query_embedding: list[float],
        pod_id: UUID,
        limit: int = 10,
        scope_path: str | None = None,
        include_descendants: bool = True,
        visible_file_ids: set[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        del pod_id
        if visible_file_ids is not None and not visible_file_ids:
            return []
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        dim = settings.embedding_dimension
        async with self._session_factory() as session:
            await session.execute(
                text(f'SET search_path TO "{self.schema_name}", public')
            )
            # Tune the HNSW scan for this query. iterative_scan keeps pulling
            # candidates from the index until enough survive the WHERE filter
            # (folder subtree + visibility), avoiding ANN over-filtering. These
            # are pgvector 0.8+ GUCs; harmless placeholders if the extension
            # isn't yet loaded in the session.
            await session.execute(text(f"SET LOCAL hnsw.ef_search = {_HNSW_EF_SEARCH}"))
            await session.execute(
                text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            )
            file_filter = ""
            params: dict[str, Any] = {
                "vec": embedding_str,
                "scope_path": scope_path,
                "scope_prefix": (
                    f"{_escape_like(scope_path)}/%"
                    if scope_path and scope_path != "/"
                    else "/%"
                ),
                "direct_parent_path": scope_path or "/",
                "include_descendants": include_descendants,
                "limit": limit,
            }
            if visible_file_ids is not None:
                file_filter = "AND rc.file_id = ANY(:visible_file_ids)"
                params["visible_file_ids"] = list(visible_file_ids)

            stmt = text(f"""
                SELECT
                    rc.chunk_index,
                    rc.file_id,
                    rc.chunk_metadata ->> 'path' AS path,
                    rc.content,
                    rc.chunk_metadata,
                    (rc.chunk_metadata ->> 'page_number')::int AS page_number,
                    (rc.chunk_metadata ->> 'page_end')::int AS page_end,
                    1 - (rc.embedding::halfvec({dim}) <=> CAST(:vec AS halfvec({dim}))) as score
                FROM "{self.schema_name}".reserved_chunks AS rc
                WHERE (
                    CAST(:scope_path AS TEXT) IS NULL
                    OR (
                        CAST(:include_descendants AS BOOLEAN) = TRUE
                        AND COALESCE(rc.chunk_metadata ->> 'path', '') LIKE CAST(:scope_prefix AS TEXT) ESCAPE '!'
                    )
                    OR (
                        CAST(:include_descendants AS BOOLEAN) = FALSE
                        AND COALESCE(rc.chunk_metadata ->> 'parent_path', '/') = CAST(:direct_parent_path AS TEXT)
                    )
                )
                {file_filter}
                ORDER BY rc.embedding::halfvec({dim}) <=> CAST(:vec AS halfvec({dim}))
                LIMIT :limit
            """)
            if visible_file_ids is not None:
                stmt = stmt.bindparams(
                    bindparam(
                        "visible_file_ids",
                        type_=ARRAY(PG_UUID(as_uuid=True)),
                    )
                )
            result = await session.execute(stmt, params)
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

    async def text_search(
        self,
        query: str,
        pod_id: UUID,
        limit: int = 10,
        scope_path: str | None = None,
        include_descendants: bool = True,
        visible_file_ids: set[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        del pod_id
        if visible_file_ids is not None and not visible_file_ids:
            return []
        async with self._session_factory() as session:
            await session.execute(
                text(f'SET search_path TO "{self.schema_name}", public')
            )
            file_filter = ""
            params: dict[str, Any] = {
                "query": query,
                "scope_path": scope_path,
                "scope_prefix": (
                    f"{_escape_like(scope_path)}/%"
                    if scope_path and scope_path != "/"
                    else "/%"
                ),
                "direct_parent_path": scope_path or "/",
                "include_descendants": include_descendants,
                "limit": limit,
            }
            if visible_file_ids is not None:
                file_filter = "AND rc.file_id = ANY(:visible_file_ids)"
                params["visible_file_ids"] = list(visible_file_ids)

            stmt = text(f"""
                WITH search_query AS (
                    SELECT websearch_to_tsquery('english', :query) AS query
                )
                SELECT
                    rc.chunk_index,
                    rc.file_id,
                    rc.chunk_metadata ->> 'path' AS path,
                    rc.content,
                    rc.chunk_metadata,
                    (rc.chunk_metadata ->> 'page_number')::int AS page_number,
                    (rc.chunk_metadata ->> 'page_end')::int AS page_end,
                    ts_rank_cd(
                        setweight(to_tsvector('english', COALESCE(rc.chunk_metadata ->> 'path', '')), 'B') ||
                        setweight(to_tsvector('english', rc.content), 'A'),
                        search_query.query
                    ) AS score
                FROM "{self.schema_name}".reserved_chunks AS rc, search_query
                WHERE (
                    CAST(:scope_path AS TEXT) IS NULL
                    OR (
                        CAST(:include_descendants AS BOOLEAN) = TRUE
                        AND COALESCE(rc.chunk_metadata ->> 'path', '') LIKE CAST(:scope_prefix AS TEXT) ESCAPE '!'
                    )
                    OR (
                        CAST(:include_descendants AS BOOLEAN) = FALSE
                        AND COALESCE(rc.chunk_metadata ->> 'parent_path', '/') = CAST(:direct_parent_path AS TEXT)
                    )
                )
                  {file_filter}
                  AND (
                    setweight(to_tsvector('english', COALESCE(rc.chunk_metadata ->> 'path', '')), 'B') ||
                    setweight(to_tsvector('english', rc.content), 'A')
                  ) @@ search_query.query
                ORDER BY score DESC, rc.chunk_index ASC
                LIMIT :limit
            """)
            if visible_file_ids is not None:
                stmt = stmt.bindparams(
                    bindparam(
                        "visible_file_ids",
                        type_=ARRAY(PG_UUID(as_uuid=True)),
                    )
                )
            result = await session.execute(stmt, params)
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
