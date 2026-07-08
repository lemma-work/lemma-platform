from enum import Enum
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import BaseModel

from app.core.config import settings


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str
    score: Optional[float] = None


class AvailableSearchEngines(str, Enum):
    DUCKDUCKGO = "duckduckgo"
    SEARXNG = "searxng"
    BRAVE = "brave"


class BaseSearchClient:
    source: str

    def is_available(self) -> bool:
        return True

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        raise NotImplementedError


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        # Track div nesting so a result is finalized only when its own
        # ``result__body`` block closes, not at the first inner ``</div>``.
        self._depth = 0
        self._body_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        class_names = set(attr.get("class", "").split())
        if tag == "div":
            self._depth += 1
            if self._current is None and "result__body" in class_names:
                self._current = {"title": "", "url": "", "snippet": ""}
                self._body_depth = self._depth
                self._capture = None
            return
        if self._current is None:
            return
        if tag == "a" and "result__a" in class_names:
            self._current["url"] = self._normalize_url(attr.get("href", ""))
            self._capture = "title"
        elif "result__snippet" in class_names:
            self._capture = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            existing = self._current.get(self._capture, "")
            # Collapse whitespace so highlight (<b>) boundaries don't leave
            # double spaces in titles/snippets.
            self._current[self._capture] = " ".join(f"{existing} {data}".split())

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture in ("title", "snippet"):
            self._capture = None
        elif tag == "div":
            at_body_close = (
                self._current is not None
                and self._body_depth is not None
                and self._depth == self._body_depth
            )
            self._depth -= 1
            if at_body_close:
                if self._current and self._current.get("title") and self._current.get("url"):
                    self.results.append(self._current)
                self._current = None
                self._body_depth = None
                self._capture = None

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = unescape(url)
        parsed = urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
            uddg = parse_qs(parsed.query).get("uddg")
            if uddg:
                return unquote(uddg[0])
        return url


class DuckDuckGoSearchClient(BaseSearchClient):
    source = "duckduckgo"

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": "lemma-local-web-search/1.0"},
            )
        response.raise_for_status()

        parser = DuckDuckGoHTMLParser()
        parser.feed(response.text)
        return [
            SearchResult(
                title=item["title"],
                url=item["url"],
                snippet=item.get("snippet", ""),
                source=self.source,
            )
            for item in parser.results[:max_results]
        ]


class SearXNGSearchClient(BaseSearchClient):
    source = "searxng"

    def __init__(self) -> None:
        self.base_url = (settings.searxng_url or "").strip().rstrip("/")

    def is_available(self) -> bool:
        return bool(self.base_url)

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if not self.base_url:
            raise ValueError("SEARXNG_URL is not set")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "pageno": 1},
                headers={"Accept": "application/json"},
            )
        response.raise_for_status()
        raw_results = response.json().get("results", [])
        sorted_results = sorted(
            raw_results,
            key=lambda item: float(item.get("score", 0) or 0),
            reverse=True,
        )
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
                source=self.source,
                score=float(item["score"]) if item.get("score") is not None else None,
            )
            for item in sorted_results[:max_results]
        ]


class BraveSearchClient(BaseSearchClient):
    source = "brave"

    def __init__(self) -> None:
        self.api_key = (settings.brave_search_api_key or "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if not self.api_key:
            raise ValueError("BRAVE_SEARCH_API_KEY is not set")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max(1, min(max_results, 20))},
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                },
            )
        response.raise_for_status()
        raw_results = (response.json().get("web") or {}).get("results", [])
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", "")),
                source=self.source,
            )
            for item in raw_results[:max_results]
        ]


class SearchClient:
    def __init__(self, search_engine: AvailableSearchEngines | None = None):
        self.search_engine = self._get_client(search_engine)

    def _get_client(
        self, engine: AvailableSearchEngines | None
    ) -> BaseSearchClient:
        clients: dict[AvailableSearchEngines, type[BaseSearchClient]] = {
            AvailableSearchEngines.SEARXNG: SearXNGSearchClient,
            AvailableSearchEngines.BRAVE: BraveSearchClient,
            AvailableSearchEngines.DUCKDUCKGO: DuckDuckGoSearchClient,
        }
        if engine is not None:
            return clients[engine]()
        configured_provider = settings.web_search_provider.strip().lower()
        if configured_provider != "auto":
            return clients[AvailableSearchEngines(configured_provider)]()
        for candidate in (
            AvailableSearchEngines.SEARXNG,
            AvailableSearchEngines.BRAVE,
            AvailableSearchEngines.DUCKDUCKGO,
        ):
            client = clients[candidate]()
            if client.is_available():
                return client
        return DuckDuckGoSearchClient()

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        if not query:
            raise ValueError("Search query cannot be empty")
        return await self.search_engine.search(query, max_results)
