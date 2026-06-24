from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.sql import text

from app.core.config import settings
from app.modules.datastore.domain.file_entities import (
    DatastoreFileSearchResult,
    SearchMethod,
)
from app.modules.datastore.infrastructure.file_chunk_repository import (
    DatastoreFileChunkRepository,
)
from app.modules.datastore.infrastructure.session import (
    get_datastore_engine,
    get_datastore_session_maker,
)
from app.core.embeddings.embeddings import Embedder
from app.core.embeddings.factory import create_embedder
from app.modules.datastore.domain.ports import RerankerPort
from app.modules.datastore.infrastructure.reranker import create_reranker
import logging

logger = logging.getLogger(__name__)


class PostgresSearchService:
    def __init__(
        self,
        pod_id: UUID,
        *,
        engine=None,
        session_factory=None,
        embedder: Embedder | None = None,
        reranker: RerankerPort | None = None,
    ):
        self.pod_id = pod_id
        self.engine = engine or get_datastore_engine()
        self.session_factory = session_factory or get_datastore_session_maker()
        self.schema_name = f'pod_{str(pod_id).replace("-", "_")}'
        self.chunk_repo = DatastoreFileChunkRepository(
            self.session_factory,
            self.schema_name,
        )
        self.embedder = embedder or create_embedder()
        self.reranker = reranker or create_reranker()
        self._initialized = False

    async def ensure_schema(self):
        if self._initialized:
            return
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema_name}"'))
            await conn.execute(
                text(
                    f'''
                    CREATE TABLE IF NOT EXISTS "{self.schema_name}".reserved_chunks (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        chunk_index INTEGER NOT NULL,
                        file_id UUID NOT NULL,
                        content TEXT NOT NULL,
                        embedding vector({settings.embedding_dimension}) NOT NULL,
                        chunk_metadata JSONB,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                    '''
                )
            )
            await conn.execute(
                text(
                    f'CREATE INDEX IF NOT EXISTS ix_reserved_chunks_file_id ON "{self.schema_name}".reserved_chunks(file_id)'
                )
            )
            await conn.execute(
                text(
                    f'''
                    CREATE INDEX IF NOT EXISTS ix_reserved_chunks_path
                    ON "{self.schema_name}".reserved_chunks ((chunk_metadata ->> 'path'))
                    '''
                )
            )
            await conn.execute(
                text(
                    f'''
                    CREATE INDEX IF NOT EXISTS ix_reserved_chunks_parent_path
                    ON "{self.schema_name}".reserved_chunks ((chunk_metadata ->> 'parent_path'))
                    '''
                )
            )
            # text_pattern_ops so subtree filters (LIKE 'prefix/%') are index-backed
            # outside the C locale; the plain btree above only serves equality.
            await conn.execute(
                text(
                    f'''
                    CREATE INDEX IF NOT EXISTS ix_reserved_chunks_path_pattern
                    ON "{self.schema_name}".reserved_chunks
                    ((chunk_metadata ->> 'path') text_pattern_ops)
                    '''
                )
            )
            await conn.execute(
                text(
                    f'''
                    CREATE INDEX IF NOT EXISTS ix_reserved_chunks_text_search
                    ON "{self.schema_name}".reserved_chunks
                    USING GIN ((
                        setweight(to_tsvector('english', COALESCE(chunk_metadata ->> 'path', '')), 'B') ||
                        setweight(to_tsvector('english', content), 'A')
                    ))
                    '''
                )
            )
        await self._ensure_vector_index()
        self._initialized = True

    async def _ensure_vector_index(self):
        # HNSW over a half-precision (halfvec) cast of the embedding: ~half the
        # index memory of a full vector index with negligible recall loss. The
        # stored column stays vector(dim); only the index/query use halfvec.
        dim = settings.embedding_dimension
        # Retire the older full-precision hnsw / ivfflat indexes (lazy per-schema
        # migration — this runs on next access for each existing pod schema).
        for legacy in ("ix_reserved_chunks_embedding_hnsw", "ix_reserved_chunks_embedding_ivfflat"):
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(
                        text(f'DROP INDEX IF EXISTS "{self.schema_name}".{legacy}')
                    )
            except Exception as exc:
                logger.info("Could not drop legacy index %s for %s: %s", legacy, self.schema_name, exc)
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    text(
                        f'''
                        CREATE INDEX IF NOT EXISTS ix_reserved_chunks_embedding_halfvec
                        ON "{self.schema_name}".reserved_chunks
                        USING hnsw ((embedding::halfvec({dim})) halfvec_cosine_ops)
                        WITH (m = 16, ef_construction = 64)
                        '''
                    )
                )
        except Exception as exc:
            lower_msg = str(exc).lower()
            if "extension" in lower_msg and ("does not exist" in lower_msg or "not installed" in lower_msg):
                logger.info(
                    "Skipping halfvec vector index for %s: extension not available",
                    self.schema_name,
                )
            else:
                logger.warning(
                    "Failed to create halfvec vector index for %s; vector search "
                    "will use sequential scan: %s",
                    self.schema_name,
                    exc,
                )

    async def index_file_chunks(
        self,
        file_id: UUID,
        chunks: list[dict],
        metadata: dict | None = None,
    ) -> bool:
        await self.ensure_schema()
        await self.remove_file(file_id)

        if not chunks:
            logger.warning("No chunks for %s", file_id)
            return False

        try:
            texts = [c["text"] for c in chunks]
            embeddings = await self.embedder.embed_batch(texts)
            await self.chunk_repo.add_chunks(file_id, chunks, embeddings, metadata)
            return True
        except Exception as exc:
            logger.error("Failed to add file to search: %s", exc)
            raise

    async def remove_file(self, file_id: UUID):
        await self.ensure_schema()
        await self.chunk_repo.remove_chunks_by_file(file_id)

    async def update_file_path(self, file_id: UUID, path: str, parent_path: str | None):
        await self.ensure_schema()
        await self.chunk_repo.update_file_path(file_id, path, parent_path)

    async def search(
        self,
        query: str,
        limit: int = 10,
        method: SearchMethod = SearchMethod.HYBRID,
        scope_path: str | None = None,
        include_descendants: bool = True,
        visible_file_ids: set[UUID] | None = None,
    ) -> list[DatastoreFileSearchResult]:
        await self.ensure_schema()
        if visible_file_ids is not None and not visible_file_ids:
            return []

        rerank_active = settings.reranker_mode != "off"
        # First-stage candidate pool: over-retrieve when reranking so the
        # cross-encoder has material to reorder. Hybrid also over-fetches per
        # side for the RRF merge regardless.
        pool = max(limit, settings.reranker_retrieve_n) if rerank_active else limit

        if method == SearchMethod.TEXT:
            rows = await self.chunk_repo.text_search(
                query=query,
                pod_id=self.pod_id,
                limit=pool,
                scope_path=scope_path,
                include_descendants=include_descendants,
                visible_file_ids=visible_file_ids,
            )
            ranked = list(rows)
            diversify = False
        elif method == SearchMethod.VECTOR:
            emb = await self.embedder.embed(query)
            rows = await self.chunk_repo.vector_search(
                emb,
                pod_id=self.pod_id,
                limit=pool,
                scope_path=scope_path,
                include_descendants=include_descendants,
                visible_file_ids=visible_file_ids,
            )
            ranked = list(rows)
            diversify = False
        else:
            emb = await self.embedder.embed(query)
            per_side = max(limit * 3, pool)
            vector_results = await self.chunk_repo.vector_search(
                emb,
                pod_id=self.pod_id,
                limit=per_side,
                scope_path=scope_path,
                include_descendants=include_descendants,
                visible_file_ids=visible_file_ids,
            )
            text_results = await self.chunk_repo.text_search(
                query=query,
                pod_id=self.pod_id,
                limit=per_side,
                scope_path=scope_path,
                include_descendants=include_descendants,
                visible_file_ids=visible_file_ids,
            )
            ranked = self._merge_ranked_results(vector_results, text_results)
            diversify = True

        results = [DatastoreFileSearchResult(**row) for row in ranked]
        if rerank_active and results:
            results = await self.reranker.rerank(query, results, top_n=len(results))
        if diversify:
            return self._diversify_file_results(results, limit)
        return results[:limit]

    def _merge_ranked_results(
        self,
        vector_results: Iterable[dict],
        text_results: Iterable[dict],
    ) -> list[dict]:
        """RRF-fuse the two first-stage result sets into one ranked list (no
        per-file diversification or truncation — that happens after reranking)."""
        merged: dict[tuple[str, int], dict] = {}

        for source_results in (vector_results, text_results):
            for rank, item in enumerate(source_results, start=1):
                key = (str(item["file_id"]), item["chunk_index"])
                weighted_score = 1.0 / (60 + rank)
                if key not in merged:
                    merged[key] = {**item, "score": weighted_score}
                else:
                    merged[key]["score"] += weighted_score

        return sorted(
            merged.values(),
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )

    def _diversify_file_results(
        self,
        ranked: Iterable[DatastoreFileSearchResult],
        limit: int,
        *,
        max_chunks_per_file: int = 2,
    ) -> list[DatastoreFileSearchResult]:
        selected: list[DatastoreFileSearchResult] = []
        deferred: list[DatastoreFileSearchResult] = []
        counts_by_file: dict[str, int] = {}

        for item in ranked:
            file_key = str(item.file_id)
            if counts_by_file.get(file_key, 0) < max_chunks_per_file:
                selected.append(item)
                counts_by_file[file_key] = counts_by_file.get(file_key, 0) + 1
            else:
                deferred.append(item)
            if len(selected) >= limit:
                return selected[:limit]

        selected.extend(deferred)
        return selected[:limit]
