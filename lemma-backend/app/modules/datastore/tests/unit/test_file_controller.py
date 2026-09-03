from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.datastore.api.controllers.file_controller import (
    build_content_disposition,
    search_files,
)
from app.modules.datastore.api.schemas.datastore_schemas import (
    FileSearchRequest,
    FileSearchResponse,
)
from app.modules.datastore.domain.file_entities import DatastoreFileSearchResult


def test_build_content_disposition_supports_unicode_filename() -> None:
    filename = "Screenshot 2026-04-06 at 1.01.14\u202fPM.png"

    header = build_content_disposition("inline", filename)

    assert header == (
        'inline; filename="Screenshot 2026-04-06 at 1.01.14 PM.png"; '
        "filename*=UTF-8''Screenshot%202026-04-06%20at%201.01.14%E2%80%AFPM.png"
    )
    header.encode("latin-1")


class TestASearchSaysWhenItWasCutShort:
    """`total` was `len(results)` on an already-truncated list, so a caller --
    frequently an agent -- could not tell a complete result from a capped one
    and reported the page length as the number of matches in the pod. The
    ad-hoc query endpoint answers the same question with `truncated`.
    """

    def _service(self, matches: int):
        async def _search_files(**_kwargs):
            return [
                DatastoreFileSearchResult(
                    file_id=uuid4(),
                    path=f"/notes/{index}.md",
                    chunk_index=0,
                    content=f"match {index}",
                    score=1.0,
                )
                for index in range(matches)
            ]

        return SimpleNamespace(search_files=_search_files)

    async def _search(self, *, matches: int, limit: int) -> FileSearchResponse:
        return await search_files(
            pod_id=uuid4(),
            data=FileSearchRequest(query="quarterly plan", limit=limit),
            file_service=self._service(matches),
            ctx=SimpleNamespace(user_id=uuid4()),
        )

    @pytest.mark.asyncio
    async def test_a_full_page_is_reported_as_cut_short(self) -> None:
        response = await self._search(matches=10, limit=10)

        assert response.total == 10
        assert response.truncated is True

    @pytest.mark.asyncio
    async def test_a_short_page_is_reported_as_complete(self) -> None:
        response = await self._search(matches=3, limit=10)

        assert response.total == 3
        assert response.truncated is False

    @pytest.mark.asyncio
    async def test_no_matches_is_not_a_truncation(self) -> None:
        response = await self._search(matches=0, limit=10)

        assert response.total == 0
        assert response.truncated is False
