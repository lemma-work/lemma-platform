from __future__ import annotations

from collections.abc import Iterable, Sequence
import time
from uuid import UUID


from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.domain.file_entities import (
    DatastoreFileSearchResult,
    SearchMethod,
)
from app.modules.datastore.domain.document_processing import IndexingMetrics
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    SEARCH_FILES_VERSION,
    migrate_pod_schema,
    pod_schema_version,
)
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
from app.core.log.log import get_logger

logger = get_logger(__name__)


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
        self._last_embedded: tuple[str, list[float]] | None = None

    async def ensure_schema(self) -> int:
        """Bring this pod's search tables to the latest version, creating them.

        The write path's entry: indexing a file needs the tables, so it
        migrates inline (once per process per schema; see
        ``pod_schema_migrations``). Readers never call this.
        """
        return await migrate_pod_schema(self.engine, self.schema_name, create=True)

    async def schema_version(self) -> int:
        """The shape this pod's search tables are in now, without changing it."""
        return await pod_schema_version(self.engine, self.schema_name)

    async def index_file_chunks(
        self,
        file_id: UUID,
        chunks: list[dict],
        metadata: dict | None = None,
        *,
        sha256: str | None = None,
    ) -> IndexingMetrics:
        schema_started = time.perf_counter()
        version = await self.ensure_schema()
        schema_seconds = time.perf_counter() - schema_started

        if not chunks:
            logger.debug(
                "datastore.postgres_search_service.no_chunks_s.diagnostic",
                file_id=file_id,
            )
            # Still replace: a new revision with no text must not leave the
            # previous revision's chunks answering for it.
            await self.chunk_repo.add_chunks(
                file_id, [], [], metadata, version=version, sha256=sha256
            )
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
            await self.chunk_repo.add_chunks(
                file_id, chunks, embeddings, metadata, version=version, sha256=sha256
            )
            persistence_seconds = time.perf_counter() - persistence_started
            metrics = IndexingMetrics(
                chunk_count=len(chunks),
                schema_seconds=schema_seconds,
                embedding_seconds=embedding_seconds,
                persistence_seconds=persistence_seconds,
            )
            logger.debug(
                "datastore.postgres_search_service.datastore_indexing_stages_file_s.observed",
                file_id=file_id,
                count=len(chunks),
                schema_seconds=schema_seconds,
                embedding_seconds=embedding_seconds,
                persistence_seconds=persistence_seconds,
            )
            return metrics
        except Exception:
            logger.debug(
                "datastore.postgres_search_service.add_file_search_s.propagated",
                exc_info=True,
            )
            raise

    async def remove_file(self, file_id: UUID):
        version = await self.schema_version()
        if version == 0:
            return
        await self.chunk_repo.remove_chunks_by_file(file_id, version=version)

    async def remove_files(self, file_ids: Sequence[UUID]) -> None:
        """The batch spelling, for a folder delete; see `remove_chunks_by_files`."""
        version = await self.schema_version()
        if version == 0:
            return
        await self.chunk_repo.remove_chunks_by_files(file_ids, version=version)

    async def update_file_path(self, file_id: UUID, path: str, parent_path: str | None):
        version = await self.schema_version()
        if version == 0:
            return
        await self.chunk_repo.update_file_path(
            file_id, path, parent_path, version=version
        )

    async def rename_folder(self, previous_path: str, new_path: str) -> bool:
        """Tell the index a folder moved, in one statement; False if it cannot.

        Version 2 keeps paths in one row per file, so this is one update. A
        schema still on version 1 keeps them in every chunk, and the caller
        repoints each file instead.
        """
        version = await self.schema_version()
        if version == 0:
            return True
        if version < SEARCH_FILES_VERSION:
            return False
        await self.chunk_repo.rename_prefix(previous_path, new_path)
        return True

    async def _embed(self, query: str) -> list[float]:
        """Embed the query, remembering the last one.

        A search can run twice for the same query: when the readable set is too
        large to enumerate up front and post-filtering the candidate pool comes
        up short, the caller widens the scope and asks again. The provider
        round trip is the slowest part of a vector or hybrid search -- measured
        at a 4.3s median in production -- and paying it twice for the same
        string is pure waste.

        One entry, keyed by the text, because the second pass is the only
        repeat there is. Keyed rather than counted so it cannot answer with
        another query's vector, which is the failure a bare "cache the last
        result" would have.
        """
        if self._last_embedded is not None and self._last_embedded[0] == query:
            return self._last_embedded[1]
        embedding = await self.embedder.embed(query)
        self._last_embedded = (query, embedding)
        return embedding

    async def search(
        self,
        query: str,
        limit: int = 10,
        method: SearchMethod = SearchMethod.HYBRID,
        scope_path: str | None = None,
        include_descendants: bool = True,
        *,
        file_scope: SearchFileScope,
    ) -> list[DatastoreFileSearchResult]:
        if file_scope.matches_nothing:
            return []
        # No DDL here: a pod whose tables do not exist yet has nothing indexed,
        # and one still on the previous version is read in that shape.
        version = await self.schema_version()
        if version == 0:
            return []

        rerank_active = datastore_settings.reranker_mode != "off"
        # First-stage candidate pool: over-retrieve when reranking so the
        # cross-encoder has material to reorder. Hybrid also over-fetches per
        # side for the RRF merge regardless.
        pool = (
            max(limit, datastore_settings.reranker_retrieve_n)
            if rerank_active
            else limit
        )

        if method == SearchMethod.TEXT:
            rows = await self.chunk_repo.text_search(
                query=query,
                pod_id=self.pod_id,
                limit=pool,
                scope_path=scope_path,
                include_descendants=include_descendants,
                file_scope=file_scope,
                version=version,
            )
            ranked = list(rows)
            diversify = False
        elif method == SearchMethod.VECTOR:
            emb = await self._embed(query)
            rows = await self.chunk_repo.vector_search(
                emb,
                pod_id=self.pod_id,
                limit=pool,
                scope_path=scope_path,
                include_descendants=include_descendants,
                file_scope=file_scope,
                version=version,
            )
            ranked = list(rows)
            diversify = False
        else:
            emb = await self._embed(query)
            per_side = max(limit * 3, pool)
            vector_results = await self.chunk_repo.vector_search(
                emb,
                pod_id=self.pod_id,
                limit=per_side,
                scope_path=scope_path,
                include_descendants=include_descendants,
                file_scope=file_scope,
                version=version,
            )
            text_results = await self.chunk_repo.text_search(
                query=query,
                pod_id=self.pod_id,
                limit=per_side,
                scope_path=scope_path,
                include_descendants=include_descendants,
                file_scope=file_scope,
                version=version,
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
