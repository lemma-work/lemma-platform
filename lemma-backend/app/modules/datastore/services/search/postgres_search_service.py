from __future__ import annotations

from collections.abc import Iterable
import time
from uuid import UUID

from sqlalchemy.sql import text

from app.core.config import settings
from app.modules.datastore.domain.file_entities import (
    DatastoreFileSearchResult,
    SearchMethod,
)
from app.modules.datastore.domain.document_processing import IndexingMetrics
from app.modules.datastore.infrastructure.file_chunk_repository import (
    DatastoreFileChunkRepository,
)
from app.modules.datastore.infrastructure.session import (
    get_datastore_engine,
    get_datastore_session_maker,
)
from app.core.embeddings.embeddings import Embedder
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
        embedder: Embedder,
        reranker: RerankerPort | None = None,
    ):
        self.pod_id = pod_id
        self.engine = engine or get_datastore_engine()
        self.session_factory = session_factory or get_datastore_session_maker()
        self.schema_name = f"pod_{str(pod_id).replace('-', '_')}"
        self.chunk_repo = DatastoreFileChunkRepository(
            self.session_factory,
            self.schema_name,
        )
        self.embedder = embedder
        self.reranker = reranker or create_reranker()
        self._initialized = False

    # Serializes concurrent ensure_schema() calls. CREATE EXTENSION/TYPE ... IF
    # NOT EXISTS is NOT atomic in Postgres: parallel indexers (e.g. the worker
    # and an in-process indexer, or two pods on a fresh DB) racing the global
    # pg_extension / pg_type catalogs hit "duplicate key" unique violations. A
    # transaction-scoped advisory lock makes the idempotent setup race-free.
    _ENSURE_SCHEMA_LOCK_KEY = 0x6C656D6D61  # "lemma"

    async def ensure_schema(self):
        if self._initialized:
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": self._ENSURE_SCHEMA_LOCK_KEY},
            )
            # Azure Database for PostgreSQL checks CREATE EXTENSION privileges
            # even when IF NOT EXISTS would otherwise be a no-op. The runtime
            # datastore role is intentionally not an azure_pg_admin member, so
            # first check the database-scoped catalog and only attempt creation
            # for self-hosted installations where the extension is absent.
            vector_installed = await conn.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                    ")"
                )
            )
            if not vector_installed:
                await conn.execute(text("CREATE EXTENSION vector"))
            await conn.execute(
                text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema_name}"')
            )
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
        for legacy in (
            "ix_reserved_chunks_embedding_hnsw",
            "ix_reserved_chunks_embedding_ivfflat",
        ):
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(
                        text(f'DROP INDEX IF EXISTS "{self.schema_name}".{legacy}')
                    )
            except Exception as exc:
                logger.info(
                    "Could not drop legacy index %s for %s: %s",
                    legacy,
                    self.schema_name,
                    exc,
                )
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
            if "extension" in lower_msg and (
                "does not exist" in lower_msg or "not installed" in lower_msg
            ):
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
    ) -> IndexingMetrics:
        schema_started = time.perf_counter()
        await self.ensure_schema()
        schema_seconds = time.perf_counter() - schema_started

        if not chunks:
            logger.warning("No chunks for %s", file_id)
            return IndexingMetrics(
                chunk_count=0,
                schema_seconds=schema_seconds,
                embedding_seconds=0.0,
                persistence_seconds=0.0,
            )

        try:
            texts = [c["text"] for c in chunks]
            embedding_started = time.perf_counter()
            embeddings = await self.embedder.embed_batch(texts)
            embedding_seconds = time.perf_counter() - embedding_started
            if len(embeddings) != len(chunks):
                raise ValueError(
                    f"Embedding provider returned {len(embeddings)} vectors for "
                    f"{len(chunks)} chunks"
                )
            # add_chunks replaces the prior revision in one transaction. The old
            # searchable revision remains intact if embedding generation fails.
            persistence_started = time.perf_counter()
            await self.chunk_repo.add_chunks(file_id, chunks, embeddings, metadata)
            persistence_seconds = time.perf_counter() - persistence_started
            metrics = IndexingMetrics(
                chunk_count=len(chunks),
                schema_seconds=schema_seconds,
                embedding_seconds=embedding_seconds,
                persistence_seconds=persistence_seconds,
            )
            logger.info(
                "Datastore indexing stages file=%s chunks=%d schema=%.3fs "
                "embed=%.3fs persist=%.3fs throughput=%.2f_chunks_per_second",
                file_id,
                len(chunks),
                schema_seconds,
                embedding_seconds,
                persistence_seconds,
                len(chunks) / max(embedding_seconds, 0.001),
            )
            return metrics
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
