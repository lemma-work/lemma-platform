from __future__ import annotations

import os

import pytest

from app.core.config import settings
from app.core.web_search.web_search import WebSearchRequest, search_web

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.provider,
    pytest.mark.skipif(
        os.getenv("LEMMA_RUN_PROVIDER_E2E") != "1",
        reason="Set LEMMA_RUN_PROVIDER_E2E=1 to run live web-search provider e2e.",
    ),
]


@pytest.mark.asyncio
async def test_duckduckgo_web_search_works_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "duckduckgo")
    monkeypatch.setattr(settings, "searxng_url", None)
    monkeypatch.setattr(settings, "brave_search_api_key", None)

    response = await search_web(
        WebSearchRequest(query="OpenAI API documentation", max_results=3)
    )

    assert response.success is True
    assert response.results
    assert all(result.source == "duckduckgo" for result in response.results)
    assert all(result.url for result in response.results)
    # DuckDuckGo HTML results carry a snippet; the parser must capture it
    # even though the snippet anchor follows the url/extras block.
    assert all(result.snippet.strip() for result in response.results)
